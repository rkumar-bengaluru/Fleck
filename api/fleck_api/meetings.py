"""Meetings: create (from the web or the helper), the live record, audio chunks, notes (spec 7.6)."""
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import false, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fleck_api.auth import current_user
from fleck_api.db import get_session
from fleck_api.devices import current_device, user_or_device
from fleck_api.jobs import enqueue, enqueue_once
from fleck_api.library import _minio
from fleck_api.models import Assist, AudioChunk, Brief, BriefItem, CalendarEvent, Client, Device, LlmCall, Meeting, Note, Segment, Summary, User
from fleck_api.realtime import hub

router = APIRouter(prefix="/api")
BUCKET = "fleck-recordings"
MAX_CHUNK = 64 * 1024 * 1024


def _row(o, *cols) -> dict:
    return {c: getattr(o, c) for c in cols}


def meeting_dict(s: Session, m: Meeting) -> dict:
    ev = s.get(CalendarEvent, m.event_id) if m.event_id else None
    client = s.get(Client, m.client_id) if m.client_id else None
    return _row(m, "id", "event_id", "title", "client_id", "recorded_by", "status", "failure_reason", "started_at", "ended_at") | {
        "client_name": client.name if client else None, "attendees": ev.attendees if ev else [],
        # the names the helper must keep out of what it sends to Claude (either side of the call)
        "people": people_of(ev) if ev else []}


def people_of(ev: CalendarEvent) -> list[str]:
    return [a.get("name") or a["email"].split("@")[0].replace(".", " ").replace("_", " ").title() for a in ev.attendees if a.get("email")]


def _meeting(s: Session, meeting_id: int) -> Meeting:
    if (m := s.get(Meeting, meeting_id)) is None:
        raise HTTPException(404, "meeting not found")
    return m


class MeetingIn(BaseModel):
    event_id: int | None = None
    title: str | None = None
    start_helper: bool = False


@router.get("/me")
def me(u: User = Depends(user_or_device)):  # the helper's "Connected as" too
    return {"id": u.id, "email": u.email, "name": u.name}


@router.post("/meetings")
async def create_meeting(body: MeetingIn, u: User = Depends(user_or_device), s: Session = Depends(get_session)):
    ev = s.get(CalendarEvent, body.event_id) if body.event_id else None
    if body.event_id and (ev is None or ev.user_id != u.id):
        raise HTTPException(404, "event not found")
    m = Meeting(event_id=body.event_id, title=body.title or (ev.title if ev else "Untitled meeting"),
                client_id=ev.client_id if ev else None, recorded_by=u.id)
    s.add(m)
    s.flush()
    out = meeting_dict(s, m)
    s.commit()  # before `start`: the helper may fetch the meeting or send events at once
    if body.start_helper:
        start = {"type": "start", "meeting_id": str(m.id), "event_id": body.event_id, "client_name": out["client_name"],
                 "people": out["people"]}
        if not await hub.to_helper(u.id, start):
            s.delete(m)
            s.commit()
            raise HTTPException(409, "Desktop app not connected")
    return out


@router.get("/meetings")
def list_meetings(_u: User = Depends(current_user), s: Session = Depends(get_session)):
    return [meeting_dict(s, m) for m in s.scalars(select(Meeting).order_by(Meeting.started_at.desc(), Meeting.id.desc()))]


@router.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: int, _u: User = Depends(user_or_device), s: Session = Depends(get_session)):
    m = _meeting(s, meeting_id)
    note = s.scalar(select(Note).where(Note.meeting_id == m.id))
    summary = s.scalar(select(Summary).where(Summary.meeting_id == m.id).order_by(Summary.version.desc()))
    brief = s.scalar(select(Brief).where(Brief.event_id == m.event_id)) if m.event_id else None
    first_meeting = s.scalar(select(func.min(Meeting.id)).where(Meeting.event_id == m.event_id)) if m.event_id else None
    items = list(s.scalars(select(BriefItem).where(BriefItem.brief_id == brief.id).order_by(BriefItem.id))) if brief else []
    return {
        "meeting": meeting_dict(s, m) | {"recorded_by_name": ((u := s.get(User, m.recorded_by)).name or u.email) if m.recorded_by else "", "library_clients": list(s.scalars(select(Client.name).order_by(Client.name)))},
        "segments": [_row(x, "segment_id", "stream", "start_ms", "end_ms", "text", "gate")
                     for x in s.scalars(select(Segment).where(Segment.meeting_id == m.id).order_by(Segment.start_ms, Segment.id))],
        "assists": [_row(x, "assist_id", "segment_id", "question", "answer", "kind", "from_brief", "late", "citations", "first_words_ms",
                         "full_ms", "rejected", "used", "used_by") for x in s.scalars(select(Assist).where(Assist.meeting_id == m.id).order_by(Assist.id))],
        "notes": note.text if note else "",
        "summary": summary.items if summary else None,
        "cost_usd": s.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
            (LlmCall.meeting_id == m.id) | (LlmCall.event_id == m.event_id if m.event_id and m.id == first_meeting else false()))),  # an event's brief cost is counted once, on its first meeting
        "brief": {"status": brief.status, "items": [_row(i, "topic", "question", "answer", "kind", "citations") for i in items]} if brief else None,
    }


