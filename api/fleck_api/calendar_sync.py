"""Google Calendar sync (read-only), the events REST routes and the Today page's devices pill."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fleck_api import auth
from fleck_api.auth import current_user
from fleck_api.config import setting
from fleck_api.db import get_session
from fleck_api.jobs import enqueue, job
from fleck_api.models import CalendarEvent, Client, Device, GoogleToken, Job, User

log = logging.getLogger("fleck.calendar")
LINK = re.compile(r"https://[\w.-]*(?:zoom\.us|teams\.microsoft\.com|teams\.live\.com|webex\.com|meet\.google\.com)/[^\s\"'<>]*")


def _now() -> datetime:  # tests pin the clock
    return datetime.now(timezone.utc)


def meeting_link(ev: dict) -> str | None:
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for ep in (ev.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    m = LINK.search(f"{ev.get('location') or ''} {ev.get('description') or ''}")
    return m.group(0).rstrip(".,;:)") if m else None


class RefreshFailed(Exception):
    pass


def _access_token(s: Session, user_id: int) -> str | None:
    row = s.scalar(select(GoogleToken).where(GoogleToken.user_id == user_id))
    if row is None:
        return None
    try:
        blob = json.loads(auth._fernet().decrypt(row.token_enc.encode()))
    except InvalidToken:
        raise RefreshFailed("stored tokens unreadable")
    if datetime.fromisoformat(blob["expires_at"]) > _now() + timedelta(minutes=1):
        return blob["access_token"]
    if not blob.get("refresh_token"):
        raise RefreshFailed("no refresh token")
    try:
        with auth.http_client() as c:
            tok = c.post(auth._urls()["token"], data={
                "grant_type": "refresh_token", "refresh_token": blob["refresh_token"],
                "client_id": setting("GOOGLE_CLIENT_ID", "gsim-client"),
                "client_secret": setting("GOOGLE_CLIENT_SECRET", "gsim-secret")}).raise_for_status().json()
        blob |= {"access_token": tok["access_token"],
                 "expires_at": (_now() + timedelta(seconds=tok.get("expires_in", 3600))).isoformat()}
    except Exception as e:  # any refresh failure skips the user; never log the response: it may carry tokens
        raise RefreshFailed(type(e).__name__)
    row.token_enc = auth._fernet().encrypt(json.dumps(blob).encode()).decode()
    s.commit()
    return blob["access_token"]


def _fetch(token: str, lo: datetime, hi: datetime) -> list[dict]:
    items, page = [], None
    with auth.http_client() as c:
        while True:
            params = {"timeMin": lo.isoformat(), "timeMax": hi.isoformat(), "singleEvents": "true",
                      "orderBy": "startTime", "maxResults": "250", **({"pageToken": page} if page else {})}
            body = c.get(auth._urls()["calendar"], params=params,
                         headers={"Authorization": f"Bearer {token}"}).raise_for_status().json()
            items += body.get("items", [])
            if not (page := body.get("nextPageToken")):
                return items


def _keep(ev: dict) -> bool:
    if ev.get("status") == "cancelled" or "dateTime" not in ev.get("start", {}):  # all-day events have only "date"
        return False
    return not any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees", []))


def sync_user(s: Session, user_id: int) -> None:
    try:
        token = _access_token(s, user_id)
    except RefreshFailed as e:  # revoked or broken grant: skip this user until they sign in again
        log.warning("calendar sync for user %s skipped: token refresh failed (%s)", user_id, e)
        return
    if token is None:
        return
    lo, hi = _now() - timedelta(hours=12), _now() + timedelta(days=7)
    rows = [{"user_id": user_id, "google_event_id": ev["id"], "title": ev.get("summary") or "(no title)",
             "description": ev.get("description"), "start_at": datetime.fromisoformat(ev["start"]["dateTime"]),
             "end_at": datetime.fromisoformat(ev["end"]["dateTime"]), "meeting_link": meeting_link(ev),
             "attendees": [{"email": a.get("email"), "name": a.get("displayName"), "self": bool(a.get("self"))}
                           for a in ev.get("attendees", [])]}
            for ev in _fetch(token, lo, hi) if _keep(ev)]
    if rows:
        stmt = insert(CalendarEvent).values(rows)
        s.execute(stmt.on_conflict_do_update(  # client_id is the user's, never overwritten
            index_elements=["user_id", "google_event_id"],
            set_={k: stmt.excluded[k] for k in ("title", "description", "start_at", "end_at", "meeting_link", "attendees")}))
    # Gone from Google (deleted, declined since): drop it. Only rows that start inside the window we just read.
    s.execute(delete(CalendarEvent).where(CalendarEvent.user_id == user_id, CalendarEvent.start_at >= lo,
                                          CalendarEvent.start_at < hi,
                                          CalendarEvent.google_event_id.not_in([r["google_event_id"] for r in rows])))
    s.commit()


@job("sync_calendar")
def sync_calendar(session: Session, payload: dict) -> None:
    sync_user(session, payload["user_id"])


SCHEDULE_S = 300


def schedule_syncs(s: Session) -> int:
    """Enqueue sync_calendar for every user with tokens unless one is already queued or running for them."""
    pending = exists().where(Job.kind == "sync_calendar", Job.status.in_(("queued", "running")),
                             Job.payload["user_id"].as_integer() == GoogleToken.user_id)
    users = s.scalars(select(GoogleToken.user_id).where(~pending)).all()
    for uid in users:
        enqueue(s, "sync_calendar", {"user_id": uid})
    return len(users)


# ---------- routes (plain def: sync DB work runs in FastAPI's threadpool) ----------

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])


def _row(s: Session, e: CalendarEvent) -> dict:
    c = s.get(Client, e.client_id) if e.client_id else None
    return {"id": e.id, "title": e.title, "description": e.description, "start_at": e.start_at, "end_at": e.end_at,
            "attendees": e.attendees, "meeting_link": e.meeting_link,
            "client": {"id": c.id, "name": c.name} if c else None}


@router.get("/events")
def list_events(range: Literal["today", "upcoming"] = "today", tz: str | None = None,
                u: User = Depends(current_user), s: Session = Depends(get_session)):
    try:
        zone = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "unknown time zone")
    midnight = _now().astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    lo, hi = (midnight, midnight + timedelta(days=1)) if range == "today" else (midnight + timedelta(days=1), None)
    q = select(CalendarEvent).where(CalendarEvent.user_id == u.id, CalendarEvent.start_at >= lo)
    if hi:
        q = q.where(CalendarEvent.start_at < hi)
    return [_row(s, e) for e in s.scalars(q.order_by(CalendarEvent.start_at))]


def _event(s: Session, u: User, event_id: int) -> CalendarEvent:
    e = s.get(CalendarEvent, event_id)
    if e is None or e.user_id != u.id:
        raise HTTPException(404, "event not found")
    return e


@router.get("/events/{event_id}")
def get_event(event_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    return _row(s, _event(s, u, event_id))


class EventClient(BaseModel):
    client_id: int | None


@router.put("/events/{event_id}/client")
def set_event_client(event_id: int, body: EventClient, u: User = Depends(current_user), s: Session = Depends(get_session)):
    e = _event(s, u, event_id)
    if body.client_id is not None and s.get(Client, body.client_id) is None:
        raise HTTPException(404, "client not found")
    e.client_id = body.client_id
    s.commit()
    return _row(s, e)


# shortcut: list-only devices endpoint for the Today pill; Task 15 (pairing) moves it to devices.py and extends it.
@router.get("/devices")
def list_devices(u: User = Depends(current_user), s: Session = Depends(get_session)):
    return [{"id": d.id, "name": d.name, "last_seen": d.last_seen}
            for d in s.scalars(select(Device).where(Device.user_id == u.id).order_by(Device.id))]
