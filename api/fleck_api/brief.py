"""Pre-meeting brief (Task 20): Claude Sonnet proposes likely questions from the redacted invite and the client's
documents; local Qwen answers each one from the library with the live path's validators. Claude never writes an answer."""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fleck_core.answer import MODEL, Delta, Done, OllamaClient, Sentence, stream_library_answer
from fleck_core.retrieval import EMBED_MODEL, Embedder, Retriever
from fleck_core.validate import redact_clients, redact_people
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fleck_api.auth import current_user
from fleck_api.claude import claude_call
from fleck_api.config import get_settings, setting
from fleck_api.db import get_session
from fleck_api.devices import user_or_device
from fleck_api.jobs import enqueue, enqueue_once, job, wait_for_recording
from fleck_api.meetings import _meeting, people_of
from fleck_api.models import Brief, BriefItem, CalendarEvent, Client, Document, Job, LlmCall, Meeting, User

log = logging.getLogger("fleck.brief")
router = APIRouter(prefix="/api")
PROMPT = Path(__file__).with_name("prompts") / "brief_questions.md"
TOPICS = ("commercial", "proof", "delivery", "risk", "technical")
MAX_QUESTIONS = 8
LEAD = timedelta(minutes=30)
RECORDING_RETRY = timedelta(minutes=5)
QUESTIONS_TTL = timedelta(minutes=10)
KEEP_ALIVE = "2m"  # the brief must not pin qwen3:4b on the GPU between meetings
TOOL = {
    "name": "propose_questions", "description": "The questions the prospect is likely to ask.", "strict": True,
    "input_schema": {"type": "object", "required": ["questions"], "additionalProperties": False, "properties": {"questions": {
        "type": "array", "items": {"type": "object", "required": ["topic", "question"], "additionalProperties": False, "properties": {
            "topic": {"type": "string", "enum": list(TOPICS)}, "question": {"type": "string"}}}}}},
}


def _keep_alive(url: str, model: str) -> int | str:
    """KEEP_ALIVE, or -1 when `model` is already loaded without expiry (a recording pinned it). Ollama sets a loaded
    model's expiry from the newest request's keep_alive, so a brief that started before Record would otherwise lower
    the live models' -1 to 2 min. shortcut: a Record landing between this check and the call still wins the race only
    if its warm-up is scheduled after the call; the live path's next request (all keep_alive -1) restores it."""
    try:
        loaded = httpx.get(f"{url}/api/ps", timeout=5).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return KEEP_ALIVE
    pinned = any(model in (m.get("name"), m.get("model")) and m.get("expires_at", "") > "2100" for m in loaded)
    return -1 if pinned else KEEP_ALIVE


class _Ollama(OllamaClient):
    def __init__(self, url: str):
        super().__init__(url)
        self.url = url

    def stream(self, messages, model, keep_alive=-1, **kw):
        return super().stream(messages, model, _keep_alive(self.url, model), **kw)

    def warm(self, model):
        self._keep(model, _keep_alive(self.url, model))


class _Embedder(Embedder):
    def embed(self, texts):
        self.keep_alive = _keep_alive(self.url, EMBED_MODEL)
        return super().embed(texts)


def _deps():
    ollama = setting("FLECK_OLLAMA_URL", "http://localhost:11434")
    return Retriever(setting("FLECK_WEAVIATE_URL", "http://localhost:8080"), _Embedder(ollama, KEEP_ALIVE, timeout=60)), _Ollama(ollama)


def propose_questions(s: Session, title: str, description: str | None, doc_titles: list[str], passages: list,
                      event_id: int | None) -> list[dict]:
    """Everything passed in is already redacted. Returns up to MAX_QUESTIONS {topic, question}; raises on a malformed reply."""
    parts = [f"<invite>\nTitle: {title}\n" + (f"Description: {description[:2000]}\n" if description else "") + "</invite>"]
    if doc_titles:
        parts.append("<documents>\n" + "\n".join(doc_titles) + "\n</documents>")
    if passages:
        parts.append("<passages>\n" + "\n\n".join(f"{p.title}\n{p.text}" for p in passages) + "\n</passages>")
    msg = claude_call("brief_questions", [{"role": "user", "content": "\n\n".join(parts)}], get_settings().claude_model, session=s,
                      event_id=event_id, system=PROMPT.read_text(), tools=[TOOL], max_tokens=2000)  # Sonnet 5.5 takes no forced tool_choice
    try:
        out = [{"topic": q["topic"], "question": q["question"].strip()} for q in msg.tool_input["questions"]]
    except (TypeError, KeyError, AttributeError):
        raise RuntimeError("Claude did not return the questions tool's shape")
    return [q for q in out if q["topic"] in TOPICS and q["question"]][:MAX_QUESTIONS]


