import json
from pathlib import Path

import numpy as np
import pytest

_CACHE = Path(__file__).parent / "data/question_embeddings.npz"


@pytest.fixture(scope="session")
def embedding_cache() -> dict[str, np.ndarray]:
    """Question text -> classification vector (CLASSIFY_TASK, float16), written by scripts/train_classifier.py for CI."""
    z = np.load(_CACHE)
    return dict(zip(json.loads(str(z["texts"])), z["vectors"]))


@pytest.fixture(scope="session")
def cached_embed(embedding_cache):
    """An `embed` for classify/Pipeline that never calls Ollama: the cached classification vector and no search vector
    (the cache holds only what the classifier needs). A text missing from the cache fails loudly."""
    def embed(text: str) -> tuple[list[float], None]:
        if text not in embedding_cache:
            pytest.fail(f"not in the CI embedding cache (add it to CI_EXTRA in scripts/train_classifier.py): {text!r}")
        return embedding_cache[text].astype(np.float64).tolist(), None
    return embed
