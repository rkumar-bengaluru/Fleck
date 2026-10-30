"""Replay a WAV through the real pipeline and print the four trigger counts (spec 7.2) and per-question rows.

Usage: uv run python scripts/measure.py --wav fixtures/conversation/conversation.wav --reference fixtures/conversation/reference.json
Latency columns stay "n/a" until answers exist (Task 13)."""
import argparse
import json
import time
from pathlib import Path

import re

from fleck_core.numbers import normalize_numbers
from fleck_helper.audio import ReplaySource
from fleck_helper.pipeline import Pipeline, Trigger

MATCH_MS = 1500  # a trigger belongs to the client turn whose end is nearest, if within this
MIN_WORD_OVERLAP = 0.5  # ...and, for a scripted question, if it holds at least this share of the question's words


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", normalize_numbers(text).lower()))


def _matches(trigger, turn) -> bool:
    if abs(turn["end_ms"] - trigger.end_ms) > MATCH_MS:
        return False
    q = _words(turn["text"])
    return not turn["question_id"] or len(q & _words(trigger.question)) >= MIN_WORD_OVERLAP * len(q)


def replay(wav: Path, transcriber=None, embed=None) -> list:
    """`embed` defaults to the live 300 ms path, where a slow embed hands the decision to the rules."""
    return list(Pipeline("measure", transcriber, embed=embed).run(ReplaySource(wav, realtime=False)))


def counts(events: list, ref: dict) -> dict:
    """The four named counts; None where the reference has nothing to count (no categories, no questions)."""
    client = [t for t in ref["turns"] if t["speaker"] == "client"]
    rows = []
    for e in (e for e in events if isinstance(e, Trigger)):
        turn = min(client, key=lambda t: abs(t["end_ms"] - e.end_ms))
        rows.append((e, turn if _matches(e, turn) else None))  # unmatched counts as a false trigger
    cats = any("category" in t for t in client)
    hit = {t["question_id"] for _, t in rows if t and t["question_id"]}
    return {
        "negative_set_short": sum(bool(t) and t.get("category") == "short" for _, t in rows) if cats else None,
        "negative_set_medium": sum(bool(t) and t.get("category") in ("medium", "logistics") for _, t in rows) if cats else None,
        "conversation_false_triggers": [e.question for e, t in rows if not (t and t["question_id"])],
        "conversation_recall": (len(hit), len(ref["questions"])),
        "triggered": {q["id"]: next((e for e, t in rows if t and t["question_id"] == q["id"]), None) for q in ref["questions"]},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", type=Path, required=True)
    ap.add_argument("--reference", type=Path, required=True)
    a = ap.parse_args()
    ref = json.loads(a.reference.read_text())
    t = time.monotonic()
    events = replay(a.wav)
    c = counts(events, ref)
    trig = [e for e in events if isinstance(e, Trigger)]
    print(f"triggers decided by the rules fallback (embedder failed or over the 300 ms budget): {sum(e.embedding is None for e in trig)}/{len(trig)}")
    hit, total = c["conversation_recall"]
    print(f"{a.wav.name}: {ref['duration_ms'] / 1000:.1f} s audio, replayed in {time.monotonic() - t:.1f} s")
    for name in ("negative_set_short", "negative_set_medium"):
        print(f"{name}: {'n/a' if c[name] is None else c[name]}")
    print(f"conversation_false_triggers: {len(c['conversation_false_triggers'])} {c['conversation_false_triggers']}")
    print(f"conversation_recall: {f'{hit}/{total}' if total else 'n/a'}")
    if total:
        print("question      triggered  trigger_end_ms  first_words_ms  full_ms")
        for q in ref["questions"]:
            e = c["triggered"][q["id"]]
            print(f"{q['id']:<13} {'yes' if e else 'NO':<10} {e.end_ms if e else '-':<15} {'n/a':<15} n/a")


if __name__ == "__main__":
    main()
