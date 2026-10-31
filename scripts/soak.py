"""60-minute soak (spec 7.7, Task 22): conversation.wav looped through `fleck-helper replay --loops N` into one meeting on
the running stack, in real time. Samples the helper's RSS and physical footprint every 30 s and swap and thermal state every minute.
Pass: p95 first words (library and brief answers, on time) in the last 10 minutes within 20% of the first 10 minutes;
helper memory growth after loop 1 (physical footprint, which counts MLX's Metal buffers) < 300 MB; every scripted question in every
loop triggered with its expected outcome (a traced Claude timeout's "Not in our material" is allowed and reported); the classifier's
breaker never opened and the rules fallback never decided (Milestone E and F follow-ups). Late answers are counted apart.
Writes web/test-results/soak/ (helper log, samples, per-loop rows) and prints a summary.
Usage: uv run --all-packages python scripts/soak.py --minutes 60   (exit 1 on a FAIL)"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from stack_replay import LOOP_MS, REF, ROOT, create_meeting, helper_env, log_counts, pct, print_rows, score, sign_in, \
    start_replay, stop, wait_ready

OUT = ROOT / "web/test-results/soak"


def tree_mb(pid: int) -> tuple[float, float]:
    """(RSS, physical footprint) of `pid` and its descendants (uv run -> python), MB. RSS leaves out MLX's Metal
    buffers (Whisper: ~100 MB RSS against ~3 GB footprint), so the growth check uses top's MEM (phys_footprint)."""
    rows = [ln.split() for ln in subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True).stdout.splitlines()]
    kids, rss = {}, {}
    for p, pp, r in rows:
        kids.setdefault(int(pp), []).append(int(p))
        rss[int(p)] = int(r)
    todo, tree = [pid], []
    while todo:
        p = todo.pop()
        tree.append(p)
        todo += kids.get(p, [])
    top = subprocess.run(["top", "-l", "1", "-stats", "pid,mem", *[a for p in tree for a in ("-pid", str(p))]],
                         capture_output=True, text=True).stdout
    unit = {"B": 1 / 2**20, "K": 1 / 1024, "M": 1, "G": 1024}
    foot = sum(float(m[2]) * unit[m[3]] for m in re.finditer(r"^(\d+)\s+([\d.]+)([BKMG])\+?-?\s*$", top, re.M)
               if int(m[1]) in tree)
    return sum(rss.get(p, 0) for p in tree) / 1024, foot


def _check_tree_mb():
    rss, foot = tree_mb(os.getpid())
    assert rss > 0 and foot > 0, (rss, foot)


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
        rss_now, foot_now = tree_mb(p.pid)
        s = {"t_s": round(time.time() - t0), "rss_mb": round(rss_now, 1), "footprint_mb": round(foot_now, 1)}
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
    foot = [s["footprint_mb"] for s in samples if s["footprint_mb"] > 0]
    # growth after loop 1 (MLX fills its cache during the first loop, ~+500 MB once) to the peak (rupak.k, decision 143)
    after1 = [s["footprint_mb"] for s in samples if s["footprint_mb"] > 0 and s["t_s"] * 1000 >= LOOP_MS]
    base = (after1 or foot or [0])[0]
    growth = (max(foot) - base) if foot else float("nan")
    swaps = [s["swap_mb"] for s in samples if "swap_mb" in s]
    thermals = sorted({s["thermal"] for s in samples if "thermal" in s})
    missed = [f"loop {r['loop']} {r['id']}" for r in rows if not r["triggered"]]
    # a Claude timeout ends "Not in our material" by design: allowed and reported, up to the traced timeouts (decision 143)
    timeouts = int(subprocess.run(["docker", "exec", "fleck-postgres-1", "psql", "-U", "fleck", "-d", "fleck", "-Atc",
                                   f"select count(*) from llm_calls where meeting_id = {int(mid)} and purpose = 'live_fallback'"
                                   " and (status = 'timeout' or error ilike '%timeout%')"],
                                  capture_output=True, text=True, check=True).stdout.strip() or 0)
    expect = {q["id"]: q["expect"] for q in REF["questions"]}
    bad = [r for r in rows if r["triggered"] and not r["ok"]]
    # each excused row is matched to its own Claude timeout (the assist's `rejected` carries it), not by count
    timed_out = [r for r in bad if r["kind"] == "not_in_material" and expect[r["id"]] != "not_in_material" and "timeout" in r["rejected"]]
    wrong = [f"loop {r['loop']} {r['id']} ({r['kind']})" for r in bad if r not in timed_out]
    timed_out = [f"loop {r['loop']} {r['id']}" for r in timed_out]
    late = [f"loop {r['loop']} {r['id']} first {r['first']} ms" for r in rows if r.get("late")]
    checks = {
        "replay exited 0": code == 0,
        f"p95 first words last 10 min {p_last} ms within 20% of first 10 min {p_first} ms":
            p_first is not None and p_last is not None and p_last <= 1.2 * p_first,
        f"helper memory (footprint) growth {growth:.0f} MB < 300 MB (from {base:.0f} MB to peak {max(foot, default=0):.0f} MB)": growth < 300,
        f"every question in every loop ({len(rows)}) triggered: missed {missed or 'none'}": not missed,
        f"every outcome as expected: wrong {wrong or 'none'}": not wrong,
        f"every excused timeout ({len(timed_out)}) is traced in llm_calls ({timeouts})": len(timed_out) <= timeouts,
        f"false triggers ({len(extra)}) at most 1 per 10 min (spec 5)": len(extra) <= max(1, int(a.minutes // 10)),
        f"breaker never opened ({counts['breaker_opened']})": counts["breaker_opened"] == 0,
        f"rules fallback never decided ({counts['rules_fallbacks']})": counts["rules_fallbacks"] == 0,
    }
    summary = {"meeting": mid, "loops": loops, "minutes": round((time.time() - t0) / 60, 1), "counts": counts,
               "rss_mb": {"first": rss[0] if rss else None, "peak": max(rss, default=None), "last": rss[-1] if rss else None},
               "footprint_mb": {"first": foot[0] if foot else None, "base": base, "peak": max(foot, default=None), "last": foot[-1] if foot else None},
               "swap_mb": {"min": min(swaps, default=None), "max": max(swaps, default=None)}, "thermal": thermals,
               "p95_first_words_library": {"first10": p_first, "last10": p_last, "n": [len(first10), len(last10)]},
               "pooled_first_words_p95": {cls: pct([r["first"] for r in on_time if r["cls"] == cls])[1] for cls in ("library", "claude", "not_in_material")},
               "late": late, "claude_timeouts": timed_out, "false_triggers": [x["question"] for x in extra], "checks": checks}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "checks"}, indent=1, default=str))
    for k, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
