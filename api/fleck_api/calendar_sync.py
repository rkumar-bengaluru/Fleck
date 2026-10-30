"""Google Calendar sync (read-only), the events REST routes and the Today page's devices pill."""
import html
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fleck_api import auth
from fleck_api.auth import current_user
from fleck_api.config import setting
from fleck_api.db import get_session
from fleck_api.jobs import enqueue_once, job
from fleck_api.models import Brief, CalendarEvent, Client, Device, GoogleToken, Job, Meeting, User

log = logging.getLogger("fleck.calendar")
_HOST_END = r"(?![\w-]|\.[\w-])"  # zoom.us, not zoom.us.evil.test
LINK = re.compile(r"https?://(?:[\w-]+\.)*(?:zoom\.us|teams\.microsoft\.com|teams\.live\.com|webex\.com|meet\.google\.com)"
                  + _HOST_END + r"[^\s\"'<>]*", re.I)
SAFELINK = re.compile(r"https?://(?:[\w-]+\.)*safelinks\.protection\.outlook\.com" + _HOST_END + r"[^\s\"'<>]*", re.I)
TRAILING = ".,;:!?)]}>'\""


def _now() -> datetime:  # tests pin the clock
    return datetime.now(timezone.utc)


def _web(url: str | None) -> str | None:
    return url if url and re.match(r"https?://", url, re.I) else None


def _unwrap_safelink(m: re.Match) -> str:
    url = parse_qs(urlparse(m.group(0)).query).get("url")
    return url[0] if url else m.group(0)


def meeting_link(ev: dict) -> str | None:
    if _web(ev.get("hangoutLink")):
        return ev["hangoutLink"]
    for ep in (ev.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video" and _web(ep.get("uri")):
            return ep["uri"]
    text = html.unescape(f"{ev.get('location') or ''} {ev.get('description') or ''}")
    m = LINK.search(SAFELINK.sub(_unwrap_safelink, text))
    return m.group(0).rstrip(TRAILING) if m else None


class RefreshFailed(Exception):
    pass


class TokensRevoked(Exception):  # Google said 401 (or invalid_grant): the grant is dead until the user signs in again
    pass


def _status(e: Exception) -> int | None:
    return getattr(getattr(e, "response", None), "status_code", None)


def _client(timeout: float | None):
    c = auth.http_client()
    if timeout is not None:
        c.timeout = timeout
    return c


def _access_token(s: Session, user_id: int, timeout: float | None = None) -> str | None:
    row = s.scalar(select(GoogleToken).where(GoogleToken.user_id == user_id))
    if row is None:
        return None
    try:
        blob = json.loads(auth._fernet().decrypt(row.token_enc.encode()))
    except InvalidToken:  # key rotated or row corrupt: as good as revoked
        raise TokensRevoked("stored tokens unreadable")
    if datetime.fromisoformat(blob["expires_at"]) > _now() + timedelta(minutes=1):
        return blob["access_token"]
    if not blob.get("refresh_token"):
        raise RefreshFailed("no refresh token")
    try:
        with _client(timeout) as c:
            tok = c.post(auth._urls()["token"], data={
                "grant_type": "refresh_token", "refresh_token": blob["refresh_token"],
                "client_id": setting("GOOGLE_CLIENT_ID", "gsim-client"),
                "client_secret": setting("GOOGLE_CLIENT_SECRET", "gsim-secret")}).raise_for_status().json()
        blob |= {"access_token": tok["access_token"],
                 "expires_at": (_now() + timedelta(seconds=tok.get("expires_in", 3600))).isoformat()}
    except Exception as e:  # any refresh failure skips the user; never log the response: it may carry tokens
        if _status(e) in (400, 401):
            raise TokensRevoked(f"refresh {_status(e)}")
        raise RefreshFailed(type(e).__name__)
    row.token_enc = auth._fernet().encrypt(json.dumps(blob).encode()).decode()
    s.commit()
    return blob["access_token"]


def _fetch(token: str, lo: datetime, hi: datetime, timeout: float | None = None) -> list[dict]:
    items, page = [], None
    with _client(timeout) as c:
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


def sync_user(s: Session, user_id: int, timeout: float | None = None) -> None:
    try:
        _sync(s, user_id, timeout)
    except TokensRevoked as e:  # forget the dead grant: no retries, the scheduler stops until the next sign-in
        s.rollback()
        s.execute(delete(GoogleToken).where(GoogleToken.user_id == user_id))
        s.commit()
        log.warning("calendar sync for user %s skipped: Google refused the tokens (%s); they were removed", user_id, e)


def _sync(s: Session, user_id: int, timeout: float | None) -> None:
    try:
        token = _access_token(s, user_id, timeout)
    except RefreshFailed as e:  # revoked or broken grant: skip this user until they sign in again
        log.warning("calendar sync for user %s skipped: token refresh failed (%s)", user_id, e)
        return
    if token is None:
        return
    lo, hi = _now() - timedelta(hours=12), _now() + timedelta(days=7)
    try:
        items = _fetch(token, lo, hi, timeout)
    except Exception as e:
        if _status(e) != 401:
            raise  # transient: the job retries
        raise TokensRevoked("calendar 401")
    rows = [{"user_id": user_id, "google_event_id": ev["id"], "title": ev.get("summary") or "(no title)",
             "description": ev.get("description"), "start_at": datetime.fromisoformat(ev["start"]["dateTime"]),
             "end_at": datetime.fromisoformat(ev["end"]["dateTime"]), "meeting_link": meeting_link(ev),
             "recurring_event_id": ev.get("recurringEventId"), "cancelled": False,
             "attendees": [{"email": a.get("email"), "name": a.get("displayName"), "self": bool(a.get("self"))}
                           for a in ev.get("attendees", [])]}
            for ev in items if _keep(ev)]
    if rows:
        stmt = insert(CalendarEvent).values(rows)
        s.execute(stmt.on_conflict_do_update(  # client_id is the user's, never overwritten
            index_elements=["user_id", "google_event_id"],
            set_={k: stmt.excluded[k] for k in ("title", "description", "start_at", "end_at", "meeting_link", "attendees",
                                                 "recurring_event_id", "cancelled")}))
    # Gone from Google (deleted, declined since), judged only inside the window just read: an event someone built on
    # (client link, brief, meeting) is kept and marked cancelled; a bare one is deleted.
    gone = select(CalendarEvent.id).where(
        CalendarEvent.user_id == user_id, CalendarEvent.start_at >= lo, CalendarEvent.start_at < hi,
        CalendarEvent.google_event_id.not_in([r["google_event_id"] for r in rows]))
    used = (CalendarEvent.client_id.is_not(None) | exists().where(Brief.event_id == CalendarEvent.id)
            | exists().where(Meeting.event_id == CalendarEvent.id))
    s.execute(update(CalendarEvent).where(CalendarEvent.id.in_(gone), used).values(cancelled=True))
    s.execute(delete(CalendarEvent).where(CalendarEvent.id.in_(gone), ~used))
    s.commit()


@job("sync_calendar")
def sync_calendar(session: Session, payload: dict) -> None:
    sync_user(session, payload["user_id"])


SCHEDULE_S = 300


def schedule_syncs(s: Session) -> int:
    """Enqueue sync_calendar for every user with tokens unless one is already queued or running for them."""
    return sum(enqueue_once(s, "sync_calendar", {"user_id": uid}) is not None
               for uid in s.scalars(select(GoogleToken.user_id)).all())


# ---------- routes (plain def: sync DB work runs in FastAPI's threadpool) ----------

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])


