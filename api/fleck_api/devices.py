"""Pairing a desktop app to a user (spec 7.6): a short-lived code from the signed-in web app, traded for a device token."""
import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from fleck_api.auth import _hash, current_user
from fleck_api.db import get_session
from fleck_api.models import Device, PairingCode, User

CODE_MINUTES = 10
router = APIRouter(prefix="/api/devices")


def device_from_header(s: Session, authorization: str | None) -> Device | None:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    d = s.scalar(select(Device).where(Device.token_hash == _hash(token)))
    if d:
        d.last_seen = datetime.now(timezone.utc)
        s.commit()
    return d


def current_device(authorization: str | None = Header(None), s: Session = Depends(get_session)) -> Device:
    if d := device_from_header(s, authorization):
        return d
    raise HTTPException(401, "unknown device")


def user_or_device(authorization: str | None = Header(None), fleck_session: str | None = Cookie(None),
                   s: Session = Depends(get_session)) -> User:
    """The owner of the device token, else the signed-in user (cookie)."""
    if authorization:
        return s.get(User, current_device(authorization, s).user_id)
    return current_user(fleck_session, s)


class PairIn(BaseModel):
    code: str
    name: str | None = None


@router.post("/pairing-code")
def pairing_code(u: User = Depends(current_user), s: Session = Depends(get_session)):
    code = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4)) + "-" + "".join(secrets.choice(string.digits) for _ in range(4))
    s.add(PairingCode(user_id=u.id, code=code, expires_at=datetime.now(timezone.utc) + timedelta(minutes=CODE_MINUTES)))
    s.commit()
    return {"code": code}


@router.post("/pair")
def pair(body: PairIn, s: Session = Depends(get_session)):
    now = datetime.now(timezone.utc)
    # one UPDATE claims the code, so two helpers racing for it cannot both win
    user_id = s.scalar(update(PairingCode).where(PairingCode.code == body.code.strip().upper(), PairingCode.used_at.is_(None),
                                                 PairingCode.expires_at > now).values(used_at=now).returning(PairingCode.user_id))
    if user_id is None:
        raise HTTPException(400, "That code is wrong or has expired. Get a new one in Settings.")
    token = secrets.token_urlsafe(32)
    s.add(Device(user_id=user_id, name=body.name, token_hash=_hash(token)))
    s.commit()
    return {"device_token": token}


@router.get("")
def devices(u: User = Depends(current_user), s: Session = Depends(get_session)):
    from fleck_api.realtime import hub
    online = hub.connected_device_ids()
    return [{"id": d.id, "name": d.name, "last_seen": d.last_seen, "connected": d.id in online}
            for d in s.scalars(select(Device).where(Device.user_id == u.id).order_by(Device.id))]
