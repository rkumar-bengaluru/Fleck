"""Latency budget bench (spec 7.1). Not a pytest. Usage: bench_latency.py [answer-model]"""
import json, os, statistics as st, subprocess, sys, tempfile, threading, time, wave
from pathlib import Path

import numpy as np
from fleck_core.answer import OllamaClient
from fleck_helper.transcribe import StreamTranscriber

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3:4b-q4_K_M"  # decision 72
N = 30
QUESTION = "What does a senior engineer cost per month, offshore?"
SYSTEM = ("You are a presales assistant for Northbeam Digital. Answer in two short sentences using only the passages. "
          "No prices, rates, percentages, SLA figures, team sizes, client names or past-work claims unless a passage states them.")
tmp = Path(tempfile.mkdtemp())


def say(voice, text, name):
    p = tmp / name
    subprocess.run(["say", "-v", voice, "-o", str(p), "--data-format=LEI16@16000", text], check=True)
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def pct(xs):
    xs = sorted(xs)
    return st.median(xs), xs[min(len(xs) - 1, int(0.95 * len(xs)))]


def passages():
    out = []
    for f in sorted(Path("clients/_firm").glob("*.md")):
        out.append(" ".join(f.read_text().split()[:195]))  # ~260 tokens
    return out


q = say("Samantha", QUESTION, "q.wav")
mono = say("Rishi", "We have been delivering cloud migration and data platform work for many years. " * 40, "m.wav")[: 16000 * 60]
print(f"question {len(q)/16000:.1f}s, monologue {len(mono)/16000:.1f}s")
tr = StreamTranscriber()
tr.transcribe(q, True)  # warm / download

QDUR = len(q) / 16000
WIN = 48000


def you_windows(stop):
    i = 0
    while not stop.is_set():
        s = (i * WIN) % (len(mono) - WIN); i += 1
        tr.transcribe(mono[s : s + WIN], False, "you")


def trial(contended):
    """One client question. realistic: You talks 4 s, stops, 1 s gap, client asks.
    contended: a client partial (non-preemptible) is already running when the final arrives, while Qwen generates.
    No You window runs in the contended case."""
    stop = threading.Event()
    if contended:
        stop.set()  # no You window: the client partial in flight and Qwen generating are the contention
    th = threading.Thread(target=you_windows, args=(stop,)); th.start()
    llm_stop = threading.Event()
    llm = threading.Thread(target=lambda: [run(" llm") for _ in iter(llm_stop.is_set, True)]) if contended else None
    if llm:
        llm.start()
    time.sleep(0.2 if contended else 4.0)
    stop.set()
    if not contended:
        time.sleep(1.0)
    tr.set_client_active(True)  # VAD: question starts
    if contended:
        time.sleep(QDUR - 0.1)
        pt = threading.Thread(target=tr.transcribe, args=(q, False, "client", "q")); pt.start()  # partial in flight
        time.sleep(0.1)
    else:
        time.sleep(QDUR)
    tr.set_client_active(False)  # VAD: question ends (silence)
    t = time.perf_counter(); txt = tr.transcribe(q, True, "client", "q")
    dt = time.perf_counter() - t
    th.join()
    if llm:
        llm_stop.set(); llm.join()
    return dt, txt



ps = passages()
ol = OllamaClient()


def run(nonce):
    ctx = "\n\n".join(f"[Passage {i+1}]\n{p}" for i, p in enumerate(ps))
    msgs = [{"role": "system", "content": SYSTEM + nonce}, {"role": "user", "content": f"{ctx}\n\nQuestion: {QUESTION}"}]
    t = time.perf_counter(); first = None
    for tok in ol.stream(msgs, MODEL):
        first = first or time.perf_counter() - t
    return first, time.perf_counter() - t


run("")  # load model
res = {}
for name, c in (("realistic", False), ("contended", True)):
    res[name] = [trial(c) for _ in range(N)]
    print(f"{name} transcript:", res[name][-1][1])
ta = [d for d, _ in res["contended"]]  # gate uses the contended case
cold = [run(f" [{time.time_ns()}]") for _ in range(N)]  # nonce ends SYSTEM: passages + question re-read, as live
warm = [run("") for _ in range(N)]  # identical prompt, fully cached: the start-early case (spec 7.1 item 2)

rss = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout.strip()
print(subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout)
r = {"model": MODEL, "a_realistic": pct([d for d, _ in res["realistic"]]), "a_transcribe_final": pct(ta), "b_cold_ttft": pct([c[0] for c in cold]), "b_cold_total": pct([c[1] for c in cold]),
     "b_warm_ttft": pct([c[0] for c in warm]), "b_warm_total": pct([c[1] for c in warm]), "helper_rss_mb": int(rss) / 1024}
for k, v in r.items():
    print(k, v if not isinstance(v, tuple) else f"p50 {v[0]:.2f}s p95 {v[1]:.2f}s")
# Gate on the live case: fresh passages + question (cold). Also show the contended case with start-early.
est = 0.6 + r["a_realistic"][1] + 0.2 + r["b_cold_ttft"][1]
early = 0.6 + r["a_transcribe_final"][1] + 0.2 + r["b_warm_ttft"][1]
print(f"contended + start-early = 0.6 + {r['a_transcribe_final'][1]:.2f} + 0.2 + {r['b_warm_ttft'][1]:.2f} = {early:.2f}s")
print(f"estimated first words = 0.6 + {r['a_realistic'][1]:.2f} + 0.2 + {r['b_cold_ttft'][1]:.2f} = {est:.2f}s ->", "PASS" if est <= 3.0 else "FAIL")
