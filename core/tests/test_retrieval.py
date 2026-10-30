import re
import uuid
from pathlib import Path

import httpx
import pytest

from fleck_core.retrieval import Embedder, Retriever, Table, Text, chunk_blocks, ensure_schema, put_passages, weaviate_client

RATE_CARD = Path(__file__).parents[2] / "clients/_firm/rate-card.md"


def _rate_table() -> Table:
    rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in RATE_CARD.read_text().splitlines() if ln.startswith("|")]
    return Table("Time and materials (USD per person per month)", rows[0], rows[2:])


def test_table_chunks_are_labelled_lines_with_caption_first():
    chunks = chunk_blocks([_rate_table()], "Rate card FY2026")
    assert len(chunks) == 2  # 11 rows, at most 8 per chunk
    assert chunks[0].splitlines()[0] == "Rate card FY2026 · Time and materials (USD per person per month)"
    assert "Senior engineer · Offshore (Bengaluru): 5,200" in chunks[0] and "|" not in chunks[0]
    assert len(chunks[0].splitlines()) == 1 + 8 * 2


def test_text_chunks_overlap_on_paragraph_boundaries():
    paras = [f"Paragraph {i} " + "word " * 25 for i in range(30)]
    chunks = chunk_blocks([Text("\n\n".join(paras))], "Doc")
    assert 2 < len(chunks) < 12 and all(len(c) < 1700 for c in chunks)
    first_last = chunks[0].split("\n\n")[-1]
    assert first_last.startswith("Paragraph") and first_last in chunks[1]  # last paragraph repeats
    assert all(c.startswith("Doc\n") for c in chunks)


def test_wide_table_chunks_stop_at_character_cap():
    t = Table("Wide", ["Role"] + [f"Column {i}" for i in range(30)], [[f"Row {r}"] + ["x" * 40] * 30 for r in range(8)])
    chunks = chunk_blocks([t], "T")
    assert len(chunks) > 1 and all(len(c) < 4500 for c in chunks)


def test_oversize_paragraph_is_split():
    assert len(chunk_blocks([Text("Sentence here. " * 400)], "D")) > 2


@pytest.fixture
def index():
    coll = f"PassageT{uuid.uuid4().hex[:8]}"
    w = weaviate_client("http://localhost:8080")
    ensure_schema(w, coll)
    emb = Embedder("http://localhost:11434")
    texts = [*chunk_blocks([_rate_table()], "Rate card FY2026"),
             "Delivery FAQ\nWe run daily standups and a weekly demo. Sprints are two weeks long.",
             "Capabilities\nWe build retrieval-augmented generation systems and data platforms for retail and insurance clients."]
    rows = [{"text": t, "doc_id": str(i), "chunk_id": f"{i}-0", "title": t.split(" · ")[0].split("\n")[0], "client": None,
             "client_slug": None, "kind": "firm"} for i, t in enumerate(texts)]
    put_passages(w, rows, emb.embed(texts), coll)
    yield Retriever("http://localhost:8080", emb, coll)
    w.delete(f"/v1/schema/{coll}")


@pytest.mark.ollama
def test_search_finds_rate_row(index):
    top = index.search("senior engineer offshore monthly cost")
    assert top and top[0].title == "Rate card FY2026" and "5,200" in top[0].text and top[0].n == 1


@pytest.mark.ollama
def test_search_unrelated_returns_empty(index):
    assert index.search("what is the capital of Peru") == []
