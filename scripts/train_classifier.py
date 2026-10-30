"""Train the question classifier (decision 94): numpy logistic regression on qwen3-embedding query vectors.

  uv run python scripts/train_classifier.py              train on fixtures/classifier/train.json; writes
                                                          helper/fleck_helper/question_model.json and the CI embedding cache
  uv run python scripts/train_classifier.py --eval F     score a held-out {"trigger": [...], "no_trigger": [...]} file

Never tune on a held-out file: the threshold comes from 5-fold cross-validation on train only. Needs Ollama."""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import httpx
import numpy as np
from fleck_core.retrieval import EMBED_MODEL, QUERY_TASK, Embedder

from fleck_helper import classify

ROOT = Path(__file__).parents[1]
TRAIN = ROOT / "fixtures/classifier/train.json"
CACHE = ROOT / "helper/tests/unit/data/question_embeddings.npz"
L2 = 1e-4  # ridge strength; chosen by CV recall at the FPR cap (see the build log), not on any held-out file
MAX_CV_FPR = 0.02  # the threshold is the lowest one whose cross-validated false-positive rate stays at or under this
# Question texts the CI tests embed besides the training lines (pipeline tests); a missing one fails those tests.
CI_EXTRA = [
    "How quickly could you start a team on this?",
    "What does a senior engineer cost per month?",
    "What does a senior engineer cost per month, offshore?",
    "Is that right?",
    "Can you see my screen? No, sorry… Can you see yours",
]
URL = os.environ.get("FLECK_OLLAMA_URL", "http://localhost:11434")


def _texts(path: Path) -> tuple[list[str], np.ndarray]:
    d = json.loads(path.read_text())
    return [t if isinstance(t, str) else t["text"] for t in d["trigger"] + d["no_trigger"]], \
        np.array([1] * len(d["trigger"]) + [0] * len(d["no_trigger"]))


def embed(questions: list[str]) -> np.ndarray:
    """Exactly `Embedder.embed_query`, batched."""
    vecs = Embedder(URL, timeout=300).embed([f"Instruct: {QUERY_TASK}\nQuery: {q}" for q in questions])
    x = np.asarray(vecs, np.float64)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def fit(x: np.ndarray, y: np.ndarray, l2: float = L2) -> tuple[np.ndarray, float]:
    """Class-balanced, L2-regularised logistic regression by Newton's method (convex; 1024 features)."""
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
    x = np.zeros((len(texts), 1024))
    x[idx] = embed([qs[i] for i in idx])
    probe = np.asarray(Embedder(URL, timeout=60).embed_query(qs[idx[0]]))
    assert np.dot(probe / np.linalg.norm(probe), x[idx[0]]) > 0.999, "batched embedding differs from embed_query"

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
    threshold = min(ok)
    cv = metrics(oof, y, threshold)
    cv_05 = metrics(oof, y, 0.5)
    print(f"5-fold CV at chosen threshold: {cv}\n5-fold CV at 0.5: {cv_05}")

    w, b = fit(x[shaped], y[shaped], l2)
    p = np.where(shaped, _prob(x, w, b), 0.0)
    fit_m = metrics(p, y, threshold)
    print(f"train (fit on all): {fit_m}")
    digest = next(m["digest"] for m in httpx.get(f"{URL}/api/tags", timeout=10).json()["models"] if m["name"] == EMBED_MODEL)
    out = {"embed_model": EMBED_MODEL, "embed_digest": digest, "query_instruction": QUERY_TASK,
           "threshold": threshold, "bias": round(float(b), 6), "weights": [round(float(v), 6) for v in w],
           "train_counts": {"trigger": int(y.sum()), "no_trigger": int((1 - y).sum()), "shape_passed": int(shaped.sum())},
           "cv": {"folds": 5, "max_fpr": MAX_CV_FPR, "at_threshold": cv, "at_0.5": cv_05}, "train": fit_m,
           "trained": datetime.date.today().isoformat()}
    classify.MODEL_PATH.write_text(json.dumps(out) + "\n")
    print(f"wrote {classify.MODEL_PATH.relative_to(ROOT)}")

    refs = [ROOT / "fixtures/conversation/reference.json", ROOT / "fixtures/negative/reference.json"]
    extra = [classify.question_text(t["text"]) for r in refs for t in json.loads(r.read_text())["turns"]]
    extra += [classify.question_text(t) for t in CI_EXTRA]
    cached = {qs[i]: x[i] for i in idx}
    missing = sorted({q for q in extra if q and q not in cached})
    if missing:
        cached.update(zip(missing, embed(missing)))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted(cached)
    np.savez_compressed(CACHE, texts=np.array(json.dumps(keys)), vectors=np.array([cached[k] for k in keys], np.float16))
    print(f"wrote {CACHE.relative_to(ROOT)}: {len(keys)} texts, {CACHE.stat().st_size / 1e6:.2f} MB")


def evaluate(path: Path):
    texts, y = _texts(path)
    pred = np.array([classify.triggers(t, "client", embed=lambda q: classify.live_embed(q, timeout=30)) for t in texts])
    print(f"recall {int(pred[y == 1].sum())}/{int(y.sum())}, false triggers {int(pred[y == 0].sum())}/{int((1 - y).sum())}"
          f" at threshold {classify.trigger_threshold()}")
    print("missed:", *[t for t, p, l in zip(texts, pred, y) if l and not p], sep="\n  ")
    print("false triggers:", *[t for t, p, l in zip(texts, pred, y) if p and not l], sep="\n  ")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", type=Path)
    ap.add_argument("--l2", type=float, default=L2, help="ridge strength (compare CV metrics across runs)")
    a = ap.parse_args()
    evaluate(a.eval) if a.eval else train(a.l2)
    sys.exit(0)
