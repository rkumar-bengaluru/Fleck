"""60-minute soak (spec 7.7, Task 22): conversation.wav looped through `fleck-helper replay --loops N` into one meeting on
the running stack, in real time. Samples the helper's RSS every 30 s and swap and thermal state every minute.
Pass: p95 first words (library and brief answers, on time) in the last 10 minutes within 20% of the first 10 minutes;
helper RSS growth < 300 MB; every scripted question in every loop triggered with its expected outcome; the classifier's
breaker never opened and the rules fallback never decided (Milestone E and F follow-ups). Late answers are counted apart.
Writes web/test-results/soak/ (helper log, samples, per-loop rows) and prints a summary.
Usage: uv run --all-packages python scripts/soak.py --minutes 60   (exit 1 on a FAIL)"""
import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from stack_replay import LOOP_MS, REF, ROOT, create_meeting, helper_env, log_counts, pct, print_rows, score, sign_in, \
    start_replay, stop, wait_ready

OUT = ROOT / "web/test-results/soak"


def tree_rss_mb(pid: int) -> float:
    """RSS of `pid` and its descendants (uv run -> python), MB."""
    rows = [ln.split() for ln in subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True).stdout.splitlines()]
    kids, rss = {}, {}
    for p, pp, r in rows:
        kids.setdefault(int(pp), []).append(int(p))
        rss[int(p)] = int(r)
    todo, total = [pid], 0
    while todo:
        p = todo.pop()
        total += rss.get(p, 0)
        todo += kids.get(p, [])
    return total / 1024


def swap_mb() -> float:
    m = re.search(r"used = ([\d.]+)M", subprocess.run(["sysctl", "vm.swapusage"], capture_output=True, text=True).stdout)
    return float(m.group(1)) if m else -1


def thermal() -> str:
    out = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True).stdout
    levels = re.findall(r"(\w+_Speed_Limit|Thermal Warning Level|Performance Warning Level|CPU_Scheduler_Limit)\s*=\s*(\d+)", out)
    return ", ".join(f"{k}={v}" for k, v in levels) or ("no warning" if "No thermal warning level has been recorded" in out else out.strip()[:120])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    a = ap.parse_args()
    loops = math.ceil(a.minutes * 60_000 / LOOP_MS)
    OUT.mkdir(parents=True, exist_ok=True)
    c = sign_in()
    env = helper_env(c)
    mid = create_meeting(c, f"Task 22 soak {a.minutes:.0f} min {time.strftime('%H%M')}")
    log = OUT / "helper-soak.log"
    print(f"soak: meeting {mid}, {loops} loops of {LOOP_MS / 1000:.1f} s, log {log}", flush=True)
    p = start_replay(env, mid, log, loops)
    t0, samples, last_minute = time.time(), [], -1
    while p.poll() is None:
        s = {"t_s": round(time.time() - t0), "rss_mb": round(tree_rss_mb(p.pid), 1)}
        if (minute := s["t_s"] // 60) != last_minute:
            last_minute, s["swap_mb"], s["thermal"] = minute, swap_mb(), thermal()
        samples.append(s)
        (OUT / "samples.json").write_text(json.dumps(samples, indent=1))
        print(json.dumps(s), flush=True)
        for _ in range(30):
            if p.poll() is not None:
                break
            time.sleep(1)
    code = p.returncode
    stop(c, mid)
    m = wait_ready(c, mid, timeout=1800)
    rows, extra = score(m, loops)
    (OUT / "rows.json").write_text(json.dumps(rows, indent=1))
    counts = log_counts(log.read_text())
    print_rows(rows, extra)

    def at_ms(r):
        return r["loop"] * LOOP_MS + next(q["end_ms"] for q in REF["questions"] if q["id"] == r["id"])
    end_ms = loops * LOOP_MS
    on_time = [r for r in rows if r["triggered"] and not r["late"] and r["first"] is not None]
    lib = [r for r in on_time if r["cls"] == "library"]
    first10 = [r["first"] for r in lib if at_ms(r) < 600_000]
    last10 = [r["first"] for r in lib if at_ms(r) >= end_ms - 600_000]
    p_first, p_last = pct(first10)[1], pct(last10)[1]
    rss = [s["rss_mb"] for s in samples if s["rss_mb"] > 0]
    # growth from the first sample after warm-up (Whisper and the classifier loaded) to the peak
    base = rss[min(2, len(rss) - 1)] if rss else 0
    growth = (max(rss) - base) if rss else float("nan")
    swaps = [s["swap_mb"] for s in samples if "swap_mb" in s]
    thermals = sorted({s["thermal"] for s in samples if "thermal" in s})
    missed = [f"loop {r['loop']} {r['id']}" for r in rows if not r["triggered"]]
    wrong = [f"loop {r['loop']} {r['id']} ({r['kind']})" for r in rows if r["triggered"] and not r["ok"]]
    late = [f"loop {r['loop']} {r['id']} first {r['first']} ms" for r in rows if r.get("late")]
    checks = {
        "replay exited 0": code == 0,
        f"p95 first words last 10 min {p_last} ms within 20% of first 10 min {p_first} ms":
            p_first is not None and p_last is not None and p_last <= 1.2 * p_first,
        f"helper RSS growth {growth:.0f} MB < 300 MB (from {base:.0f} MB to peak {max(rss, default=0):.0f} MB)": growth < 300,
        f"every question in every loop ({len(rows)}) triggered: missed {missed or 'none'}": not missed,
        f"every outcome as expected: wrong {wrong or 'none'}": not wrong,
        f"breaker never opened ({counts['breaker_opened']})": counts["breaker_opened"] == 0,
        f"rules fallback never decided ({counts['rules_fallbacks']})": counts["rules_fallbacks"] == 0,
    }
    summary = {"meeting": mid, "loops": loops, "minutes": round((time.time() - t0) / 60, 1), "counts": counts,
               "rss_mb": {"first": rss[0] if rss else None, "base": base, "peak": max(rss, default=None), "last": rss[-1] if rss else None},
               "swap_mb": {"min": min(swaps, default=None), "max": max(swaps, default=None)}, "thermal": thermals,
               "p95_first_words_library": {"first10": p_first, "last10": p_last, "n": [len(first10), len(last10)]},
               "pooled_first_words_p95": {cls: pct([r["first"] for r in on_time if r["cls"] == cls])[1] for cls in ("library", "claude", "not_in_material")},
               "late": late, "false_triggers": [x["question"] for x in extra], "checks": checks}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "checks"}, indent=1, default=str))
    for k, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
