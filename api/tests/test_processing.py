import io
import json
import subprocess
import wave
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update

from fleck_api import chat, processing
from fleck_api.claude import Message
from fleck_api.jobs import run_once
from fleck_api.meetings import BUCKET
from fleck_api.library import _minio
from fleck_api.realtime import hub
from fleck_api.models import Assist, Job, Meeting, Note, Segment, Summary
from test_meetings import pair
from test_realtime import helper_of, new_meeting
from test_auth import env  # noqa: F401  (fixture)

ROOT = Path(__file__).parents[2]


def wav(seconds=1.0, rate=16000, level=3000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes((level.to_bytes(2, "little", signed=True) * 2) * int(rate * seconds))
    return buf.getvalue()


def jobs_of(s, mid):
    return list(s.scalars(select(Job).where(Job.kind == "process_meeting", Job.payload["meeting_id"].as_integer() == mid)))


def test_stop_idempotent_and_offline_helper(env):
    client, _, make = env
    auth = pair(env)
    offline = new_meeting(client, auth)
    for _ in range(2):
        assert client.post(f"/api/meetings/{offline}/stop").status_code == 204
    with make() as s:
        assert s.get(Meeting, offline).status == "processing"
        [j] = jobs_of(s, offline)  # one job, however often Stop is pressed
        assert s.scalar(select(Job.run_after <= func.now()).where(Job.id == j.id)) is True  # helper offline: no grace
    online = new_meeting(client, auth)
    with helper_of(client, auth) as helper:
        for _ in range(2):
            assert client.post(f"/api/meetings/{online}/stop").status_code == 204
        assert helper.receive_json() == {"type": "stop", "meeting_id": str(online)}
    with make() as s:
        [j] = jobs_of(s, online)
        assert s.scalar(select(Job.run_after > func.now() + text("interval '10 seconds'")).where(Job.id == j.id)) is True  # 15 s grace for late chunks


def probe(path, *entries):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", f"stream={','.join(entries)}:format=duration", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_recording_assembled_with_gap_filled(env, tmp_path):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    for seq in (0, 1, 3):  # chunk 2 never arrived
        assert client.put(f"/api/meetings/{mid}/audio/{seq}", content=wav(1.0), headers=auth).status_code == 204
    client.post(f"/api/meetings/{mid}/stop")
    with make() as s:
        processing.assemble_recording(s, mid)
        s.commit()
        key = s.get(Meeting, mid).recording_key
    assert key == f"recordings/{mid}.m4a"
    _minio().fget_object(BUCKET, key, str(tmp_path / "r.m4a"))
    info = probe(str(tmp_path / "r.m4a"), "codec_name", "channels")
    assert info["streams"][0]["codec_name"] == "aac" and info["streams"][0]["channels"] == 2
    assert 3.8 < float(info["format"]["duration"]) < 4.4  # 3 chunks + 1 s of silence
    # Range support
    r = client.get(f"/api/meetings/{mid}/recording", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-range"].startswith("bytes 0-99/")
    assert client.get(f"/api/meetings/{mid}/recording").status_code == 200


def test_process_meeting_ready_and_failed(env, monkeypatch):
    client, _, make = env
    auth = pair(env)
    ok, bad = new_meeting(client, auth), new_meeting(client, auth)
    client.put(f"/api/meetings/{ok}/audio/0", content=wav(1.0), headers=auth)
    for m in (ok, bad):
        client.post(f"/api/meetings/{m}/stop")
    monkeypatch.setattr(processing, "compute_used_markers", lambda s, m: None)
    monkeypatch.setattr(processing, "summarise", lambda s, m: (_ for _ in ()).throw(RuntimeError("no key")) if m == bad else None)
    with make() as s:
        while run_once(s):
            pass
        assert s.get(Meeting, ok).status == "ready" and s.get(Meeting, ok).recording_key
        s.refresh(s.get(Meeting, bad))
        assert s.get(Meeting, bad).status == "failed" and "no key" in s.get(Meeting, bad).failure_reason


class Fake:
    """Embeds a text as a one-hot over its words, so equal wording scores 1."""
    WORDS = ["thirty", "minutes", "team", "weeks", "code", "owner", "five", "days"]

    def embed(self, texts):
        return [[float(w in t.lower()) for w in self.WORDS] + [0.01] for t in texts]


def _seed(make, mid=None):
    with make() as s:
        m = Meeting(title="t", status="processing")
        s.add(m)
        s.flush()
        s.add_all([Segment(meeting_id=m.id, segment_id="c1", stream="client", start_ms=0, end_ms=1000, text="How fast?"),
                   Segment(meeting_id=m.id, segment_id="y-early", stream="you", start_ms=500, end_ms=900, text="thirty minutes"),  # before the card
                   Segment(meeting_id=m.id, segment_id="y1", stream="you", start_ms=6000, end_ms=8000, text="We respond in thirty minutes."),
                   Segment(meeting_id=m.id, segment_id="y-over", stream="you", start_ms=1500, end_ms=3500, text="five days"),  # starts as the card shows (2000 ms)
                   Segment(meeting_id=m.id, segment_id="c2", stream="client", start_ms=30000, end_ms=31000, text="And the timeline?"),
                   Segment(meeting_id=m.id, segment_id="y-mono", stream="you", start_ms=10000, end_ms=40000, text="five days"),  # a monologue 20 s older than the question
                   Segment(meeting_id=m.id, segment_id="y-late", stream="you", start_ms=30000, end_ms=31000, text="team weeks")])
        s.add_all([Assist(meeting_id=m.id, assist_id="a-auto", segment_id="c1", question="q", answer="thirty minutes", first_words_ms=1000),
                   Assist(meeting_id=m.id, assist_id="a-miss", segment_id="c1", question="q", answer="team weeks code", first_words_ms=1000),
                   Assist(meeting_id=m.id, assist_id="a-over", segment_id="c1", question="q", answer="five days", first_words_ms=1000),
                   Assist(meeting_id=m.id, assist_id="a-mono", segment_id="c2", question="q", answer="five days", first_words_ms=1000),
                   Assist(meeting_id=m.id, assist_id="a-manual", segment_id="c1", question="q", answer="thirty minutes", first_words_ms=1000,
                          used=False, used_by="manual")])
        s.commit()
        return m.id


def test_manual_used_not_overwritten(make_session):
    mid = _seed(make_session)
    with make_session() as s:
        processing.compute_used_markers(s, mid, Fake())
        a = {x.assist_id: x for x in s.scalars(select(Assist).where(Assist.meeting_id == mid))}
    assert (a["a-auto"].used, a["a-auto"].used_by) == (True, "auto") and a["a-auto"].used_score > 0.9
    assert (a["a-miss"].used, a["a-miss"].used_by) == (False, "auto")  # the only match is 30 s after the card
    assert (a["a-manual"].used, a["a-manual"].used_by) == (False, "manual")
    assert a["a-over"].used is True  # a You final that overlaps the window counts, though it started before the card showed
    assert a["a-mono"].used is False  # a line that began well before the question is not an answer to it


NOTES = "Asked for SLA in writing\nSend the rate card\n\nKickoff in May"


def _summary_env(make, monkeypatch, replies, notes=NOTES):
    with make() as s:
        m = Meeting(title="t", status="processing")
        s.add(m)
        s.flush()
        s.add(Note(meeting_id=m.id, text=notes))
        s.add(Segment(meeting_id=m.id, segment_id="c1", stream="client", start_ms=0, end_ms=1000, text="What is the SLA?"))
        s.commit()
        mid = m.id
    calls = []

    def fake(purpose, messages, model, meeting_id=None, **kw):
        calls.append((purpose, model, messages))
        r = replies[min(len(calls), len(replies)) - 1]
        return r if isinstance(r, Message) else Message(text="", tool_input=r, input_tokens=1, output_tokens=1, model=model)
    monkeypatch.setattr(processing, "claude_call", fake)
    return mid, calls


def _sections(make, mid):
    with make() as s:
        return {x["heading"]: x["items"] for x in s.scalar(select(Summary.items).where(Summary.meeting_id == mid))}


def test_summary_keeps_user_text_verbatim(make_session, monkeypatch):
    para = {"sections": [{"heading": "Key points", "items": [{"user_text": "SLA requested in writing", "ai_text": "Client wants the SLA."},
                                                              {"user_text": "Send the rate card", "ai_text": "Rate card to follow."}]}]}
    mid, calls = _summary_env(make_session, monkeypatch, [para, para])
    with make_session() as s:
        processing.summarise(s, mid)
    assert len(calls) == 2  # one retry
    kp = _sections(make_session, mid)["Key points"]
    assert {"user_text": "Asked for SLA in writing", "ai_text": ""} in kp  # the paraphrase fell back to the original line
    assert {"user_text": "Send the rate card", "ai_text": "Rate card to follow."} in kp
    assert all(i["user_text"] in NOTES for items in _sections(make_session, mid).values() for i in items if i["user_text"])


def test_summary_includes_every_note_line(make_session, monkeypatch):
    reply = {"sections": [{"heading": "Next steps", "items": [{"user_text": "Send the rate card", "ai_text": "Send it."},
                                                              {"user_text": "Kickoff in May", "ai_text": ""}, {"user_text": "", "ai_text": "Inferred."}]}]}
    mid, calls = _summary_env(make_session, monkeypatch, [reply])
    with make_session() as s:
        processing.summarise(s, mid)
    assert len(calls) == 1
    sec = _sections(make_session, mid)
    assert sec["Key points"] == [{"user_text": "Asked for SLA in writing", "ai_text": ""}]  # omitted by Claude, appended
    assert {"user_text": "", "ai_text": "Inferred."} in sec["Next steps"]


@pytest.mark.ollama
def test_used_markers_match_script(make_session):
    """The stored segments and assists of one full replay of conversation.wav (fixtures/conversation/replayed_meeting.json;
    q_general's answer is a real Haiku reply, the replay having run without a key) against reference.json."""
    fx = json.loads((ROOT / "fixtures/conversation/replayed_meeting.json").read_text())
    ref = json.loads((ROOT / "fixtures/conversation/reference.json").read_text())["questions"]
    with make_session() as s:
        m = Meeting(title="replayed", status="processing")
        s.add(m)
        s.flush()
        s.add_all(Segment(meeting_id=m.id, **x) for x in fx["segments"])
        s.add_all(Assist(meeting_id=m.id, **x) for x in fx["assists"])
        s.commit()
        processing.compute_used_markers(s, m.id)
        seg = {x.segment_id: x for x in s.scalars(select(Segment).where(Segment.meeting_id == m.id))}
        got = {}
        for a in s.scalars(select(Assist).where(Assist.meeting_id == m.id)):
            q = seg[a.segment_id]  # the reference question ends inside the turn this card answered
            got[next(r["id"] for r in ref if q.start_ms <= r["end_ms"] <= q.end_ms)] = (a.used, round(a.used_score, 3))
    print("\n".join(f"{r['id']:10} expect {r['used']!s:5} got {got[r['id']]}" for r in ref))
    assert {r["id"]: got[r["id"]][0] for r in ref} == {r["id"]: r["used"] for r in ref}


def _items(*texts):
    return {"sections": [{"heading": "Next steps", "items": [{"user_text": t, "ai_text": ""} for t in texts]}]}


def _all_user_text(make, mid):
    return sorted(i["user_text"] for items in _sections(make, mid).values() for i in items if i["user_text"])


def test_coverage_counts_whole_lines_and_duplicates(make_session, monkeypatch):
    notes = "Pricing\nPricing for phase 2\nFollow up\nFollow up"
    mid, _ = _summary_env(make_session, monkeypatch, [_items("Pricing for phase 2", "Follow up")], notes)
    with make_session() as s:
        processing.summarise(s, mid)
    assert _all_user_text(make_session, mid) == sorted(["Pricing", "Pricing for phase 2", "Follow up", "Follow up"])


def test_bullets_do_not_double_add(make_session, monkeypatch):
    mid, _ = _summary_env(make_session, monkeypatch, [_items("Send card", "Call Dana")], "- Send card\n* Call Dana")
    with make_session() as s:
        processing.summarise(s, mid)
    assert _all_user_text(make_session, mid) == ["Call Dana", "Send card"]


def test_summary_retries_after_text_only_or_malformed_reply(make_session, monkeypatch):
    good = _items("Asked for SLA in writing", "Send the rate card", "Kickoff in May")
    for first in (Message("I will summarise...", None, 1, 1, "m"), {"sections": [{"heading": "Next steps", "items": [{"user_text": "x"}]}]}):
        mid, calls = _summary_env(make_session, monkeypatch, [first, good])
        with make_session() as s:
            processing.summarise(s, mid)
        assert len(calls) == 2 and "rejected" in calls[1][2][0]["content"] and "Next steps" in _sections(make_session, mid)


def test_summary_falls_back_to_note_lines_and_retry_names_bad_lines(make_session, monkeypatch):
    mid, calls = _summary_env(make_session, monkeypatch, [Message("", None, 1, 1, "m", "max_tokens"), {"sections": "nope"}])
    with make_session() as s:
        processing.summarise(s, mid)
    assert "token limit" in calls[1][2][0]["content"]
    assert _sections(make_session, mid) == {"Key points": [{"user_text": ln, "ai_text": ""} for ln in NOTES.splitlines() if ln]}
    mid, calls = _summary_env(make_session, monkeypatch, [_items("Paraphrase of the SLA note")])
    with make_session() as s:
        processing.summarise(s, mid)
    assert '"Paraphrase of the SLA note"' in calls[1][2][0]["content"]


def test_summary_drops_an_addition_whose_figure_is_not_in_the_transcript_or_notes(make_session, monkeypatch):
    def reply(ai):
        return {"sections": [{"heading": "Next steps", "items": [{"user_text": "Send the rate card", "ai_text": ai},
                                                                 {"user_text": "Kickoff in May", "ai_text": "Kickoff is in May."}]}]}
    made_up = reply("Send a revised quote at USD 12,000/month.")
    mid, calls = _summary_env(make_session, monkeypatch, [made_up, made_up])
    with make_session() as s:
        processing.summarise(s, mid)
    assert len(calls) == 2 and "12,000" in calls[1][2][0]["content"] and "figures" in calls[1][2][0]["content"]
    assert _sections(make_session, mid)["Next steps"][:2] == [{"user_text": "Send the rate card", "ai_text": ""},
                                                             {"user_text": "Kickoff in May", "ai_text": "Kickoff is in May."}]
    mid, calls = _summary_env(make_session, monkeypatch, [made_up, reply("Send the card with the 99.9% SLA.")])
    with make_session() as s:
        s.add(Segment(meeting_id=mid, segment_id="c2", stream="client", start_ms=2000, end_ms=3000, text="We need 99.9 percent."))
        s.commit()
        processing.summarise(s, mid)
    assert _sections(make_session, mid)["Next steps"][0]["ai_text"] == "Send the card with the 99.9% SLA."  # the retry's figure is said


def test_a_gap_marker_is_labelled_fleck_not_client(make_session, monkeypatch):
    mid, calls = _summary_env(make_session, monkeypatch, [_items("Asked for SLA in writing", "Send the rate card", "Kickoff in May")])
    with make_session() as s:
        s.add(Segment(meeting_id=mid, segment_id="gap-5000", stream="client", start_ms=5000, end_ms=9000,
                      text="Audio from the call was interrupted (0:05–0:09)"))
        s.commit()
        processing.summarise(s, mid)
        rows = chat.meeting_rows(s, s.get(Meeting, mid))
    prompt = calls[0][2][0]["content"]
    assert "Client: What is the SLA?" in prompt and "Fleck: Audio from the call was interrupted" in prompt
    assert "Fleck: Audio from the call was interrupted" in rows[0]["text"]


def test_stop_race_enqueues_once(env, monkeypatch):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)

    async def other_stop_wins(user_id, data):  # another Stop commits while this one awaits the helper
        with make() as s:
            s.execute(update(Meeting).where(Meeting.id == mid).values(status="processing"))
            s.commit()
        return True
    monkeypatch.setattr(hub, "to_helper", other_stop_wins)
    assert client.post(f"/api/meetings/{mid}/stop").status_code == 204
    with make() as s:
        assert jobs_of(s, mid) == []  # the winner (not simulated here) is the one that enqueues


def test_recording_ranges_and_missing_file(env, tmp_path):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    assert client.get(f"/api/meetings/{mid}/recording").status_code == 404
    client.put(f"/api/meetings/{mid}/audio/0", content=wav(1.0), headers=auth)
    client.post(f"/api/meetings/{mid}/stop")
    with make() as s:
        processing.assemble_recording(s, mid)
        s.commit()
    full = client.get(f"/api/meetings/{mid}/recording").content
    n = len(full)
    r = client.get(f"/api/meetings/{mid}/recording", headers={"Range": "bytes=-10"})
    assert r.status_code == 206 and r.content == full[-10:] and r.headers["content-range"] == f"bytes {n - 10}-{n - 1}/{n}"
    r = client.get(f"/api/meetings/{mid}/recording", headers={"Range": f"bytes={n - 5}-{n + 999}"})
    assert r.status_code == 206 and r.content == full[-5:]
    for bad in ("bytes=5-3", f"bytes={n}-", "bytes=-0", "bytes=abc"):
        r = client.get(f"/api/meetings/{mid}/recording", headers={"Range": bad})
        assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{n}", bad
    with make() as s:
        s.get(Meeting, mid).recording_key = "recordings/gone.m4a"
        s.commit()
    assert client.get(f"/api/meetings/{mid}/recording").status_code == 404


def test_audio_seq_past_four_hours_fails_the_meeting(env):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    client.put(f"/api/meetings/{mid}/audio/0", content=wav(1.0), headers=auth)
    client.put(f"/api/meetings/{mid}/audio/{processing.MAX_CHUNKS}", content=wav(1.0), headers=auth)
    client.post(f"/api/meetings/{mid}/stop")
    with make() as s:
        while run_once(s):
            pass
        s.expire_all()
        m = s.get(Meeting, mid)
        assert m.status == "failed" and "4 hour" in m.failure_reason