def answer_question(question: str, retriever, ollama, client_names: list[str]) -> dict:
    """The live path's search and checks, without the fallback. kind is library, reference or not_in_material.
    Raises when Weaviate or Ollama fails: a down service must not turn every question into a gap."""
    vector = retriever.embedder.embed_query(question)
    out = {"vector": vector, "kind": "not_in_material", "answer": "", "citations": []}
    passages = retriever.search(question, vector=vector)
    if not passages:
        return out
    now, texts = time.time(), {}
    for u in stream_library_answer(question, passages, client_names, ollama, now, now):
        if isinstance(u, Sentence):
            texts[u.index] = u.text if u.ok else ""
        elif isinstance(u, Done):
            if u.check.reasons and u.check.reasons[0] in ("timeout", "ollama_error"):
                raise RuntimeError(f"local model failed: {u.check.reasons[0]}")
            if u.check.ok:
                out |= {"kind": u.check.kind, "answer": " ".join(t for _, t in sorted(texts.items()) if t),
                        "citations": [c.model_dump() for c in u.check.citations]}
    return out


_URL, _EMAIL, _DIGITS = re.compile(r"https?://\S+|www\.\S+", re.I), re.compile(r"\S+@\S+"), re.compile(r"\+?\d(?:[\s().-]?\d){5,}")


def _safe(text: str, people: list[str], client_names: list[str]) -> str:
    """Invite text for Claude: links and dial-in numbers/passcodes (6+ digits) go, emails and attendee names become
    "someone" whatever their case (typed text, unlike the live transcript), client names "a client"."""
    text = _EMAIL.sub("someone", _URL.sub("", text))
    return redact_clients(redact_people(_DIGITS.sub("", text), people, ignore_case=True), client_names)


@job("build_brief", on_fail=lambda s, payload, error: mark_failed(s, payload, error))
def build_brief(s: Session, payload: dict) -> None:
    ev = s.get(CalendarEvent, payload["event_id"])
    if ev is None or ev.cancelled:
        return
    if "scheduled_for" in payload and (ev.client_id is None or payload["scheduled_for"] != ev.start_at.isoformat()):
        return  # unlinked or moved since it was queued
    # Ollama is not touched mid-meeting (it would compete with the live path for the GPU)
    later = {"event_id": ev.id} | ({"scheduled_for": payload["scheduled_for"]} if "scheduled_for" in payload else {})
    if wait_for_recording(s, "build_brief", later, RECORDING_RETRY):
        return
    b = _brief(s, ev.id)
    b.status, b.error = "building", None
    s.commit()
    client = s.get(Client, ev.client_id) if ev.client_id else None
    retriever, ollama = _deps()
    people = people_of(ev)
    names = list(s.scalars(select(Client.name))) + ([client.name] if client else [])
    titles = list(s.scalars(select(Document.title).where(Document.client_id == client.id, Document.status == "indexed")
                            .order_by(Document.title))) if client else []
    # the redacted invite (title and description), plus the client's documents when one is linked. Names go first (privacy rule).
    title = _safe(ev.title, people, names)
    desc = _safe(ev.description, people, names) if ev.description else None
    asked = b.questions
    if asked and datetime.fromisoformat(asked["at"]) > datetime.now(timezone.utc) - QUESTIONS_TTL:
        questions = asked["items"]  # a retry after a failed answer step: Sonnet was already paid for
    else:
        passages = retriever.search(title, limit=6, client_slug=client.slug) if client and titles else []
        questions = propose_questions(s, title, desc, titles, passages, ev.id)
        b.questions = {"at": datetime.now(timezone.utc).isoformat(), "items": questions}
        s.commit()
    ollama.warm(MODEL)
    answered = [(q, answer_question(q["question"], retriever, ollama, names)) for q in questions]
    s.execute(delete(BriefItem).where(BriefItem.brief_id == b.id))
    s.add_all(BriefItem(brief_id=b.id, topic=q["topic"], question=q["question"], answer=a["answer"], kind=a["kind"],
                        citations=a["citations"], question_vector=a["vector"]) for q, a in answered)
    b.doc_count = len(titles)
    b.context = (f"{client.name}: {len(titles)} library document{'s' * (len(titles) != 1)} ({', '.join(titles[:6])})." if titles
                 else f"New prospect: no documents for {client.name} in the library." if client
                 else "No client is linked to this event, so the questions come from the invite alone.")
    b.status, b.updated_at, b.questions = "ready", func.now(), None
    s.commit()