@router.put("/meetings/{meeting_id}/audio/{seq}", status_code=204)
async def put_audio(meeting_id: int, seq: int, request: Request, d: Device = Depends(current_device), s: Session = Depends(get_session)):
    if _meeting(s, meeting_id).recorded_by != d.user_id:
        raise HTTPException(403, "not your meeting")
    data = await request.body()
    if not data or len(data) > MAX_CHUNK:
        raise HTTPException(400, "empty or oversized audio chunk")
    key = f"meetings/{meeting_id}/{seq:06d}.wav"
    mc = _minio()
    if not mc.bucket_exists(BUCKET):
        mc.make_bucket(BUCKET)
    mc.put_object(BUCKET, key, io.BytesIO(data), len(data), content_type="audio/wav")  # same key: a resend overwrites
    s.execute(insert(AudioChunk).values(meeting_id=meeting_id, seq=seq, blob_key=key).on_conflict_do_nothing())
    s.commit()


class NotesIn(BaseModel):
    text: str


@router.put("/meetings/{meeting_id}/notes", status_code=204)
def put_notes(meeting_id: int, body: NotesIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    m = _meeting(s, meeting_id)
    s.execute(insert(Note).values(meeting_id=meeting_id, text=body.text, updated_by=u.id).on_conflict_do_update(
        index_elements=[Note.meeting_id], set_={"text": body.text, "updated_by": u.id, "updated_at": datetime.now(timezone.utc)}))
    s.commit()
    if m.status == "ready":  # the chat index holds the note lines
        enqueue_once(s, "index_meeting", {"meeting_id": meeting_id})


class ClientIn(BaseModel):
    client_id: int | None


@router.put("/meetings/{meeting_id}/client", status_code=204)
def put_client(meeting_id: int, body: ClientIn, _u: User = Depends(current_user), s: Session = Depends(get_session)):
    m = _meeting(s, meeting_id)
    if body.client_id is not None and s.get(Client, body.client_id) is None:
        raise HTTPException(404, "client not found")
    m.client_id = body.client_id
    s.commit()


@router.post("/meetings/{meeting_id}/stop", status_code=204)
async def stop_meeting(meeting_id: int, u: User = Depends(user_or_device), s: Session = Depends(get_session),
                       authorization: str | None = Header(None)):
    m = _meeting(s, meeting_id)
    if authorization and m.recorded_by != u.id:  # a helper stops only its own user's recordings
        raise HTTPException(403, "not your meeting")
    sent = await hub.to_helper(m.recorded_by or u.id, {"type": "stop", "meeting_id": str(m.id)})
    # the transition is one conditional UPDATE, so only one of any number of Stops gets the row back and enqueues
    won = s.execute(update(Meeting).where(Meeting.id == m.id, Meeting.status == "recording")
                    .values(status="processing", ended_at=func.now()).returning(Meeting.id)).first()
    if won:
        # 15 s for the helper's last chunks to arrive; nothing to wait for when it is offline
        enqueue(s, "process_meeting", {"meeting_id": m.id}, func.now() + timedelta(seconds=15) if sent else None)  # commits both
        await hub.to_browsers(m.id, {"type": "meeting_state", "meeting_id": str(m.id), "status": "processing"})
    else:
        s.rollback()


@router.post("/meetings/{meeting_id}/retry", status_code=204)
def retry_meeting(meeting_id: int, _u: User = Depends(current_user), s: Session = Depends(get_session)):
    _meeting(s, meeting_id)
    won = s.execute(update(Meeting).where(Meeting.id == meeting_id, Meeting.status == "failed")
                    .values(status="processing", failure_reason=None).returning(Meeting.id)).first()
    if won:  # one conditional UPDATE, like Stop: a double click enqueues once
        enqueue(s, "process_meeting", {"meeting_id": meeting_id})  # commits both
    else:
        s.rollback()


class UsedIn(BaseModel):
    used: bool


@router.patch("/assists/{assist_id}")
def patch_assist(assist_id: str, body: UsedIn, _u: User = Depends(current_user), s: Session = Depends(get_session)):
    a = s.scalar(select(Assist).where(Assist.assist_id == assist_id))
    if a is None:
        raise HTTPException(404, "assist not found")
    a.used, a.used_by = body.used, "manual"
    s.commit()
    return _row(a, "assist_id", "used", "used_by")
