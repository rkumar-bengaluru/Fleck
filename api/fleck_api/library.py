"""Library: upload, parse, chunk, embed, index (spec 7.5), plus the REST routes and the seed import."""
import datetime as dt
import logging
import mimetypes
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from fleck_core.retrieval import (Block, Embedder, Table, Text, chunk_blocks, delete_document_passages, ensure_schema,
                                  put_passages, weaviate_client)
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from fleck_api.config import setting
from fleck_api.db import get_session
from fleck_api.jobs import enqueue, job
from fleck_api.models import Client, Document

log = logging.getLogger("fleck.library")
BUCKET = "fleck-documents"
KINDS = ("client", "firm", "reference")
MAX_BYTES = 50 * 1024 * 1024
SUPPORTED = (".pdf", ".docx", ".pptx", ".xlsx", ".md", ".markdown", ".txt")


class ParseError(Exception):
    """The file itself is unusable (wrong type, empty, unreadable): retrying cannot help."""


# ---------- parsing ----------

def _markdown_blocks(md: str) -> list[Block]:
    """Pipe tables become Tables (caption = nearest heading above); everything else is Text."""
    blocks: list[Block] = []
    buf: list[str] = []
    heading: str | None = None
    rows: list[list[str]] = []

    def flush_text():
        if t := "\n".join(buf).strip():
            blocks.append(Text(t))
        buf.clear()

    def flush_table():
        if len(rows) >= 2:  # header + at least one row
            blocks.append(Table(heading, rows[0], rows[1:]))
        rows.clear()

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            if buf and not rows:
                flush_text()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                rows.append(cells)
            continue
        flush_table()
        if m := re.match(r"#{1,6}\s+(.*)", s):
            heading = m[1].strip()
        buf.append(line)
    flush_table()
    flush_text()
    return blocks


def _display(cell) -> str:
    """Value as Excel shows it. shortcut: first format section only, no scientific, fractions or conditions."""
    v, fmt = cell.value, cell.number_format.split(";")[0]
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if fmt == "General":
        return str(int(v)) if float(v).is_integer() else f"{v:g}"
    m = re.search(r"[#0?][#0?,]*(?:\.([0?#]+))?", fmt)
    if not m:
        return str(v)
    pre = re.sub(r'["\\_*]|\[[^\]]*\]', "", fmt[:m.start()])
    suf = re.sub(r'["\\_*]|\[[^\]]*\]', "", fmt[m.end():])
    if "%" in suf:
        v *= 100
    n = f"{abs(v):{',' if ',' in m[0] else ''}.{len(m[1] or '')}f}"
    return f"{'-' if v < 0 else ''}{pre}{n}{suf}"


def _xlsx_blocks(path: Path) -> list[Block]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    blocks: list[Block] = []
    for ws in wb.worksheets:
        grid = [[c for c in row] for row in ws.iter_rows(max_col=ws.max_column)]
        hdr = next((i for i, row in enumerate(grid) if row and all(c.value not in (None, "") for c in row)), None)
        if hdr is None:
            continue
        caption = " ".join(str(c.value) for row in grid[:hdr] for c in row if c.value not in (None, "")) or ws.title
        rows = [[_display(c) for c in row] for row in grid[hdr + 1:]]
        blocks.append(Table(caption, [str(c.value) for c in grid[hdr]], [r for r in rows if any(r)]))
    return blocks


def parse_document(path: Path) -> list[Block]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ParseError(f"Unsupported file type '{ext or path.name}'. Upload a PDF, DOCX, PPTX, XLSX or Markdown file.")
    if path.stat().st_size == 0:
        raise ParseError("The file is empty.")
    try:
        if ext == ".xlsx":
            blocks = _xlsx_blocks(path)
        else:
            from markitdown import MarkItDown
            blocks = _markdown_blocks(MarkItDown().convert(str(path)).text_content)
    except Exception as e:
        raise ParseError(f"Could not read the file: {type(e).__name__}: {e}"[:300]) from e
    if not blocks:
        raise ParseError("No text found in the file.")
    return blocks


