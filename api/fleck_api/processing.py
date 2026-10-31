"""After a meeting (spec 7.8): recording assembly, "used it" markers and the summary, run as the `process_meeting` job."""
import difflib
import io
import json
import math
import re
import subprocess
import tempfile
import wave
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fleck_core.retrieval import Embedder
from fleck_core.validate import figures_in_sources
from minio.error import S3Error
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fleck_api.auth import current_user
from fleck_api.claude import claude_call
from fleck_api.config import get_settings, setting
from fleck_api.db import get_session
from fleck_api.jobs import LATE_GRACE, enqueue, job
from fleck_api.library import _minio
from fleck_api.meetings import BUCKET, _meeting
from fleck_api.models import Assist, AudioChunk, Meeting, Note, Segment, Summary, User

router = APIRouter(prefix="/api")
USED_WINDOW_MS = 15_000
USED_GRACE_MS = 2_000  # a You line may begin this long before the question ends (overlapping speech), no earlier
HEADINGS = ("Key points", "Decisions", "Next steps", "Open questions")
SUMMARY_PROMPT = Path(__file__).with_name("prompts") / "summary.md"
SUMMARY_TOOL = {
    "name": "write_summary", "description": "The meeting summary.", "strict": True,
    "input_schema": {"type": "object", "required": ["sections"], "additionalProperties": False, "properties": {"sections": {"type": "array", "items": {
        "type": "object", "required": ["heading", "items"], "additionalProperties": False, "properties": {
            "heading": {"type": "string", "enum": list(HEADINGS)},
            "items": {"type": "array", "items": {"type": "object", "required": ["user_text", "ai_text"], "additionalProperties": False, "properties": {
                "user_text": {"type": "string"}, "ai_text": {"type": "string"}}}}}}}}},
}
MAX_CHUNKS = 4 * 360  # 4 hours of the helper's 10 s chunks; a larger seq is a bad upload, not a meeting
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def assemble_recording(s: Session, meeting_id: int) -> None:
    """Chunks by seq (a missing seq becomes silence as long as a full chunk) -> one stereo AAC file in MinIO."""
    chunks = list(s.scalars(select(AudioChunk).where(AudioChunk.meeting_id == meeting_id).order_by(AudioChunk.seq)))
    if not chunks:
        return
    if chunks[-1].seq >= MAX_CHUNKS:
        raise RuntimeError(f"audio chunk {chunks[-1].seq} is past the {MAX_CHUNKS}-chunk (4 hour) limit")
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
                            "-c:a", "aac", "-ac", "2", str(out)], capture_output=True, text=True, timeout=600)
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
    """Auto "used it": the best cosine between an assist's answer and the You finals that overlap the 15 s after the
    card appeared (a vendor who starts talking as the card shows is reading it). Assists a person set (used_by manual) are left alone."""
    threshold = float(setting("FLECK_USED_THRESHOLD", "") or 0.52)
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
            near = [x.segment_id for x in you if x.end_ms > shown and q.end_ms - USED_GRACE_MS <= x.start_ms < shown + USED_WINDOW_MS]
            if near:
                answer = embedder.embed([a.answer])[0]
                score = max(_cos(answer, vec[i]) for i in near)
        a.used_score, a.used = score, score >= threshold
    s.commit()


def _clean(text: str) -> list[str]:
    """Non-empty lines without their list bullet."""
    return [x for ln in text.splitlines() if (x := BULLET.sub("", ln).strip())]


def _note_lines(s: Session, meeting_id: int) -> list[str]:
    note = s.scalar(select(Note).where(Note.meeting_id == meeting_id))
    return _clean(note.text if note else "")


def _verbatim(user_text: str, lines: list[str]) -> bool:
    """A whole note line, or a run of whole consecutive lines."""
    ut = _clean(user_text)
    return bool(ut) and any(lines[k:k + len(ut)] == ut for k in range(len(lines)))


def speaker(seg: Segment) -> str:
    """A transcript line's label; a `gap-` line is Fleck's capture-outage marker (spec 7.7), not the client speaking."""
    return "Fleck" if seg.segment_id.startswith("gap-") else "You" if seg.stream == "you" else "Client"


def _shape(got: dict | None) -> list[dict] | None:
    """The tool input as a list of sections, or None when it is not the schema's shape."""
    try:
        out = [{"heading": x["heading"], "items": [{"user_text": i["user_text"], "ai_text": i["ai_text"]} for i in x["items"]]}
               for x in got["sections"] if x["heading"] in HEADINGS]
    except (TypeError, KeyError):
        return None
    return out if all(isinstance(v, str) for x in out for i in x["items"] for v in i.values()) else None


