"""Live traffic (spec 7.6): the helper's websocket in, browsers' websockets out. One api process, so the hub is in memory."""
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from fastapi import APIRouter, Cookie, Depends, WebSocket, WebSocketDisconnect
from fleck_core.events import AssistEvent, SegmentEvent, parse_event
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fleck_api.auth import allowed_origins, current_user
from fleck_api.db import get_session
from fleck_api.devices import device_from_header
from fleck_api.models import Assist, LlmCall, Meeting, Segment

log = logging.getLogger("fleck.realtime")
router = APIRouter(prefix="/api/ws")


@dataclass
class _Helper:
    ws: WebSocket
    user_id: int
    device_id: int
    seen: float = field(default_factory=time.monotonic)


class Hub:
    def __init__(self):
        self.browsers: dict[int, set[WebSocket]] = {}
        self.helpers: list[_Helper] = []

    def connected_device_ids(self) -> set[int]:
        return {h.device_id for h in self.helpers}

    async def to_browsers(self, meeting_id: int, data: dict) -> None:
        for ws in list(self.browsers.get(meeting_id, ())):
            try:
                await ws.send_json(data)
            except Exception:  # gone; its receive loop cleans up
                log.info("browser websocket dropped")

    async def to_helper(self, user_id: int, data: dict) -> bool:
        """To the user's most recently seen connected device; False if there is none."""
        for h in sorted((h for h in self.helpers if h.user_id == user_id), key=lambda h: h.seen, reverse=True):
            try:
                await h.ws.send_json(data)
                return True
            except Exception:
                continue
        return False


hub = Hub()


@router.websocket("/meetings/{meeting_id}")
async def browser_ws(ws: WebSocket, meeting_id: int, fleck_session: str | None = Cookie(None)):
    origin = ws.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in allowed_origins():
        await ws.close(1008)
        return
    with contextmanager(ws.app.dependency_overrides.get(get_session, get_session))() as s:  # short: a socket open for the whole meeting must not hold a pooled connection
        try:
            current_user(fleck_session, s)
        except Exception:
            await ws.close(1008)
            return
        m = s.get(Meeting, meeting_id)
        status = m.status if m else "unknown"
    await ws.accept()
    hub.browsers.setdefault(meeting_id, set()).add(ws)
    # the client then fetches GET /api/meetings/{id} and applies events: this one says it is subscribed
    await ws.send_json({"type": "meeting_state", "meeting_id": str(meeting_id), "status": status})
    try:
        while True:
            await ws.receive_text()  # nothing is expected; this notices the disconnect
    except WebSocketDisconnect:
        pass
    finally:
        hub.browsers[meeting_id].discard(ws)


@router.websocket("/helper")
async def helper_ws(ws: WebSocket, s: Session = Depends(get_session)):
    device = device_from_header(s, ws.headers.get("authorization"))
    if device is None:
        await ws.close(1008)
        return
    await ws.accept()
    me = _Helper(ws, device.user_id, device.id)
    hub.helpers.append(me)
    try:
        while True:
            data = await ws.receive_json()
            me.seen = time.monotonic()
            try:
                ev = parse_event(data)
            except ValidationError:
                log.warning("helper sent an unreadable event")
                await ws.send_json({"type": "ack"})
                continue
            if isinstance(ev, (SegmentEvent, AssistEvent)):
                try:
                    await _handle(s, device.user_id, ev)
                except Exception:  # acked anyway, or the helper resends it first on every reconnect and the outbox wedges
                    s.rollback()
                    log.exception("event %s %s rejected", ev.type, getattr(ev, "segment_id", ""))
            await ws.send_json({"type": "ack"})  # one per message, duplicates and junk included: the helper resends what is unacked
    except WebSocketDisconnect:
        pass
    finally:
        hub.helpers.remove(me)


async def _handle(s: Session, user_id: int, ev: SegmentEvent | AssistEvent) -> None:
    try:
        mid = int(ev.meeting_id)
    except ValueError:
        return
    m = s.get(Meeting, mid)
    if m is None or m.recorded_by != user_id:
        log.warning("event for meeting %s that this device does not record", ev.meeting_id)
        return
    if (isinstance(ev, SegmentEvent) and not _store_segment(s, mid, ev)) or (isinstance(ev, AssistEvent) and not _store_assist(s, mid, ev)):
        return  # a duplicate
    await hub.to_browsers(mid, ev.model_dump(mode="json"))


def _store_segment(s: Session, mid: int, ev: SegmentEvent) -> bool:
    if not ev.final:
        return True  # partials are only relayed
    r = s.execute(insert(Segment).values(meeting_id=mid, segment_id=ev.segment_id, stream=ev.stream, start_ms=ev.start_ms,
                                         end_ms=ev.end_ms, text=ev.text, gate=ev.gate).on_conflict_do_nothing().returning(Segment.id))
    fresh = r.scalar() is not None
    s.commit()
    return fresh


def _store_assist(s: Session, mid: int, ev: AssistEvent) -> bool:
    a = s.scalar(select(Assist).where(Assist.assist_id == ev.assist_id))
    if a is not None and a.kind is not None:
        return False  # finished (kind is set by done): a resend of any phase changes nothing
    if ev.phase == "thinking":
        if a is None:
            a = Assist(meeting_id=mid, assist_id=ev.assist_id)
            s.add(a)
        # a newer answer for the same turn replaces the card
        a.segment_id, a.question, a.answer, a.kind = ev.segment_id, ev.question, "", None
    elif ev.phase == "done":
        if a is None:
            return False
        a.answer = ev.text  # the helper puts the whole shown answer here ("" for not_in_material)
        a.kind, a.from_brief = ev.kind or "not_in_material", ev.from_brief
        a.citations = [c.model_dump(mode="json") for c in ev.citations]
        a.first_words_ms, a.full_ms, a.rejected = ev.first_words_ms, ev.full_ms, ev.rejected
        for c in ev.llm:
            s.add(LlmCall(purpose=c.purpose, model=c.model, request={"text": c.request}, response={"text": c.response},
                          input_tokens=c.input_tokens, output_tokens=c.output_tokens, latency_ms=c.latency_ms,
                          status=c.status, error=c.error, meeting_id=mid))
    s.commit()
    return True
