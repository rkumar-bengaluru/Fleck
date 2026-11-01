"""Library: upload, parse, chunk, embed, index (spec 7.5), plus the REST routes and the seed import."""
import datetime as dt
import hashlib
import io
import logging
import mimetypes
import re
import tempfile
import uuid
from collections import Counter
from pathlib import Path

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from fleck_core.retrieval import (Block, Embedder, Table, Text, chunk_blocks, delete_document_passages, ensure_schema,
                                  put_passages, weaviate_client)
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from fleck_api.auth import current_user
from fleck_api.config import setting
from fleck_api.db import get_session
from fleck_api.jobs import RECORDING_RETRY, enqueue, job, wait_for_recording
from fleck_api.models import Assist, Client, Document

log = logging.getLogger("fleck.library")
BUCKET = "fleck-documents"
KINDS = ("client", "firm", "reference")
MAX_BYTES = 50 * 1024 * 1024
UNSUPPORTED = "Upload a PDF, DOCX, PPTX, XLSX, Markdown or text file."
SUPPORTED = (".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt")


# Titles for the reference contracts (the filenames say nothing); keyed by path under clients/.
DOC_TITLES = {
    "_reference/kubient/msa-part1.pdf": "Kubient MSA, part 1", "_reference/kubient/msa-part2.pdf": "Kubient MSA, part 2",
    "_reference/cardlytics/maintenance-agreement.pdf": "Cardlytics maintenance agreement",
    "_reference/healthgate/hosting-and-management-agreement.pdf": "HealthGate hosting agreement",
    "_reference/sphere3d/consulting-agreement.pdf": "Sphere 3D consulting agreement",
    "_reference/photronics/outsourcing-agreement.pdf": "Photronics outsourcing agreement",
}


class ParseError(Exception):
    """The file itself is unusable (wrong type, empty, unreadable): retrying cannot help."""


# ---------- parsing ----------

def _markdown_blocks(md: str) -> list[Block]:
    """Pipe tables become Tables (caption = nearest heading above); everything else is Text."""
    blocks: list[Block] = []
    buf: list[str] = []
    path: dict[int, str] = {}  # heading level -> text; the caption is the whole path so units in a parent heading survive
    rows: list[list[str]] = []

    start: list[str | None] = [None]  # heading path where the current text buffer began

    def flush_text():
        if t := "\n".join(buf).strip():
            blocks.append(Text(t, start[0]))
        buf.clear()

    def flush_table():
        if len(rows) >= 2 and any(rows[0]):  # header + at least one row; an all-blank header is a PDF form grid
            blocks.append(Table(" · ".join(path[k] for k in sorted(path)) or None, rows[0], rows[1:]))
        rows.clear()

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            if buf and not rows:
                flush_text()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not (any(cells) and all(re.fullmatch(r":?-+:?", c) for c in cells if c)):
                rows.append(cells)
            continue
        flush_table()
        if m := re.match(r"(#{1,6})\s+(.*)", s):
            path = {k: v for k, v in path.items() if k < len(m[1])} | {len(m[1]): m[2].strip()}
            flush_text()  # every section is its own block, under its own heading
        if not buf and not s:
            continue
        if not buf:
            start[0] = " · ".join(path[k] for k in sorted(path)) or None
        buf.append(line)
    flush_table()
    flush_text()
    return blocks


_FMT_TOKEN = re.compile(r'"([^"]*)"|\[\$([^\]\-]*)[^\]]*\]|\[[^\]]*\]|_.|\*.|\\(.)')


