"""Answer quality of the live library path on the 8 fixture questions (spec 7.4, expected outcomes per spec 8).
Real retrieval (Weaviate 8080) and qwen3:4b (Ollama 11434). Each question runs twice: as asked (whatever the cache
holds from the previous question), then after prefill() on the first half of the question (spec 7.1 item 2).
Usage: uv run --all-packages python scripts/measure_answers.py   (exit 1 if any question FAILs; known limits don't)"""
import re
import statistics as st
import sys
import time
from pathlib import Path

import yaml

from fleck_api.library import CLIENT_NAMES
from fleck_core.answer import Done, OllamaClient, Sentence, prefill, stream_library_answer
from fleck_core.retrieval import Embedder, Retriever

ROOT = Path(__file__).parents[1]
CLIENTS = [CLIENT_NAMES.get(d.name, d.name.replace("-", " ").title()) for d in sorted((ROOT / "clients").iterdir())
           if d.is_dir() and not d.name.startswith("_") and d.name != "jf"]
PAST_PRICE = re.compile(r"1\.42|1,420|38,000|2\.1M|Larkspur", re.I)  # Larkspur's commercials (proposal, SOW, case study)


def run(question, passages, ollama):
    now = time.time()
    ev = list(stream_library_answer(question, passages, CLIENTS, ollama, now, now))
    done: Done = ev[-1]
    r = done.check.reasons
    kind = "timeout" if r == ["timeout"] else "NONE" if r == ["none"] else "answer" if done.check.ok else "not_in_material"
    sents = [e for e in ev if isinstance(e, Sentence)]
    return {"kind": kind, "check": done.check, "first": done.first_words_ms, "full": done.full_ms,
            "shown": " ".join(s.text for s in sents if s.ok) if done.check.ok else "Not in our material",
            "sentences": len(sents), "rejected": [s.reason for s in sents if not s.ok and s.reason != "uncited"],
            "uncited": sum(s.reason == "uncited" for s in sents)}


KNOWN_LIMITS = {"q_liability": 101}  # decision 101: qwen3:4b replies NONE, the Claude fallback answers


def verdict(q, expect, doc, res):
    """PASS, "PASS -> fallback" (NONE hands over to Task 14's fallback, which Task 14 verifies), FALLBACK (known
    limit) or FAIL."""
    titles = [c.title for c in res["check"].citations]
    handed = res["kind"] == "NONE"
    if expect in ("library", "reference"):
        if res["kind"] == "answer" and res["check"].kind == expect and doc in titles:
            return "PASS"
        return f"FALLBACK (known limit, decision {KNOWN_LIMITS[q]})" if handed and q in KNOWN_LIMITS else "FAIL"
    # general, or not_in_material (spec 7.4: the pricing question must end in "Not in our material")
    if res["kind"] in ("NONE", "not_in_material") and not PAST_PRICE.search(res["shown"]):
        return "PASS -> fallback" if handed else "PASS"
    return "FAIL"


def pct(xs):
    xs = sorted(x for x in xs if x is not None)
    return (st.median(xs), xs[min(len(xs) - 1, int(0.95 * len(xs)))]) if xs else (None, None)


def main() -> int:
    turns = [t for t in yaml.safe_load((ROOT / "fixtures/conversation/script.yaml").read_text())["turns"] if t.get("q")]
    retriever = Retriever("http://localhost:8080", Embedder("http://localhost:11434", timeout=30), timeout=10)
    ollama = OllamaClient()
    rows = []
    for t in turns:
        passages = retriever.search(t["text"])
        cold = run(t["text"], passages, ollama)
        words = t["text"].split()
        prefill(passages, " ".join(words[: len(words) // 2]), ollama)
        warm = run(t["text"], passages, ollama)
        ok = verdict(t["q"], t["expect"], t["doc"], cold)
        rows.append((t, passages, cold, warm, ok))
        print(f"\n{t['q']}: {t['text']}  (expect {t['expect']}{', ' + t['doc'] if t['doc'] else ''})")
        print("  passages: " + "; ".join(f"[{p.n}] {p.kind} {p.title}" for p in passages))
        print(f"  {ok} kind={cold['kind']} label={cold['check'].kind} cites={[c.title for c in cold['check'].citations]}"
              f" rejected={cold['rejected']} uncited={cold['uncited']} first_words_ms={cold['first']} full_ms={cold['full']}"
              f" | prefilled first_words_ms={warm['first']} full_ms={warm['full']} kind={warm['kind']}")
        print(f"  shown: {cold['shown']}")
    n = len(rows)
    cited = sum(r[2]["kind"] == "answer" for r in rows)
    nones = sum(r[2]["kind"] == "NONE" for r in rows)
    sents = sum(r[2]["sentences"] for r in rows)
    rej = sum(len(r[2]["rejected"]) for r in rows)
    cp, wp = pct([r[2]["first"] for r in rows]), pct([r[3]["first"] for r in rows])
    failed = sum(r[4] == "FAIL" for r in rows)
    unc = sum(r[2]["uncited"] for r in rows)
    print(f"\ncite rate {cited}/{n}  NONE rate {nones}/{n}  sentence rejection {rej}/{sents}  uncited dropped {unc}/{sents}")
    print(f"first_words_ms without prefill p50 {cp[0]} p95 {cp[1]}; with prefill p50 {wp[0]} p95 {wp[1]} (from answer start)")
    print("spec 8 outcomes: " + ", ".join(f"{v} {sum(r[4] == v for r in rows)}" for v in dict.fromkeys(r[4] for r in rows)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
