"""End-to-end latency (spec 7.1): replay the test conversation in real time through Pipeline + Assistant and time,
per client question, the question's end -> first words shown, and -> done. Gates (spec 5, decision 112), p95 pooled
over every question of --runs replays (one run's p95 of 8 is just its slowest question): library and brief answers
first words <= 3.0 s and done <= 6.0 s; Claude fallback answers first words <= 5.0 s; "Not in our material" is
reported, not gated.

Real Whisper, the live classifier, Weaviate (8080), qwen3:4b (11434) and the Claude fallback (ANTHROPIC_API_KEY from
the environment or .env; --fallback off without it). Candidates start search + prefill early (decision 103). A
question with no words shown ("Not in our material") counts at done, when that label appears. Each question's
outcome is checked against spec 8 (script.yaml `expect`; q_liability goes to the fallback, decision 101).
Each Claude call prints when it started and when its first text came, from the question's end. Takes the WAV's
length (~6.4 min) per run.
Usage: uv run --all-packages python scripts/measure_e2e.py [--runs 3]   (exit 1 on a latency FAIL or a wrong outcome)"""
import os
import argparse
import re
import json
import statistics as st
import sys
import time
from pathlib import Path

import yaml
from measure import counts

from fleck_api.library import CLIENT_NAMES
from fleck_core.answer import OllamaClient
from fleck_core.retrieval import Embedder, Retriever
from fleck_helper.assist import Assistant
from fleck_helper.audio import ReplaySource
from fleck_helper.pipeline import Candidate, Pipeline, Trigger

ROOT = Path(__file__).parents[1]
CLIENTS = [CLIENT_NAMES.get(d.name, d.name.replace("-", " ").title()) for d in sorted((ROOT / "clients").iterdir())
           if d.is_dir() and not d.name.startswith("_") and d.name != "jf"]
GATES_S = {"library": {"first": 3.0, "done": 6.0}, "claude": {"first": 5.0}, "not_in_material": {}}
KNOWN_LIMITS = {"q_liability": {"general", "reference"}}  # decision 101: qwen3:4b replies NONE, the fallback answers
PRICE = re.compile(r"\d|[$€£₹]|\b(USD|INR|EUR|GBP)\b")  # q_price may show no figure of any kind


def _load_env() -> None:
    """The keys the live path reads, from .env when the environment lacks them (the api gets them from compose)."""
    env = ROOT / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        k, _, v = line.partition("=")
        if k in ("ANTHROPIC_API_KEY", "FLECK_CLAUDE_FAST_MODEL") and v.strip() and not os.environ.get(k):
            os.environ[k] = v.strip()


class _Clocked:
    """The source, noting the wall time of t_ms 0 (frames are paced in real time from the first one)."""
    def __init__(self, src):
        self.src, self.start_wall = src, None

    def frames(self):
        for f in self.src.frames():
            if self.start_wall is None:
                self.start_wall = time.time() - f.t_ms / 1000
            yield f


def _recorder(trig, start_wall, out: dict):
    """A `post` for Assistant.start that times one Trigger's events. A row left without done was superseded."""
    q_end = start_wall + trig.end_ms / 1000
    row = out[trig.segment_id] = {"first": None, "done": None, "kind": None, "text": "", "seen": "", "llm": []}

    def post(e):
        now_ms = round((time.time() - q_end) * 1000)
        if e.phase in ("delta", "sentence") and e.text and row["first"] is None:
            row["first"] = now_ms
        if e.phase == "sentence":
            row["text"] += e.text + " "
        row["seen"] += e.text + " " if e.phase in ("delta", "sentence") else ""  # all ever on screen
        if e.phase == "done":
            row.update(done=now_ms, kind=e.kind, rejected=e.rejected, llm=e.llm)
            row["first"] = row["first"] if row["first"] is not None else now_ms  # "Not in our material" appears now
    return post


def pct(xs):
    xs = sorted(xs)
    return (st.median(xs), xs[min(len(xs) - 1, int(0.95 * len(xs)))]) if xs else (None, None)


def _class(r) -> str:
    return {"general": "claude", "not_in_material": "not_in_material"}.get(r["kind"], "library")


