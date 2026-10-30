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
    emb = Embedder("http://localhost:11434", timeout=120)
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


def test_form_grid_table_dropped():
    t = Table(None, ["", "", "Hours", "Estimated", "Target", "", ""], [["", "Function", "required", "Cost", "date", "Comments", ""]])
    assert chunk_blocks([t], "Maintenance agreement") == []


def test_table_junk_dropped():
    # a label row with no values (rendered "7) ... · · · ·") and a mostly empty grid carry nothing
    t = Table(None, ["Step", "", "", "", ""], [["7) Bank of America Project a) Evaluates CO Response.", "", "", "", ""]])
    assert chunk_blocks([t], "Maintenance agreement") == []
    assert chunk_blocks([Table("Rates", ["Role", "A", "B", "C"], [["x", "1", "", ""], ["y", "", "", ""], ["z", "", "", ""]])], "T") == []


# ---- stub Ollama + Weaviate for timeout, keep_alive and ranking tests
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

from fleck_core.retrieval import RetrievalTimeout  # noqa: E402


@pytest.fixture
def stub():
    state = {"embed_sleep": 0, "graphql_sleep": 0, "hits": [], "bodies": []}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            if self.path == "/api/embed":
                state["bodies"].append(body)
                time.sleep(state["embed_sleep"])
                out = {"embeddings": [[0.1, 0.2]] * len(body["input"])}
            else:
                time.sleep(state["graphql_sleep"])
                out = {"data": {"Get": {"Passage": state["hits"]}}}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", state
    srv.shutdown()


def _hit(i, kind):
    return {"doc_id": str(i), "chunk_id": f"{i}-0", "text": f"t{i}", "title": f"T{i}", "client": None, "kind": kind}


def test_keep_alive_sent_and_slow_embed_times_out(stub):
    url, state = stub
    Embedder(url).embed(["a"])
    assert state["bodies"][-1]["keep_alive"] == -1
    state["embed_sleep"] = 0.5
    t0 = time.time()
    with pytest.raises(RetrievalTimeout):
        Retriever(url, Embedder(url, timeout=0.2), "Passage").search("q")
    assert time.time() - t0 < 0.45


def test_slow_weaviate_times_out(stub):
    url, state = stub
    state["graphql_sleep"] = 0.5
    with pytest.raises(RetrievalTimeout):
        Retriever(url, Embedder(url), "Passage", timeout=0.2).search("q")


def test_firm_ranks_first_and_one_reference_kept(stub):
    url, state = stub
    state["hits"] = [_hit(1, "reference"), _hit(2, "reference"), _hit(3, "firm"), _hit(4, "reference"), _hit(5, "client")]
    got = Retriever(url, Embedder(url), "Passage").search("q", limit=3)
    assert [(p.n, p.doc_id) for p in got] == [(1, "3"), (2, "5"), (3, "1")]
    state["hits"] = [_hit(1, "reference"), _hit(2, "reference"), _hit(4, "reference")]  # no firm/client passage: references are all there is
    assert len(Retriever(url, Embedder(url), "Passage").search("q", limit=3)) == 3