def _display(cell) -> str:
    """Value as Excel shows it, from the positive section of the format.
    shortcut: no fractions or conditions (raw value shown instead); '#' decimals count as fixed."""
    v, fmt = cell.value, cell.number_format.split(";")[0]
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if fmt == "General":
        return str(int(v)) if float(v).is_integer() else f"{v:g}"
    raw = f"{int(v):,}" if float(v).is_integer() else f"{v:,}"
    fmt = _FMT_TOKEN.sub(lambda m: m[1] or m[2] or m[3] or "", fmt)  # literals and [$€-407] symbols kept; padding and colours dropped
    m = re.search(r"([#0?][#0?,]*)(?:\.([0?#]+))?(,*)", fmt)
    if not m or re.search(r"\?\s*/\s*[\d?]", fmt):  # no digits, or a fraction format: show the raw value rather than guess
        return raw
    pre, suf = fmt[:m.start()].strip(), fmt[m.end():].rstrip()
    sci = re.match(r"[eE][+-]0+", suf)
    if sci:
        return f"{pre}{v:.{len(m[2] or '')}E}{suf[sci.end():]}".strip()
    scale = len(m[3]) + len(m[1]) - len(m[1].rstrip(",")) if not m[2] else len(m[3])
    v /= 1000 ** scale
    if "%" in pre + suf:
        v *= 100
    n = f"{abs(v):{',' if ',' in m[1].rstrip(',') else ''}.{len(m[2] or '')}f}"
    return f"{'-' if v < 0 else ''}{pre}{n}{suf}".strip()


def _xlsx_blocks(path: Path) -> list[Block]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    blocks: list[Block] = []
    for ws in wb.worksheets:
        grid = [[c for c in row] for row in ws.iter_rows(max_col=ws.max_column)]
        # the header is the first row filled except possibly its first cell (a blank corner above the row labels)
        hdr = next((i for i, row in enumerate(grid) if len(row) > 1 and all(c.value not in (None, "") for c in row[1:])), None)
        if hdr is None:
            continue
        caption = " ".join(str(c.value) for row in grid[:hdr] for c in row if c.value not in (None, "")) or ws.title
        rows = [[_display(c) for c in row] for row in grid[hdr + 1:]]
        blocks.append(Table(caption, [str(c.value or "") for c in grid[hdr]], [r for r in rows if any(r)]))
    return blocks


def parse_document(path: Path) -> list[Block]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ParseError(UNSUPPORTED)
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
    if doc is None or wait_for_recording(session, "index_document", payload, RECORDING_RETRY):  # deleted while queued
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
        title = DOC_TITLES.get(doc.source_path or "") or document_title(path, blocks)
    chunks = chunk_blocks(blocks, title)
    if not chunks:
        _fail(doc, "No indexable text found in the file.")
        return
    client = session.get(Client, doc.client_id) if doc.client_id else None
    embedder, vectors = Embedder(setting("FLECK_OLLAMA_URL", "http://localhost:11434"), timeout=120), []
    doc.progress_done, doc.progress_total = 0, len(chunks)
    for i in range(0, len(chunks), 32):  # the Library page shows "Indexing, 32 of 120"
        vectors += embedder.embed(chunks[i:i + 32])
        doc.progress_done = len(vectors)
        session.commit()
    rows = [{"text": c, "doc_id": str(doc.id), "chunk_id": f"{doc.id}-{i}", "title": title, "client": client.name if client else None,
             "client_slug": client.slug if client else None, "kind": doc.kind} for i, c in enumerate(chunks)]
    doc_id = doc.id

    def exists() -> bool:  # fresh read: the document may have been deleted while we parsed and embedded
        session.commit()
        return session.scalar(select(Document.id).where(Document.id == doc_id)) is not None

    with weaviate_client(setting("FLECK_WEAVIATE_URL", "http://localhost:8080")) as w:
        ensure_schema(w, _collection())
        if not exists():
            return
        delete_document_passages(w, str(doc_id), _collection())
        put_passages(w, rows, vectors, _collection())
        if not exists():  # deleted mid-write: take back what we wrote
            delete_document_passages(w, str(doc_id), _collection())
            return
    doc = session.get(Document, doc_id)
    doc.title, doc.chunk_count, doc.status = title, len(chunks), "indexed"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_or_create_client(session: Session, name: str, slug: str | None = None) -> Client:
    name = name.strip()
    c = session.scalar(select(Client).where(Client.slug == (slug or _slug(name))))
    if c is None:
        c = Client(name=name, slug=slug or _slug(name))
        session.add(c)
    elif c.name != name:
        c.name = name
    session.commit()
    return c