def run(a, ref, expect) -> tuple[list[tuple[str, int, int]], list[str]]:
    """One real-time replay; prints its table. ((class, first ms, done ms) per question, ids of wrong outcomes)."""
    retriever = Retriever("http://localhost:8080", Embedder("http://localhost:11434"))
    assistant = Assistant(retriever, OllamaClient(), a.fallback, CLIENTS, [])
    src, pipe = _Clocked(ReplaySource(a.wav, realtime=True)), Pipeline("e2e")
    results, threads, events, candidates = {}, [], [], 0
    for e in pipe.run(src):
        events.append(e)
        if isinstance(e, Candidate):
            candidates += 1
            assistant.prepare(e.text, e.embedding)
        elif isinstance(e, Trigger):  # handle() here on the loop thread, its events on a worker: the helper's pattern
            threads.append(assistant.start(e, "e2e", _recorder(e, src.start_wall, results), src.start_wall))
    for th in threads:
        th.join(timeout=30)
    c = counts(events, ref)
    hit, total = c["conversation_recall"]
    print(f"{a.wav.name}: recall {hit}/{total}, false triggers {len(c['conversation_false_triggers'])} "
          f"{c['conversation_false_triggers']}, candidates {candidates}, rules fallbacks {pipe.fallbacks}")
    print(f"{'question':<13} {'first_words_ms':>14} {'done_ms':>8}  outcome  kind  rejected  shown")
    rows, wrong = [], []
    for q in ref["questions"]:
        t = c["triggered"][q["id"]]
        r = results.get(t.segment_id) if t else None
        if not r or r["done"] is None:
            print(f"{q['id']:<13} {'-':>14} {'-':>8}  {'not triggered' if not t else 'superseded or no done'}")
            wrong.append(q["id"])
            continue
        rows.append((_class(r), r["first"], r["done"]))
        # without Claude, general answers end in not_in_material: only the library outcomes are checked
        want = KNOWN_LIMITS.get(q["id"], {expect[q["id"]]})
        ok_kind = r["kind"] in want or a.fallback == "off" and r["kind"] == "not_in_material" and want & {"general"}
        if expect[q["id"]] == "not_in_material" and PRICE.search(r["seen"]):
            ok_kind = False  # spec 7.4: no figure may be shown for the unbacked price question
        wrong += [] if ok_kind else [q["id"]]
        print(f"{q['id']:<13} {r['first']:>14} {r['done']:>8}  {'PASS' if ok_kind else 'FAIL':<7}  {r['kind']} "
              f"{r.get('rejected') or ''} claude={[c.status for c in r['llm']]} {r['text'][:90]!r}\n{'':<13} asked: {t.question!r}")
        for call in r["llm"]:
            print(f"{'':<13} claude {call.status}{' speculative' if call.speculative else ''}: started "
                  f"{call.started_at} ms, first text {call.first_text_at} ms (from the question's end)")
    for seg, r in results.items():
        if not any(t and t.segment_id == seg for t in c["triggered"].values()):
            print(f"(false trigger {seg}: first {r['first']} done {r['done']} kind {r['kind']})")
    for cls in GATES_S:
        mine = [x for x in rows if x[0] == cls]
        if mine:
            print(f"this run, {cls}: first words max {max(f for _, f, _ in mine)} ms, done max {max(d for *_, d in mine)} ms"
                  f" over {len(mine)}")
    print(f"spec 8 outcomes: {'all as expected' if not wrong else 'WRONG ' + ', '.join(wrong)}")
    return rows, wrong


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", type=Path, default=ROOT / "fixtures/conversation/conversation.wav")
    ap.add_argument("--reference", type=Path, default=ROOT / "fixtures/conversation/reference.json")
    ap.add_argument("--script", type=Path, default=ROOT / "fixtures/conversation/script.yaml")
    ap.add_argument("--fallback", choices=("claude", "off"), default="claude")
    ap.add_argument("--runs", type=int, default=1, help="replays, one after another; the gate is on all pooled")
    a = ap.parse_args()
    _load_env()
    if a.fallback == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is empty: set it in .env or run with --fallback off")
    ref = json.loads(a.reference.read_text())
    expect = {t["q"]: t["expect"] for t in yaml.safe_load(a.script.read_text())["turns"] if t.get("q")}
    runs, wrong = [], []
    for i in range(a.runs):
        print(f"--- run {i + 1}/{a.runs}")
        rows, w = run(a, ref, expect)
        runs.append(rows)
        wrong += [f"{q} (run {i + 1})" for q in w]
    ok = sum(map(len, runs)) == a.runs * len(ref["questions"])  # a question without done fails the run
    for cls, gate in GATES_S.items():
        for what, col in (("first", 1), ("done", 2)):
            xs = [x[col] for rows in runs for x in rows if x[0] == cls]
            if not xs:
                continue
            (p50, p95), lim = pct(xs), gate.get(what)
            ok &= lim is None or p95 <= lim * 1000
            maxima = [max((x[col] for x in rows if x[0] == cls), default=None) for rows in runs]
            print(f"pooled {cls} {what}: p50 {p50} ms p95 {p95} ms over {len(xs)}; per-run max {maxima} -> "
                  + ("reported, not gated" if lim is None else f"{'PASS' if p95 <= lim * 1000 else 'FAIL'} (target {lim:.1f} s)"))
    print(f"spec 8 outcomes: {'all as expected' if not wrong else 'WRONG ' + ', '.join(wrong)}")
    return 0 if ok and not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