def _pdf_title(path: Path) -> str | None:
    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser
        with open(path, "rb") as f:
            t = PDFDocument(PDFParser(f)).info[0].get("Title")
        return t.decode("utf-8", "ignore").strip() or None if isinstance(t, bytes) else None
    except Exception:
        return None


def document_title(path: Path, blocks: list[Block]) -> str:
    for b in blocks:
        if isinstance(b, Text) and (m := re.search(r"(?m)^# +(.+)$", b.text)):
            return m[1].strip()
    if path.suffix.lower() == ".pdf" and (t := _pdf_title(path)):
        return t
    s = re.sub(r"[-_]+", " ", path.stem).strip()
    return s[:1].upper() + s[1:].lower()


# ---------- storage and indexing ----------

def _minio() -> Minio:
    url = setting("FLECK_MINIO_URL", "http://localhost:9000")
    return Minio(url.split("://")[-1], setting("FLECK_MINIO_USER", "fleck"), setting("FLECK_MINIO_PASSWORD", "fleck-secret"),
                 secure=url.startswith("https"))


def _bucket() -> Minio:
    m = _minio()
    if not m.bucket_exists(BUCKET):
        try:
            m.make_bucket(BUCKET)
        except S3Error as e:  # lost a creation race
            if e.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise
    return m


def _collection() -> str:
    return setting("FLECK_WEAVIATE_COLLECTION", "Passage")


def ensure_index() -> None:
    with weaviate_client(setting("FLECK_WEAVIATE_URL", "http://localhost:8080")) as w:
        ensure_schema(w, _collection())


def _fail(doc: Document, error: str) -> None:
    doc.status, doc.error = "failed", error[:500]


def _mark_failed(session: Session, payload: dict, error: str) -> None:  # job on_fail: retries are used up
    if doc := session.get(Document, payload["document_id"]):
        _fail(doc, f"Indexing failed after 3 attempts: {error}")


@job("index_document", on_fail=_mark_failed)
def index_document(session: Session, payload: dict) -> None:
    """Idempotent: a retry replaces the document's passages."""
    doc = session.get(Document, payload["document_id"])
    if doc is None:  # deleted while queued
        return
    doc.status, doc.error = "indexing", None
    session.commit()
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / doc.filename
        m = _bucket()
        m.fget_object(BUCKET, doc.blob_key, str(path))
        try:
            blocks = parse_document(path)
        except ParseError as e:
            _fail(doc, str(e))
            return
        title = document_title(path, blocks)
    chunks = chunk_blocks(blocks, title)
    client = session.get(Client, doc.client_id) if doc.client_id else None
    vectors = Embedder(setting("FLECK_OLLAMA_URL", "http://localhost:11434")).embed(chunks)
    rows = [{"text": c, "doc_id": str(doc.id), "chunk_id": f"{doc.id}-{i}", "title": title, "client": client.name if client else None,
             "client_slug": client.slug if client else None, "kind": doc.kind} for i, c in enumerate(chunks)]
    with weaviate_client(setting("FLECK_WEAVIATE_URL", "http://localhost:8080")) as w:
        ensure_schema(w, _collection())
        delete_document_passages(w, str(doc.id), _collection())
        put_passages(w, rows, vectors, _collection())
    doc.title, doc.chunk_count, doc.status = title, len(chunks), "indexed"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_or_create_client(session: Session, name: str) -> Client:
    name = name.strip()
    c = session.scalar(select(Client).where(Client.slug == _slug(name)))
    if c is None:
        c = Client(name=name, slug=_slug(name))
        session.add(c)
        session.commit()
    return c


