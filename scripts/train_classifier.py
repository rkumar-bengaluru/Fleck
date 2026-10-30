"""Train the question classifier (decisions 94, 95): numpy logistic regression on qwen3-embedding:4b vectors
under CLASSIFY_TASK (search stays on 0.6b).

  uv run python scripts/train_classifier.py              train on fixtures/classifier/train.json; writes
                                                          helper/fleck_helper/question_model.json and the CI embedding cache
  uv run python scripts/train_classifier.py --eval F     score a held-out {"trigger": [...], "no_trigger": [...]} file
  uv run python scripts/train_classifier.py --eval F --rules-only   score the CPU rules fallback on it instead

Never tune on a held-out file: the threshold comes from 5-fold cross-validation on train only. Needs Ollama with
qwen3-embedding:4b (classifier) and qwen3-embedding:0.6b (search); scripts/ensure_ollama.sh pulls both."""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

import httpx
import numpy as np
from fleck_core.retrieval import EMBED_MODEL, QUERY_TASK

from fleck_helper import classify

ROOT = Path(__file__).parents[1]
TRAIN = ROOT / "fixtures/classifier/train.json"
CACHE = ROOT / "helper/tests/unit/data/question_embeddings.npz"
# Ridge strength, by 5-fold CV recall at FPR <= 2% (fix round 4 on 0.6b: 3e-5 -> 0.892, 1e-5 -> 0.905, 3e-6 -> 0.894,
# 1e-6 -> 0.892; kept for 4b by the decision 95 ruling). Never chosen on a held-out file.
L2 = 1e-5
MAX_CV_FPR = 0.02  # the threshold is the lowest one whose cross-validated false-positive rate stays at or under this
# The CI cache holds only what the CI tests embed: the fixture transcripts plus these (a missing one fails loudly).
CI_EXTRA = [
    "How quickly could you start a team on this?",
    "What does a senior engineer cost per month?",
    "What does a senior engineer cost per month, offshore?",
    "Okay, that works for us.",
    "we close our budget cycle just before that. Who owns the code you write for us?",
    "Okay, that is about what we expected. How quickly could you have a team working on this?",
    "Does Thursday work for the follow up?",
    # re-review 2: names go to the model, the vendor's own name included; the client's colleagues must not trigger
    "Rupak, what would the onsite rate be?",
    "Rupak, can you walk us through the support model?",
    "Bengaluru, is that where the whole team sits?",
    "Arjun, can you check with the infra team?",
    "Meera, what did the vendor team say about the integration?",
    "Dana, can you tell them about our volumes?",
    "Marcus, did you want to ask about the iManage integration?",
    # share + information, not a document to send
    "Could you share some numbers on how much review time it saved?",
    "Could you share details on your security certifications?",
    "Can you share your experience with Indian contract law?",
    "Could you share a case study where the pilot failed?",
    # the second look's negative turn (pipeline test), whole and last segment
    "I just shared the deck in the chat. Are you able to open it on your side?",
    "Are you able to open it on your side?",
]
URL = os.environ.get("FLECK_OLLAMA_URL", "http://localhost:11434")


def _texts(path: Path) -> tuple[list[str], np.ndarray]:
    """Lines and labels, deduplicated ignoring case and punctuation (the first line and its label win), so one text
    can't sit in a CV train and test fold at once."""
    d = json.loads(path.read_text())
    seen: dict[str, tuple[str, int]] = {}
    for label, key in ((1, "trigger"), (0, "no_trigger")):
        for t in d[key]:
            t = t if isinstance(t, str) else t["text"]
            seen.setdefault(" ".join(re.findall(r"[a-z0-9]+", t.lower())), (t, label))
    return [t for t, _ in seen.values()], np.array([label for _, label in seen.values()])


def embed(questions: list[str]) -> np.ndarray:
    """The classification vectors, exactly as `classify.live_embed` asks for them, batched."""
    x = np.asarray(classify.embed_classify(questions, timeout=600), np.float64)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def fit(x: np.ndarray, y: np.ndarray, l2: float = L2) -> tuple[np.ndarray, float]:
    """Class-balanced, L2-regularised logistic regression by Newton's method (convex)."""
    xb = np.hstack([x, np.ones((len(x), 1))])
    sw = np.where(y == 1, 0.5 / max(y.mean(), 1e-9), 0.5 / max(1 - y.mean(), 1e-9))
    reg = np.full(xb.shape[1], l2)
    reg[-1] = 0.0  # the bias is not regularised
    w = np.zeros(xb.shape[1])
    for _ in range(25):
        p = 1 / (1 + np.exp(-(xb @ w)))
        grad = xb.T @ (sw * (p - y)) / len(y) + reg * w
        hess = (xb * (sw * p * (1 - p))[:, None]).T @ xb / len(y) + np.diag(reg + 1e-9)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return w[:-1], float(w[-1])


def _prob(x, w, b):
    return 1 / (1 + np.exp(-(x @ w + b)))


def metrics(p: np.ndarray, y: np.ndarray, t: float) -> dict:
    hit = p >= t
    return {"threshold": round(float(t), 4), "recall": round(float(hit[y == 1].mean()), 4),
            "fpr": round(float(hit[y == 0].mean()), 4), "accuracy": round(float((hit == (y == 1)).mean()), 4)}