def _put_blob(key: str, data: bytes, filename: str) -> None:
    _bucket().put_object(BUCKET, key, io.BytesIO(data), len(data), content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")


def store_document(session: Session, filename: str, data: bytes, kind: str, client_id: int | None,
                   source_path: str | None = None, content_hash: str | None = None) -> Document:
    """Add the row, save the original to MinIO, queue indexing. A failed upload leaves neither."""
    key = f"documents/{uuid.uuid4().hex}/{filename}"
    doc = Document(client_id=client_id, kind=kind, title=Path(filename).stem, filename=filename, blob_key=key, status="queued",
                   source_path=source_path, content_hash=content_hash)
    session.add(doc)
    session.commit()
    try:
        _put_blob(key, data, filename)
    except Exception:
        session.delete(doc)
        session.commit()
        raise
    doc.job_id = enqueue(session, "index_document", {"document_id": doc.id})
    return doc


def _row(session: Session, d: Document) -> dict:
    c = session.get(Client, d.client_id) if d.client_id else None
    return {"id": d.id, "title": d.title, "filename": d.filename, "kind": d.kind, "client_id": d.client_id,
            "client": c.name if c else None, "status": d.status, "error": d.error, "chunk_count": d.chunk_count,
            "used_count": d.used_count, "progress_done": d.progress_done, "progress_total": d.progress_total, "created_at": d.created_at}


# ---------- routes (plain def: sync DB work runs in FastAPI's threadpool) ----------

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])


class NewClient(BaseModel):
    name: str


def _client_json(s: Session, c: Client) -> dict:
    kinds = set(s.scalars(select(Document.kind).where(Document.client_id == c.id).distinct()))
    # reference_only: every document is someone else's contract, so not a client to link a meeting to
    return {"id": c.id, "name": c.name, "slug": c.slug, "reference_only": kinds == {"reference"}}


@router.get("/clients")
def list_clients(s: Session = Depends(get_session)):
    return [_client_json(s, c) for c in s.scalars(select(Client).order_by(Client.name))]


@router.post("/clients")
def create_client(body: NewClient, s: Session = Depends(get_session)):
    if not _slug(body.name):
        raise HTTPException(422, "Client name is required")
    return _client_json(s, get_or_create_client(s, body.name))


@router.get("/library/documents")
def list_documents(kind: str | None = None, q: str | None = None, s: Session = Depends(get_session)):
    stmt = select(Document).order_by(Document.created_at.desc(), Document.id.desc())
    if kind:
        stmt = stmt.where(Document.kind == kind)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Document.title.ilike(like), Document.filename.ilike(like)))
    used = Counter(c.get("doc_id") for cs in s.scalars(select(Assist.citations)) for c in cs)  # assists citing each document
    return [_row(s, d) | {"used_count": used[str(d.id)]} for d in s.scalars(stmt)]


async def size_guard(request: Request, call_next):
    """Refuse an oversized upload from its Content-Length before the body is read and spooled."""
    if request.method == "POST" and request.url.path == "/api/library/documents":
        try:
            size = int(request.headers.get("content-length") or 0)
        except ValueError:
            return Response("Invalid Content-Length", status_code=400)
        if size > MAX_BYTES + 4096:
            return Response("File is larger than 50 MB", status_code=413)
    return await call_next(request)


@router.post("/library/documents", status_code=202)
def upload_document(file: UploadFile, kind: str = Form(...), client_id: int | None = Form(None), s: Session = Depends(get_session)):
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    if kind == "client" and client_id is None:
        raise HTTPException(422, "A client document needs a client_id")
    if client_id is not None and s.get(Client, client_id) is None:
        raise HTTPException(422, "Unknown client")
    name = Path(file.filename or "upload").name
    if Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(415, UNSUPPORTED)
    data = b""
    while chunk := file.file.read(1024 * 1024):
        data += chunk
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "File is larger than 50 MB")
    doc = store_document(s, name, data, kind, client_id)
    return {**_row(s, doc), "job_id": doc.job_id}


def _doc(s: Session, doc_id: int) -> Document:
    d = s.get(Document, doc_id)
    if d is None:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/library/documents/{doc_id}/file")
