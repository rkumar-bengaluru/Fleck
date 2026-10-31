"""After a meeting (spec 7.8): recording assembly, "used it" markers and the summary, run as the `process_meeting` job."""
import difflib
import io
import json
import math
import re
import subprocess
import tempfile
import wave
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fleck_core.retrieval import Embedder
from sqlalchemy import select
from sqlalchemy.orm import Session

from fleck_api.auth import current_user
from fleck_api.claude import claude_call
from fleck_api.config import get_settings, setting
from fleck_api.db import get_session
from fleck_api.jobs import job
from fleck_api.library import _minio
from fleck_api.meetings import BUCKET, _meeting
from fleck_api.models import Assist, AudioChunk, Meeting, Note, Segment, Summary, User

router = APIRouter(prefix="/api")
USED_WINDOW_MS = 15_000
HEADINGS = ("Key points", "Decisions", "Next steps", "Open questions")
SUMMARY_PROMPT = Path(__file__).with_name("prompts") / "summary.md"
SUMMARY_TOOL = {
    "name": "write_summary", "description": "The meeting summary.",
    "input_schema": {"type": "object", "required": ["sections"], "properties": {"sections": {"type": "array", "items": {
        "type": "object", "required": ["heading", "items"], "properties": {
            "heading": {"type": "string", "enum": list(HEADINGS)},
            "items": {"type": "array", "items": {"type": "object", "required": ["user_text", "ai_text"], "properties": {
                "user_text": {"type": "string"}, "ai_text": {"type": "string"}}}}}}}}},
}


def assemble_recording(s: Session, meeting_id: int) -> None:
    """Chunks by seq (a missing seq becomes silence as long as a full chunk) -> one stereo AAC file in MinIO."""
    chunks = list(s.scalars(select(AudioChunk).where(AudioChunk.meeting_id == meeting_id).order_by(AudioChunk.seq)))
    if not chunks:
        return
    mc = _minio()
    with tempfile.TemporaryDirectory() as d:
        paths, params, frames = {}, None, 0
        for c in chunks:
            p = Path(d) / f"{c.seq:06d}.wav"
            mc.fget_object(BUCKET, c.blob_key, str(p))
            with wave.open(str(p)) as w:
                params, frames = params or w.getparams(), max(frames, w.getnframes())
            paths[c.seq] = p
        gap = Path(d) / "silence.wav"
        with wave.open(str(gap), "wb") as w:
            w.setnchannels(params.nchannels), w.setsampwidth(params.sampwidth), w.setframerate(params.framerate)
            w.writeframes(b"\0" * params.sampwidth * params.nchannels * frames)
        order = [paths.get(seq, gap) for seq in range(chunks[-1].seq + 1)]  # shortcut: a gap is a whole chunk, 10 s in the helper
        (Path(d) / "list.txt").write_text("".join(f"file '{p}'\n" for p in order))
        out = Path(d) / "out.m4a"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(Path(d) / "list.txt"),
                            "-c:a", "aac", "-ac", "2", str(out)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-300:]}")
        key = f"recordings/{meeting_id}.m4a"
        mc.fput_object(BUCKET, key, str(out), content_type="audio/mp4")
    s.get(Meeting, meeting_id).recording_key = key


def store_transcript(s: Session, meeting_id: int) -> None:
    segs = s.scalars(select(Segment).where(Segment.meeting_id == meeting_id).order_by(Segment.start_ms, Segment.id))
    assists = s.scalars(select(Assist).where(Assist.meeting_id == meeting_id).order_by(Assist.id))
    doc = {"segments": [{c: getattr(x, c) for c in ("segment_id", "stream", "start_ms", "end_ms", "text", "gate")} for x in segs],
           "assists": [{c: getattr(x, c) for c in ("assist_id", "segment_id", "question", "answer", "kind", "citations", "used", "used_by")} for x in assists]}
    data = json.dumps(doc).encode()
    _minio().put_object(BUCKET, f"transcripts/{meeting_id}.json", io.BytesIO(data), len(data), content_type="application/json")


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def compute_used_markers(s: Session, meeting_id: int, embedder=None) -> None:
    """Auto "used it": the best cosine between an assist's answer and the You finals that start within 15 s after the
    card appeared. Assists a person set (used_by manual) are left alone."""
    threshold = float(setting("FLECK_USED_THRESHOLD", "") or 0.55)
    segs = {x.segment_id: x for x in s.scalars(select(Segment).where(Segment.meeting_id == meeting_id))}
    you = [x for x in segs.values() if x.stream == "you" and x.text.strip()]
    todo = [a for a in s.scalars(select(Assist).where(Assist.meeting_id == meeting_id)) if a.used_by != "manual"]
    embedder = embedder or Embedder(setting("FLECK_OLLAMA_URL", "http://localhost:11434"), timeout=120)
    vec = dict(zip((x.segment_id for x in you), embedder.embed([x.text for x in you]))) if you else {}
    for a in todo:
        q = segs.get(a.segment_id)
        a.used_by, a.used_score, score = "auto", None, 0.0
        if q is not None and a.answer.strip():
            shown = q.end_ms + (a.first_words_ms or 0)
            near = [x.segment_id for x in you if shown <= x.start_ms <= shown + USED_WINDOW_MS]
            if near:
                answer = embedder.embed([a.answer])[0]
                score = max(_cos(answer, vec[i]) for i in near)
        a.used_score, a.used = score, score >= threshold
    s.commit()