def mark_failed(s: Session, payload: dict, error: str) -> None:
    if b := s.scalar(select(Brief).where(Brief.event_id == payload["event_id"])):
        b.status, b.error = "failed", error[:500]


def _brief(s: Session, event_id: int) -> Brief:
    s.execute(insert(Brief).values(event_id=event_id, status="queued").on_conflict_do_nothing(index_elements=["event_id"]))
    return s.scalar(select(Brief).where(Brief.event_id == event_id).execution_options(populate_existing=True))


def request_build(s: Session, event_id: int) -> None:
    """Queue a build now (a client was linked, or Refresh brief). Once per event while one is waiting."""
    b = _brief(s, event_id)
    if b.status != "building":
        b.status = "queued"
    b.questions = None  # a refresh asks Sonnet again
    s.commit()
    enqueue_once(s, "build_brief", {"event_id": event_id})


def schedule_briefs(s: Session, user_id: int, now: datetime) -> None:
    """One build_brief per event with a client, run_after 30 min before its start. The payload carries the start, so a
    re-sync finds its own job (queued, running or done) and adds nothing; a moved event swaps its queued job.
    An event starting within 30 min is left to the build its client link queued."""
    for e in s.scalars(select(CalendarEvent).where(CalendarEvent.user_id == user_id, CalendarEvent.client_id.is_not(None),
                                                    CalendarEvent.cancelled.is_(False), CalendarEvent.start_at > now)).all():
        payload = {"event_id": e.id, "scheduled_for": e.start_at.isoformat()}
        s.execute(delete(Job).where(Job.kind == "build_brief", Job.status == "queued", Job.payload["event_id"].as_integer() == e.id,
                                    Job.payload.has_key("scheduled_for"), Job.payload["scheduled_for"].as_string() != payload["scheduled_for"]))  # moved
        s.commit()
        if e.start_at > now + LEAD and not s.scalar(select(Job.id).where(Job.kind == "build_brief", Job.payload == payload).limit(1)):
            enqueue(s, "build_brief", payload, e.start_at - LEAD)


def _items(s: Session, b: Brief | None) -> list[BriefItem]:
    return list(s.scalars(select(BriefItem).where(BriefItem.brief_id == b.id).order_by(BriefItem.id))) if b else []


def _page(s: Session, e: CalendarEvent) -> dict:
    b = s.scalar(select(Brief).where(Brief.event_id == e.id))
    c = s.get(Client, e.client_id) if e.client_id else None
    return {"event": {"id": e.id, "title": e.title, "start_at": e.start_at, "end_at": e.end_at, "client": {"id": c.id, "name": c.name} if c else None},
            "status": b.status if b else "none", "context": b.context if b else None, "doc_count": b.doc_count if b else 0,
            "prepared_at": b.updated_at if b and b.status == "ready" else None, "error": b.error if b else None,
            "cost_usd": s.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.event_id == e.id)),
            "items": [{c: getattr(i, c) for c in ("topic", "question", "answer", "kind", "citations")} for i in _items(s, b)]}


def _event(s: Session, u: User, event_id: int) -> CalendarEvent:
    e = s.get(CalendarEvent, event_id)
    if e is None or e.user_id != u.id:
        raise HTTPException(404, "event not found")
    return e


@router.get("/events/{event_id}/brief")
def get_brief(event_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    return _page(s, _event(s, u, event_id))


@router.post("/events/{event_id}/brief")
def refresh_brief(event_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    e = _event(s, u, event_id)
    request_build(s, e.id)
    return _page(s, e)


@router.get("/meetings/{meeting_id}/brief")
def meeting_brief(meeting_id: int, _u: User = Depends(user_or_device), s: Session = Depends(get_session)):
    """What the helper matches live questions against: answered items with their question vectors. Gaps are left out,
    so a question about one goes to the library and the fallback like any other."""
    m = _meeting(s, meeting_id)
    b = s.scalar(select(Brief).where(Brief.event_id == m.event_id)) if m.event_id else None  # a rebuild or a failed refresh keeps the old items
    return {"items": [{c: getattr(i, c) for c in ("question", "answer", "kind", "citations", "question_vector")}
                      for i in _items(s, b) if i.kind != "not_in_material" and i.question_vector]}