def summarise(s: Session, meeting_id: int) -> None:
    lines = _note_lines(s, meeting_id)
    segs = s.scalars(select(Segment).where(Segment.meeting_id == meeting_id).order_by(Segment.start_ms, Segment.id))
    transcript = "\n".join(f"{speaker(x)}: {x.text}" for x in segs)
    prompt = f"<notes>\n{chr(10).join(lines)}\n</notes>\n\n<transcript>\n{transcript}\n</transcript>"
    sources = [transcript, "\n".join(lines)]
    sections: list[dict] = []
    for attempt in range(2):  # one retry, told what was wrong
        msg = claude_call("summary", [{"role": "user", "content": prompt}], get_settings().claude_model, meeting_id, session=s,
                          system=SUMMARY_PROMPT.read_text(), tools=[SUMMARY_TOOL], max_tokens=16000)  # Sonnet 5.5 takes no forced tool_choice
        got = None if msg.stop_reason == "max_tokens" else _shape(msg.tool_input)
        if got is None:
            sections, why = [], ("The reply hit the token limit; be briefer." if msg.stop_reason == "max_tokens"
                                 else "You did not call write_summary with the required fields.")
        else:
            sections = got
            bad = [i["user_text"] for x in sections for i in x["items"] if i["user_text"] and not _verbatim(i["user_text"], lines)]
            made_up = [i["ai_text"] for x in sections for i in x["items"] if not figures_in_sources(i["ai_text"], sources)]
            if not bad and not made_up:
                break
            why = " ".join(([("These user_text values are not copied exactly from the note lines: " + "; ".join(f'"{b}"' for b in bad))] if bad else [])
                           + ([("These ai_text values state figures that are not in the transcript or the notes: "
                                + "; ".join(f'"{b}"' for b in made_up))] if made_up else []))
        prompt += f"\n\nYour previous reply was rejected. {why} Call write_summary again."
    for x in sections:  # a figure still not in the transcript or the notes: the addition goes (spec 7.4's rule, decision 152)
        for i in x["items"]:
            if not figures_in_sources(i["ai_text"], sources):
                i["ai_text"] = ""
    avail = Counter(lines)
    for x in sections:  # lines a valid item already carries
        for i in x["items"]:
            if i["user_text"] and _verbatim(i["user_text"], lines):
                i["user_text"] = "\n".join(_clean(i["user_text"]))
                avail.subtract(_clean(i["user_text"]))
    for x in sections:  # still paraphrased: the closest line nothing else carries stands in, without Claude's addition
        for i in x["items"]:
            if i["user_text"] and not _verbatim(i["user_text"], lines):
                near = difflib.get_close_matches(i["user_text"], list(avail.elements()), 1, 0)
                i["user_text"], i["ai_text"] = (near[0] if near else ""), ""
                avail.subtract(near)
    sections = [x for x in ({"heading": x["heading"], "items": [i for i in x["items"] if i["user_text"] or i["ai_text"]]} for x in sections) if x["items"]]
    missing = []  # coverage guard: each line as often as it was written
    for ln, n in avail.items():
        missing += [{"user_text": ln, "ai_text": ""}] * max(n, 0)
    missing.sort(key=lambda m: lines.index(m["user_text"]))
    if missing:
        kp = next((x for x in sections if x["heading"] == "Key points"), None)
        if kp is None:
            sections.insert(0, kp := {"heading": "Key points", "items": []})
        kp["items"] += missing
    version = (s.scalar(select(Summary.version).where(Summary.meeting_id == meeting_id).order_by(Summary.version.desc())) or 0) + 1
    s.add(Summary(meeting_id=meeting_id, version=version, items=sections))
    s.commit()


def _row_count(s: Session, mid: int) -> int:
    return sum(s.scalar(select(func.count()).select_from(t).where(t.meeting_id == mid)) for t in (Segment, AudioChunk, Assist))


@job("process_meeting")
def process_meeting(s: Session, payload: dict) -> None:
    """Idempotent: only a meeting still `processing` is worked on. A step error ends the meeting as `failed` (no job retry)."""
    m = s.get(Meeting, payload["meeting_id"])
    if m is None or m.status != "processing":
        return
    mid = m.id
    rows = _row_count(s, mid)
    try:
        assemble_recording(s, mid)
        s.commit()
        compute_used_markers(s, mid)
        store_transcript(s, mid)  # after the markers: it carries used / used_by
        summarise(s, mid)
        if _row_count(s, mid) != rows:  # rows landed while this ran: once more, with them (decision 153)
            s.commit()
            enqueue(s, "process_meeting", {"meeting_id": mid}, func.now() + LATE_GRACE)
            return
        s.get(Meeting, mid).status = "ready"
        s.commit()
        enqueue(s, "index_meeting", {"meeting_id": mid})  # its own job: a slow Ollama must not fail the meeting
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
    try:
        size = mc.stat_object(BUCKET, key).size
    except S3Error:
        raise HTTPException(404, "recording file is missing")
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
