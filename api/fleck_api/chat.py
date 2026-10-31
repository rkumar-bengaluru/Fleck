"""Chat about one meeting or across meetings (Task 21). `index_meeting` writes a ready meeting's transcript windows, note lines
and summary items to Weaviate `MeetingText`; `POST /api/chat` retrieves from it and from the library, and Sonnet answers citing [n]."""
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fleck_core.retrieval import (TENANT, Embedder, RetrievalError, Retriever, delete_document_passages, ensure_schema, put_passages,
                                  weaviate_client)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fleck_api.auth import current_user
from fleck_api.claude import claude_call
from fleck_api.config import get_settings, setting
from fleck_api.db import get_session
from fleck_api.jobs import enqueue, job
from fleck_api.library import _collection
from fleck_api.meetings import _meeting
from fleck_api.models import ChatMessage, ChatThread, Job, Meeting, Segment, Summary, User
from fleck_api.processing import _note_lines

log = logging.getLogger("fleck.chat")
router = APIRouter(prefix="/api")
PROMPT = Path(__file__).with_name("prompts") / "chat.md"
WINDOW_MS = 60_000
MEETING_HITS, LIBRARY_HITS, HISTORY = 8, 4, 6
_txt = lambda n, tok="word": {"name": n, "dataType": ["text"], "tokenization": tok}  # noqa: E731
PROPERTIES = [_txt("text"), _txt("title"), _txt("kind", "field"), _txt("meeting_id", "field"), _txt("chunk_id", "field"),
              {"name": "start_ms", "dataType": ["int"]}]


def _meeting_collection() -> str:
    return setting("FLECK_WEAVIATE_MEETING_COLLECTION", "MeetingText")


def _urls() -> tuple[str, str]:
    return setting("FLECK_WEAVIATE_URL", "http://localhost:8080"), setting("FLECK_OLLAMA_URL", "http://localhost:11434")


def ensure_meeting_index() -> None:
    with weaviate_client(_urls()[0]) as w:
        ensure_schema(w, _meeting_collection(), PROPERTIES)


def meeting_rows(s: Session, m: Meeting) -> list[dict]:
    """transcript in 60 s windows (start_ms = the first line's), one row per note line and per summary item."""
    base = {"meeting_id": str(m.id), "title": m.title}
    rows, windows = [], {}
    for x in s.scalars(select(Segment).where(Segment.meeting_id == m.id, Segment.text != "").order_by(Segment.start_ms, Segment.id)):
        w = windows.setdefault(x.start_ms // WINDOW_MS, {"start_ms": x.start_ms, "lines": []})
        w["lines"].append(f"{'You' if x.stream == 'you' else 'Client'}: {x.text}")
    rows += [base | {"kind": "transcript", "start_ms": w["start_ms"], "text": "\n".join(w["lines"]), "chunk_id": f"m{m.id}-t{k}"} for k, w in windows.items()]
    rows += [base | {"kind": "note", "start_ms": 0, "text": ln, "chunk_id": f"m{m.id}-n{i}"} for i, ln in enumerate(_note_lines(s, m.id))]
    summary = s.scalar(select(Summary).where(Summary.meeting_id == m.id).order_by(Summary.version.desc()))
    items = [(sec["heading"], i) for sec in (summary.items if summary else []) for i in sec["items"]]
    rows += [base | {"kind": "summary", "start_ms": 0, "text": f"{h}: {i['user_text']} {i['ai_text']}".strip(), "chunk_id": f"m{m.id}-s{k}"}
             for k, (h, i) in enumerate(items)]
    return rows


@job("index_meeting")
def index_meeting(s: Session, payload: dict) -> None:
    """Idempotent: a re-run replaces the meeting's rows."""
    m = s.get(Meeting, payload["meeting_id"])
    if m is None or m.status != "ready":
        return
    rows = meeting_rows(s, m)
    vectors = Embedder(_urls()[1], timeout=120).embed([r["text"] for r in rows]) if rows else []
    with weaviate_client(_urls()[0]) as w:
        ensure_schema(w, _meeting_collection(), PROPERTIES)
        delete_document_passages(w, str(m.id), _meeting_collection(), "meeting_id")
        put_passages(w, rows, vectors, _meeting_collection())


def backfill(s: Session) -> None:
    """Worker start: every ready meeting that has no index job yet (queued, running or done) gets one."""
    for mid in s.scalars(select(Meeting.id).where(Meeting.status == "ready")):
        if not s.scalar(select(Job.id).where(Job.kind == "index_meeting", Job.payload["meeting_id"].as_integer() == mid,
                                             Job.status.in_(("queued", "running", "done"))).limit(1)):
            enqueue(s, "index_meeting", {"meeting_id": mid})


def search_meetings(question: str, vector: list[float], meeting_id: int | None) -> list[dict]:
    where = f'where:{{path:["meeting_id"],operator:Equal,valueText:{json.dumps(str(meeting_id))}}},' if meeting_id is not None else ""
    coll = _meeting_collection()
    q = (f'{{Get{{{coll}(tenant:"{TENANT}",{where}hybrid:{{query:{json.dumps(question)},vector:{json.dumps(vector)},alpha:0.5}},'
         f'limit:{MEETING_HITS}){{text title kind meeting_id start_ms}}}}}}')
    with weaviate_client(_urls()[0], 10) as w:
        r = w.post("/v1/graphql", json={"query": q})
        r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"Weaviate query failed: {body['errors'][0]['message']}")
    return body["data"]["Get"][coll] or []