def _row(s: Session, e: CalendarEvent) -> dict:
    c = s.get(Client, e.client_id) if e.client_id else None
    return {"id": e.id, "title": e.title, "description": e.description, "start_at": e.start_at, "end_at": e.end_at,
            "attendees": e.attendees, "meeting_link": e.meeting_link, "cancelled": e.cancelled,
            "client": {"id": c.id, "name": c.name} if c else None}


def _repeats(s: Session, e: CalendarEvent) -> str | None:
    """'daily' or 'weekly' from the gap between the series' first two stored occurrences."""
    starts = s.scalars(select(CalendarEvent.start_at).where(
        CalendarEvent.user_id == e.user_id, CalendarEvent.recurring_event_id == e.recurring_event_id)
        .order_by(CalendarEvent.start_at).limit(2)).all()
    gap = starts[1] - starts[0] if len(starts) == 2 else None
    return {timedelta(days=1): "daily", timedelta(days=7): "weekly"}.get(gap)


@router.get("/events")
def list_events(range: Literal["today", "upcoming"] = "today", tz: str | None = None,
                u: User = Depends(current_user), s: Session = Depends(get_session)):
    try:
        zone = ZoneInfo(tz or setting("TZ", "")) if tz or setting("TZ", "") else datetime.now().astimezone().tzinfo
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "unknown time zone")
    day = _now().astimezone(zone).date()  # midnights by date arithmetic: a DST-change day is 23 or 25 hours long
    midnight, next_midnight = (datetime.combine(d, time(0), tzinfo=zone) for d in (day, day + timedelta(days=1)))
    lo, hi = (midnight, next_midnight) if range == "today" else (next_midnight, None)
    q = select(CalendarEvent).where(CalendarEvent.user_id == u.id, CalendarEvent.start_at >= lo)
    if hi:
        q = q.where(CalendarEvent.start_at < hi)
    events = s.scalars(q.order_by(CalendarEvent.start_at)).all()
    if range == "today":
        return [_row(s, e) for e in events]
    out, seen = [], set()
    for e in events:  # Upcoming: each recurring series once, at its next occurrence
        if e.recurring_event_id:
            if e.recurring_event_id in seen:
                continue
            seen.add(e.recurring_event_id)
        out.append(_row(s, e) | {"repeats": _repeats(s, e) if e.recurring_event_id else None})
    return out


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


@router.get("/calendar/status")
def calendar_status(u: User = Depends(current_user), s: Session = Depends(get_session)):
    """connected: Google tokens are stored; syncing: a sync for this user is queued or running."""
    return {"connected": s.scalar(select(GoogleToken.id).where(GoogleToken.user_id == u.id)) is not None,
            "syncing": s.scalar(select(Job.id).where(Job.kind == "sync_calendar", Job.payload == {"user_id": u.id},
                                                     Job.status.in_(("queued", "running"))).limit(1)) is not None}


# shortcut: list-only devices endpoint for the Today pill; Task 15 (pairing) moves it to devices.py and extends it.
@router.get("/devices")
def list_devices(u: User = Depends(current_user), s: Session = Depends(get_session)):
    return [{"id": d.id, "name": d.name, "last_seen": d.last_seen}
            for d in s.scalars(select(Device).where(Device.user_id == u.id).order_by(Device.id))]