def document_file(doc_id: int, s: Session = Depends(get_session)):
    d = _doc(s, doc_id)
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', "_", d.filename)
    headers = {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(d.filename)}",
               "X-Content-Type-Options": "nosniff"}
    obj = _bucket().get_object(BUCKET, d.blob_key)

    def body():
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(body(), media_type=mimetypes.guess_type(d.filename)[0] or "application/octet-stream", headers=headers)


def remove_document(s: Session, d: Document) -> None:
    """Row first, so a worker that is mid-index sees it gone and takes back its own passages; then passages and original."""
    doc_id, key = str(d.id), d.blob_key
    s.delete(d)
    s.commit()
    with weaviate_client(setting("FLECK_WEAVIATE_URL", "http://localhost:8080")) as w:
        ensure_schema(w, _collection())
        delete_document_passages(w, doc_id, _collection())
    try:
        _bucket().remove_object(BUCKET, key)
    except S3Error:
        log.warning("blob %s already gone", key)


@router.delete("/library/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int, s: Session = Depends(get_session)):
    remove_document(s, _doc(s, doc_id))
    return Response(status_code=204)


# ---------- seed ----------

# Display names that differ from the folder name; any other folder is title-cased.
CLIENT_NAMES = {"calloway-marsh": "Calloway & Marsh", "meridian-intel": "Meridian Intelligence", "larkspur-retail": "Larkspur Retail Group",
                "brightwater-insurance": "Brightwater Insurance", "healthgate": "HealthGate", "sphere3d": "Sphere3D"}


class SeedError(Exception):
    pass


def seed_library(session: Session, root: Path, removed: list[str] | None = None) -> list[Document]:
    """Import clients/: folder -> client; _firm -> firm; _reference/<name> -> reference with client <name>. Never touches jf/.
    Idempotent: unchanged files are skipped, changed or failed ones are re-uploaded and re-indexed, and seed-created
    documents whose file is gone are removed."""
    docs: list[Document] = []
    seen: set[str] = set()
    for f in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED):
        parts = f.relative_to(root).parts
        if len(parts) < 2 or "jf" in parts or parts[-1].lower() == "readme.md":
            continue
        rel = "/".join(parts)
        seen.add(rel)
        folder = parts[1] if parts[0] == "_reference" and len(parts) > 2 else None if parts[0] in ("_firm", "_reference") else parts[0]
        kind = "firm" if parts[0] == "_firm" else "reference" if parts[0] == "_reference" else "client"
        client = get_or_create_client(session, CLIENT_NAMES.get(folder, folder.replace("-", " ").title()), folder) if folder else None
        cid = client.id if client else None
        data = f.read_bytes()
        h = hashlib.sha256(data + b"\0" + (client.name if client else "").encode()).hexdigest()
        doc = session.scalar(select(Document).where(Document.source_path == rel)) or session.scalar(select(Document).where(
            Document.source_path.is_(None), Document.kind == kind, Document.filename == f.name,
            Document.client_id.is_(None) if cid is None else Document.client_id == cid))
        if doc is None:
            docs.append(store_document(session, f.name, data, kind, cid, rel, h))
        elif doc.content_hash != h or doc.status == "failed":
            old = doc.blob_key
            doc.blob_key = f"documents/{uuid.uuid4().hex}/{f.name}"
            _put_blob(doc.blob_key, data, f.name)
            doc.source_path, doc.content_hash, doc.status, doc.error, doc.client_id = rel, h, "queued", None, cid
            session.commit()
            try:
                _bucket().remove_object(BUCKET, old)
            except S3Error:
                pass
            enqueue(session, "index_document", {"document_id": doc.id})
            docs.append(doc)
    if not seen:  # a wrong path or an empty folder must never wipe the library
        raise SeedError(f"No library files found under {root}; nothing imported and nothing removed.")
    for d in session.scalars(select(Document).where(Document.source_path.is_not(None))).all():
        if d.source_path not in seen:
            if removed is not None:
                removed.append(d.source_path)
            remove_document(session, d)
    return docs