def _mmss(ms: int) -> str:
    return f"{ms // 60_000}:{ms // 1000 % 60:02d}"


def _source(n: int, hit) -> tuple[str, dict]:
    """The prompt block for source n, and its citation."""
    if isinstance(hit, dict):  # a MeetingText row
        mid, ms, kind = int(hit["meeting_id"]), hit["start_ms"] or 0, hit["kind"]
        where = f", {_mmss(ms)}" if kind == "transcript" else ""
        cite = {"type": "meeting", "n": n, "meeting_id": mid, "kind": kind, "label": f"{hit['title']} {_mmss(ms)}" if kind == "transcript" else f"{hit['title']} ({kind})"}
        if kind == "transcript":
            cite["start_ms"] = ms
        return f"[{n}] Meeting \"{hit['title']}\"{where}, {kind}:\n{hit['text']}", cite
    return f"[{n}] Document \"{hit.title}\" ({hit.kind}):\n{hit.text}", {"type": "document", "n": n, "doc_id": hit.doc_id, "label": hit.title}


def _thread(s: Session, meeting_id: int | None, create: bool = True) -> ChatThread | None:
    """One thread per meeting and one for Ask-all (unique indexes, migration 0010): a racing insert does nothing and both re-select."""
    q = select(ChatThread).where(ChatThread.meeting_id == meeting_id if meeting_id is not None else ChatThread.meeting_id.is_(None)).order_by(ChatThread.id).limit(1)
    t = s.scalar(q)
    if t is None and create:
        s.execute(insert(ChatThread).values(meeting_id=meeting_id).on_conflict_do_nothing())
        s.commit()
        t = s.scalar(q)
    return t


MARKER = re.compile(r"( ?)\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")  # 3+ digit numbers such as [2024] are not markers


def _markers(text: str, valid: set[int]) -> str:
    """Rewrite each marker group as [1][2] with the valid numbers only; a group with none left disappears."""
    def fix(g):
        keep = "".join(f"[{n}]" for n in dict.fromkeys(int(x) for x in re.findall(r"\d+", g[2])) if n in valid)
        return g[1] + keep if keep else ""
    return MARKER.sub(fix, text)


def _history(s: Session, thread_id: int) -> list[ChatMessage]:
    return list(s.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.id)))


class ChatIn(BaseModel):
    meeting_id: int | None = None
    text: str


@router.get("/chat")
def get_chat(meeting_id: int | None = None, _u: User = Depends(current_user), s: Session = Depends(get_session)):
    t = _thread(s, meeting_id, create=False)  # reading makes no thread
    return [{"role": m.role, "text": m.text, "citations": m.citations} for m in _history(s, t.id)] if t else []


@router.post("/chat")
def post_chat(body: ChatIn, _u: User = Depends(current_user), s: Session = Depends(get_session)):
    question = body.text.strip()
    if not question:
        raise HTTPException(422, "Type a question")
    if body.meeting_id is not None:
        _meeting(s, body.meeting_id)
    weaviate, ollama = _urls()
    embedder = Embedder(ollama, timeout=60)
    try:
        vec = embedder.embed_query(question)
        hits = search_meetings(question, vec, body.meeting_id)
    except (httpx.HTTPError, RuntimeError) as e:
        log.warning("chat search failed: %s", e)
        raise HTTPException(503, "Search is not available right now. Check that Ollama and Weaviate are running.")
    try:
        passages = Retriever(weaviate, embedder, _collection(), timeout=10).search(question, LIBRARY_HITS, vector=vec)
    except RetrievalError as e:  # the library is a bonus here: the meeting's own text still answers
        log.warning("chat library search failed: %s", e)
        passages = []
    blocks, cites = zip(*(_source(n, h) for n, h in enumerate([*hits, *passages], 1))) if hits or passages else ((), ())
    thread = _thread(s, body.meeting_id)
    past = _history(s, thread.id)[-HISTORY:]  # earlier [n] belong to earlier sources: they are removed before the history goes back
    prompt = "<sources>\n" + "\n\n".join(blocks) + f"\n</sources>\n\n<question>\n{question}\n</question>"
    try:
        msg = claude_call("chat", [{"role": m.role, "content": _markers(m.text, set())} for m in past] + [{"role": "user", "content": prompt}],
                          get_settings().claude_model, body.meeting_id, session=s, system=PROMPT.read_text(), max_tokens=2000)
    except Exception:  # claude_call traced the failure; the key and the API's message stay out of the reply
        raise HTTPException(502, "Claude could not answer. Try again.")
    valid = {c["n"] for c in cites}
    text = _markers(msg.text.strip(), valid)
    used = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    citations = [c for c in cites if c["n"] in used]
    if cites and not citations:
        text += "\n\nNo sources cited."
    s.add_all([ChatMessage(thread_id=thread.id, role="user", text=question), ChatMessage(thread_id=thread.id, role="assistant", text=text, citations=citations)])
    s.commit()
    return {"text": text, "citations": citations}