def train(l2: float = L2):
    texts, y = _texts(TRAIN)
    qs = [classify.question_text(t) for t in texts]
    shaped = np.array([q is not None for q in qs])
    print(f"train: {int(y.sum())} trigger, {int((1 - y).sum())} no_trigger; shape gate passes "
          f"{int(shaped[y == 1].sum())} trigger and {int(shaped[y == 0].sum())} no_trigger lines")
    print("trigger lines the shape gate rejects:", [t for t, q, l in zip(texts, qs, y) if l and q is None])
    idx = np.flatnonzero(shaped)
    vecs = embed([qs[i] for i in idx])
    x = np.zeros((len(texts), vecs.shape[1]))
    x[idx] = vecs
    probe = np.asarray(classify.live_embed(qs[idx[0]], timeout=60)[0])
    assert np.dot(probe / np.linalg.norm(probe), x[idx[0]]) > 0.999, "batched embedding differs from live_embed"

    rng = np.random.default_rng(0)
    fold = np.empty(len(y), int)
    for label in (0, 1):  # stratified 5-fold
        members = rng.permutation(np.flatnonzero(y == label))
        fold[members] = np.arange(len(members)) % 5
    oof = np.zeros(len(y))
    for k in range(5):
        tr = shaped & (fold != k)
        w, b = fit(x[tr], y[tr], l2)
        te = shaped & (fold == k)
        oof[te] = _prob(x[te], w, b)
    candidates = sorted(set(np.round(oof[shaped], 4)) | {0.5})
    ok = [t for t in candidates if t > 0 and metrics(oof, y, t)["fpr"] <= MAX_CV_FPR]
    if not ok:
        raise SystemExit(f"no threshold keeps the CV false-positive rate at or under {MAX_CV_FPR}: "
                         "add no_trigger lines or change --l2")
    threshold = min(ok)
    cv = metrics(oof, y, threshold)
    cv_05 = metrics(oof, y, 0.5)
    print(f"5-fold CV at chosen threshold: {cv}\n5-fold CV at 0.5: {cv_05}")

    w, b = fit(x[shaped], y[shaped], l2)
    p = np.where(shaped, _prob(x, w, b), 0.0)
    fit_m = metrics(p, y, threshold)
    print(f"train (fit on all): {fit_m}")
    digests = {m["name"]: m["digest"] for m in httpx.get(f"{URL}/api/tags", timeout=10).json()["models"]}
    out = {"classify_model": classify.CLASSIFY_MODEL, "classify_digest": digests[classify.CLASSIFY_MODEL],
           "classify_options": classify.CLASSIFY_OPTIONS, "classify_instruction": classify.CLASSIFY_TASK,
           "search_model": EMBED_MODEL, "search_digest": digests[EMBED_MODEL], "search_instruction": QUERY_TASK, "l2": l2,
           "threshold": threshold, "bias": round(float(b), 6), "weights": [round(float(v), 6) for v in w],
           "train_counts": {"trigger": int(y.sum()), "no_trigger": int((1 - y).sum()), "shape_passed": int(shaped.sum())},
           "cv": {"folds": 5, "max_fpr": MAX_CV_FPR, "at_threshold": cv, "at_0.5": cv_05}, "train": fit_m,
           "trained": datetime.date.today().isoformat()}
    classify.MODEL_PATH.write_text(json.dumps(out) + "\n")
    print(f"wrote {classify.MODEL_PATH.relative_to(ROOT)}")

    refs = [ROOT / "fixtures/conversation/reference.json", ROOT / "fixtures/negative/reference.json"]
    extra = [classify.question_text(t["text"]) for r in refs for t in json.loads(r.read_text())["turns"]]
    extra += [classify.question_text(t) for t in CI_EXTRA]
    by_text = {qs[i]: x[i] for i in idx}
    wanted = sorted({q for q in extra if q})
    missing = [q for q in wanted if q not in by_text]
    if missing:
        by_text.update(zip(missing, embed(missing)))
    cached = {q: by_text[q] for q in wanted}  # only the CI texts: 2560-d vectors for all of train would be ~13 MB
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted(cached)
    np.savez_compressed(CACHE, texts=np.array(json.dumps(keys)), vectors=np.array([cached[k] for k in keys], np.float16))
    print(f"wrote {CACHE.relative_to(ROOT)}: {len(keys)} texts, {CACHE.stat().st_size / 1e6:.2f} MB")


def evaluate(path: Path, rules_only: bool = False):
    texts, y = _texts(path)
    emb = classify.rules_only if rules_only else lambda q: classify.live_embed(q, timeout=30)
    pred = np.array([classify.triggers(t, "client", embed=emb) for t in texts])
    print(f"{'rules fallback' if rules_only else 'model'}: recall {int(pred[y == 1].sum())}/{int(y.sum())}, false triggers {int(pred[y == 0].sum())}/{int((1 - y).sum())}"
          f" at threshold {classify.FALLBACK_MIN_CONFIDENCE if rules_only else classify.trigger_threshold()}")
    print("missed:", *[t for t, p, l in zip(texts, pred, y) if l and not p], sep="\n  ")
    print("false triggers:", *[t for t, p, l in zip(texts, pred, y) if p and not l], sep="\n  ")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", type=Path)
    ap.add_argument("--rules-only", action="store_true", help="with --eval: score the CPU rules fallback")
    ap.add_argument("--l2", type=float, default=L2, help="ridge strength (compare CV metrics across runs)")
    a = ap.parse_args()
    evaluate(a.eval, a.rules_only) if a.eval else train(a.l2)
    sys.exit(0)