def _note_lines(s: Session, meeting_id: int) -> tuple[str, list[str]]:
    note = s.scalar(select(Note).where(Note.meeting_id == meeting_id))
    text = note.text if note else ""
    return text, [x.strip() for x in text.splitlines() if x.strip()]


def summarise(s: Session, meeting_id: int) -> None:
    notes, lines = _note_lines(s, meeting_id)
    segs = s.scalars(select(Segment).where(Segment.meeting_id == meeting_id).order_by(Segment.start_ms, Segment.id))
    transcript = "\n".join(f"{'You' if x.stream == 'you' else 'Client'}: {x.text}" for x in segs)
    prompt = f"<notes>\n{notes}\n</notes>\n\n<transcript>\n{transcript}\n</transcript>"
    ask = lambda: claude_call("summary", [{"role": "user", "content": prompt}], get_settings().claude_model, meeting_id, session=s,  # noqa: E731
                              system=SUMMARY_PROMPT.read_text(), tools=[SUMMARY_TOOL], max_tokens=4096)  # Sonnet 5.5 takes no forced tool_choice
    for attempt in range(2):  # verbatim guard: one retry
        if (got := ask().tool_input) is None:
            raise RuntimeError("Claude returned no summary")
        sections = [x for x in got.get("sections", []) if x.get("heading") in HEADINGS]
        if all(i["user_text"] in notes for x in sections for i in x["items"] if i["user_text"]):
            break
    else:  # still paraphrased: the closest note line stands in, without Claude's addition
        used: set[str] = set()
        for x in sections:
            for i in x["items"]:
                if i["user_text"] and i["user_text"] not in notes:
                    near = difflib.get_close_matches(i["user_text"], [ln for ln in lines if ln not in used], 1, 0)
                    i["user_text"], i["ai_text"] = (near[0] if near else ""), ""
                    used.update(near)
    sections = [{"heading": x["heading"], "items": [i for i in x["items"] if i["user_text"] or i["ai_text"]]} for x in sections]
    sections = [x for x in sections if x["items"]]
    covered = "\n".join(i["user_text"] for x in sections for i in x["items"] if i["user_text"])  # coverage guard
    missing = [{"user_text": ln, "ai_text": ""} for ln in lines if ln not in covered]
    if missing:
        kp = next((x for x in sections if x["heading"] == "Key points"), None)
        if kp is None:
            sections.insert(0, kp := {"heading": "Key points", "items": []})
        kp["items"] += missing
    version = (s.scalar(select(Summary.version).where(Summary.meeting_id == meeting_id).order_by(Summary.version.desc())) or 0) + 1
    s.add(Summary(meeting_id=meeting_id, version=version, items=sections))
    s.commit()


@job("process_meeting")
def process_meeting(s: Session, payload: dict) -> None:
    """Idempotent: only a meeting still `processing` is worked on. A step error ends the meeting as `failed` (no job retry)."""
    m = s.get(Meeting, payload["meeting_id"])
    if m is None or m.status != "processing":
        return
    mid = m.id
    try:
        assemble_recording(s, mid)
        s.commit()
        store_transcript(s, mid)
        compute_used_markers(s, mid)
        summarise(s, mid)
        s.get(Meeting, mid).status = "ready"
    except Exception as e:
        s.rollback()
        m = s.get(Meeting, mid)
        m.status, m.failure_reason = "failed", f"{type(e).__name__}: {e}"[:500]
    s.commit()


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


@router.get("/meetings/{meeting_id}/recording")
def get_recording(meeting_id: int, request: Request, _u: User = Depends(current_user), s: Session = Depends(get_session)):
    m = _meeting(s, meeting_id)
    if not m.recording_key:
        raise HTTPException(404, "no recording")
    mc, key = _minio(), m.recording_key
    size = mc.stat_object(BUCKET, key).size
    head = {"Accept-Ranges": "bytes"}
    start, end, status = 0, size - 1, 200
    if rng := request.headers.get("range"):  # single ranges only: "a-b", "a-", "-n"
        g = _RANGE.match(rng.strip())
        if not g or not (g[1] or g[2]):
            raise HTTPException(416, "bad range", headers={"Content-Range": f"bytes */{size}"})
        start, end = (max(0, size - int(g[2])), size - 1) if not g[1] else (int(g[1]), min(int(g[2]), size - 1) if g[2] else size - 1)
        if start > end or start >= size:
            raise HTTPException(416, "bad range", headers={"Content-Range": f"bytes */{size}"})
        status, head["Content-Range"] = 206, f"bytes {start}-{end}/{size}"
    obj = mc.get_object(BUCKET, key, offset=start, length=end - start + 1)

    def body():
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()
    return StreamingResponse(body(), status_code=status, media_type="audio/mp4", headers=head | {"Content-Length": str(end - start + 1)})