def store_document(session: Session, filename: str, data: bytes, kind: str, client_id: int | None) -> Document:
    """Save the original to MinIO, add the row, queue indexing."""
    key = f"documents/{uuid.uuid4().hex}/{filename}"
    m = _bucket()
    import io
    m.put_object(BUCKET, key, io.BytesIO(data), len(data), content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
    doc = Document(client_id=client_id, kind=kind, title=Path(filename).stem, filename=filename, blob_key=key, status="queued")
    session.add(doc)
    session.commit()
    doc.job_id = enqueue(session, "index_document", {"document_id": doc.id})
    return doc


def _row(session: Session, d: Document) -> dict:
    c = session.get(Client, d.client_id) if d.client_id else None
    return {"id": d.id, "title": d.title, "filename": d.filename, "kind": d.kind, "client_id": d.client_id,
            "client": c.name if c else None, "status": d.status, "error": d.error, "chunk_count": d.chunk_count,
            "used_count": d.used_count, "created_at": d.created_at}


# ---------- routes (plain def: sync DB work runs in FastAPI's threadpool) ----------

router = APIRouter(prefix="/api")


class NewClient(BaseModel):
    name: str


@router.get("/clients")
def list_clients(s: Session = Depends(get_session)):
    return [{"id": c.id, "name": c.name, "slug": c.slug} for c in s.scalars(select(Client).order_by(Client.name))]


@router.post("/clients")
def create_client(body: NewClient, s: Session = Depends(get_session)):
    if not _slug(body.name):
        raise HTTPException(422, "Client name is required")
    c = get_or_create_client(s, body.name)
    return {"id": c.id, "name": c.name, "slug": c.slug}


@router.get("/library/documents")
def list_documents(kind: str | None = None, q: str | None = None, s: Session = Depends(get_session)):
    stmt = select(Document).order_by(Document.created_at.desc(), Document.id.desc())
    if kind:
        stmt = stmt.where(Document.kind == kind)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Document.title.ilike(like), Document.filename.ilike(like)))
    return [_row(s, d) for d in s.scalars(stmt)]


@router.post("/library/documents", status_code=202)
def upload_document(file: UploadFile, kind: str = Form(...), client_id: int | None = Form(None), s: Session = Depends(get_session)):
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    if client_id is not None and s.get(Client, client_id) is None:
        raise HTTPException(422, "Unknown client")
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is larger than 50 MB")
    doc = store_document(s, Path(file.filename or "upload").name, data, kind, client_id)
    return {**_row(s, doc), "job_id": doc.job_id}


def _doc(s: Session, doc_id: int) -> Document:
    d = s.get(Document, doc_id)
    if d is None:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/library/documents/{doc_id}/file")
def document_file(doc_id: int, s: Session = Depends(get_session)):
    d = _doc(s, doc_id)
    obj = _bucket().get_object(BUCKET, d.blob_key)

    def body():
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(body(), media_type=mimetypes.guess_type(d.filename)[0] or "application/octet-stream",
                             headers={"Content-Disposition": f'inline; filename="{d.filename}"'})


@router.delete("/library/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int, s: Session = Depends(get_session)):
    d = _doc(s, doc_id)
    with weaviate_client(setting("FLECK_WEAVIATE_URL", "http://localhost:8080")) as w:
        ensure_schema(w, _collection())
        delete_document_passages(w, str(d.id), _collection())
    try:
        _bucket().remove_object(BUCKET, d.blob_key)
    except S3Error:
        log.warning("blob %s already gone", d.blob_key)
    s.delete(d)
    s.commit()
    return Response(status_code=204)


# ---------- seed ----------

def seed_library(session: Session, root: Path) -> list[Document]:
    """Import clients/: folder -> client; _firm -> firm; _reference/<name> -> reference with client <name>.
    Never touches jf/. Idempotent: a file already imported (same kind, client, filename) is not added again."""
    docs: list[Document] = []
    for f in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED):
        parts = f.relative_to(root).parts
        if len(parts) < 2 or "jf" in parts or parts[-1].lower() == "readme.md":
            continue
        if parts[0] == "_firm":
            kind, client = "firm", None
        elif parts[0] == "_reference":
            kind, client = "reference", get_or_create_client(session, parts[1].replace("-", " ").title()) if len(parts) > 2 else None
        else:
            kind, client = "client", get_or_create_client(session, parts[0].replace("-", " ").title())
        cid = client.id if client else None
        doc = session.scalar(select(Document).where(Document.kind == kind, Document.filename == f.name,
                                                    Document.client_id.is_(None) if cid is None else Document.client_id == cid))
        if doc and doc.status == "indexed":
            continue
        if doc is None:
            doc = store_document(session, f.name, f.read_bytes(), kind, cid)
        else:
            doc.status = "queued"
            session.commit()
            enqueue(session, "index_document", {"document_id": doc.id})
        docs.append(doc)
    return docs
