import re
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from fleck_core.retrieval import TENANT, chunk_blocks
from fleck_core.types import Passage
from fleck_core.validate import check_library_answer
from sqlalchemy import func, select

from fleck_api import jobs, library
from fleck_api.db import get_session
from fleck_api.main import create_app
from fleck_api.models import Document

ROOT = Path(__file__).parents[2]
FIX = Path(__file__).parent / "fixtures"
WEAVIATE = "http://localhost:8080"


def _chunks(path: Path) -> list[str]:
    blocks = library.parse_document(path)
    return chunk_blocks(blocks, library.document_title(path, blocks))


def test_xlsx_header_and_currency_kept():
    # fixture rows: Engagement manager, Senior engineer, ML engineer, QA engineer (no Tech lead)
    xl = _chunks(FIX / "rate-card.xlsx")
    assert any("Senior engineer · Offshore (Bengaluru): $5,200" in c and "(USD per person per month)" in c.splitlines()[0] for c in xl)
    md = _chunks(ROOT / "clients/_firm/rate-card.md")
    row = next(c for c in md if "Senior engineer · Offshore (Bengaluru): 5,200" in c)
    assert "(USD per person per month)" in row.splitlines()[0] and "|" not in row


def test_pptx_table_rows_keep_header():
    c = _chunks(FIX / "rate-card.pptx")
    assert any("ML engineer · Onsite (US/UK): 17,000" in x and "USD per person per month" in x.splitlines()[0] for x in c)


def test_pdf_text_extracted():
    assert any("Bank of America" in c for c in _chunks(ROOT / "clients/_reference/cardlytics/maintenance-agreement.pdf"))


def test_titles():
    p = ROOT / "clients/_firm/rate-card.md"
    assert library.document_title(p, library.parse_document(p)) == "Rate card FY2026"
    assert library.document_title(FIX / "rate-card.xlsx", []) == "Rate card"


@pytest.mark.parametrize("answer,ok", [("A senior engineer is $13,500 offshore. [1]", False), ("A senior engineer is $13,500 onsite. [1]", True),
                                       ("A senior engineer is $5,200 offshore. [1]", True)])
def test_label_binding_on_real_rate_card_chunks(answer, ok):
    chunk = next(c for c in _chunks(ROOT / "clients/_firm/rate-card.md") if "Senior engineer · Offshore" in c)
    p = Passage(n=1, doc_id="d", chunk_id="c", title="Rate card FY2026", client=None, kind="firm", text=chunk)
    assert check_library_answer(answer, [p]).ok is ok


@pytest.fixture
def api(make_session, monkeypatch):
    coll = f"PassageT{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("FLECK_WEAVIATE_COLLECTION", coll)
    monkeypatch.setattr(library.Embedder, "embed", lambda self, texts: [[0.1, 0.2, 0.3, 0.4]] * len(texts))
    app = create_app()

    def sess():
        with make_session() as s:
            yield s
    app.dependency_overrides[get_session] = sess
    yield TestClient(app), make_session, coll
    with make_session() as s:
        for d in s.scalars(select(Document)):
            try:
                library._minio().remove_object(library.BUCKET, d.blob_key)
            except Exception:
                pass
    httpx.delete(f"{WEAVIATE}/v1/schema/{coll}")


def _count(coll: str) -> int:
    q = f'{{Aggregate{{{coll}(tenant:"{TENANT}"){{meta{{count}}}}}}}}'
    return httpx.post(f"{WEAVIATE}/v1/graphql", json={"query": q}).json()["data"]["Aggregate"][coll][0]["meta"]["count"]


def _upload(c, name, data, kind="firm"):
    r = c.post("/api/library/documents", files={"file": (name, data)}, data={"kind": kind})
    assert r.status_code == 202, r.text
    return r.json()


def _drain(make_session):
    with make_session() as s:
        while jobs.run_once(s):
            pass


def test_bad_upload_marked_failed(api):
    c, make_session, _ = api
    bad1, bad2 = _upload(c, "empty.pdf", b""), _upload(c, "deck.key", b"keynote")
    good = _upload(c, "faq.md", b"# Delivery FAQ\n\nWe start within 4 weeks of signature.\n")
    _drain(make_session)
    docs = {d["id"]: d for d in c.get("/api/library/documents").json()}
    assert docs[bad1["id"]]["status"] == "failed" and docs[bad1["id"]]["error"] == "The file is empty."
    assert docs[bad2["id"]]["status"] == "failed" and "Unsupported file type '.key'" in docs[bad2["id"]]["error"]
    assert docs[good["id"]]["status"] == "indexed" and docs[good["id"]]["title"] == "Delivery FAQ" and docs[good["id"]]["chunk_count"] == 1
    assert [d["id"] for d in c.get("/api/library/documents", params={"q": "faq"}).json()] == [good["id"]]


def test_failed_job_marks_document_failed_with_readable_error(api, monkeypatch):
    c, make_session, _ = api
    monkeypatch.setattr(library.Embedder, "embed", lambda self, texts: (_ for _ in ()).throw(httpx.ConnectError("ollama down")))
    d = _upload(c, "a.md", b"# A\n\ntext\n")
    with make_session() as s:
        for _ in range(3):
            jobs.run_once(s)
            s.execute(jobs.Job.__table__.update().values(run_after=func.now()))
            s.commit()
    row = c.get("/api/library/documents").json()[0]
    assert row["id"] == d["id"] and row["status"] == "failed" and "ollama down" in row["error"]


def test_reindex_replaces_passages_and_file_streams(api):
    c, make_session, coll = api
    d = _upload(c, "a.md", b"# A\n\nsome text\n")
    _drain(make_session)
    n = _count(coll)
    with make_session() as s:
        jobs.enqueue(s, "index_document", {"document_id": d["id"]})
    _drain(make_session)
    assert n >= 1 and _count(coll) == n
    assert c.get(f"/api/library/documents/{d['id']}/file").content == b"# A\n\nsome text\n"


def test_delete_removes_passages(api):
    c, make_session, coll = api
    d = _upload(c, "a.md", b"# A\n\nsome text\n")
    _drain(make_session)
    assert _count(coll) >= 1
    assert c.delete(f"/api/library/documents/{d['id']}").status_code == 204
    assert _count(coll) == 0 and c.get("/api/library/documents").json() == []


def test_clients_roundtrip(api):
    c, *_ = api
    a = c.post("/api/clients", json={"name": "Acme Corp"}).json()
    assert c.post("/api/clients", json={"name": "Acme Corp"}).json() == a
    assert c.get("/api/clients").json() == [a]


def test_seed_skips_jf(api, tmp_path):
    _, make_session, _ = api
    for rel in ("jf/x.md", "_firm/a.md", "acme-co/b.md", "_reference/foo/c.md", "README.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("# T\n\ntext\n")
    with make_session() as s:
        library.seed_library(s, tmp_path)
        library.seed_library(s, tmp_path)  # idempotent
        got = sorted((d.kind, d.filename) for d in s.scalars(select(Document)))
    assert got == [("client", "b.md"), ("firm", "a.md"), ("reference", "c.md")]
