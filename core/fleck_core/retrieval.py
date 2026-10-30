"""Chunking, embeddings (Ollama) and hybrid search (Weaviate over REST/GraphQL, vectors pushed by us)."""
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass

import httpx

from .types import Passage

TENANT = "default"
COLLECTION = "Passage"
EMBED_MODEL = "qwen3-embedding:0.6b"
QUERY_TASK = "Given a client's question in a sales meeting, retrieve passages from the firm's documents that answer it"
TABLE_CHARS, EMBED_CHARS = 3000, 6000  # a wide table chunk stops early; nothing longer than EMBED_CHARS is embedded
log = logging.getLogger("fleck.retrieval")
CHUNK_CHARS, OVERLAP_CHARS, TABLE_ROWS = 1400, 200, 8  # ~350 and ~50 tokens at 4 chars/token


@dataclass
class Text:
    text: str
    heading: str | None = None  # heading path at the start of the text, like a table caption


@dataclass
class Table:
    caption: str | None
    header: list[str]
    rows: list[list[str]]


Block = Text | Table


def _pieces(para: str) -> list[str]:
    """Split an over-long paragraph on sentence ends, then on words."""
    out, cur = [], ""
    for s in re.split(r"(?<=[.;:!?])\s+", para):
        while len(s) > CHUNK_CHARS:
            cut = s.rfind(" ", 0, CHUNK_CHARS)
            cut = cut if cut > 0 else CHUNK_CHARS
            out.append(s[:cut])
            s = s[cut:].lstrip()
        if cur and len(cur) + len(s) + 1 > CHUNK_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur} {s}".strip()
    return out + [cur] if cur else out


def _text_chunks(text: str) -> list[str]:
    paras = [q for raw in re.split(r"\n\s*\n", text) if (p := raw.strip()) for q in ([p] if len(p) <= CHUNK_CHARS else _pieces(p))]
    chunks, cur = [], []
    for p in paras:
        if cur and sum(map(len, cur)) + 2 * len(cur) + len(p) > CHUNK_CHARS:
            chunks.append("\n\n".join(cur))
            cur = [cur[-1]] if len(cur[-1]) <= OVERLAP_CHARS else []  # overlap on a paragraph boundary
        cur.append(p)
    return chunks + ["\n\n".join(cur)] if cur else chunks


def _substantial(chunk: str, lenient: bool = False) -> bool:
    """Form labels and page footers ("number:", "Source: ...") embed close to every query; keep text with real content."""
    body = chunk.split("\n", 1)[1] if "\n" in chunk else ""
    # a short section under its own markdown heading ("A month is 160 billable hours") is kept
    return len(body.split()) >= (6 if lenient else 20) and sum(map(str.isalpha, body)) >= (25 if lenient else 100)


def _head(title: str, caption: str | None) -> str:
    if caption and caption.startswith(f"{title} · "):
        caption = caption[len(title) + 3:]  # the title already leads line 1
    return f"{title} · {caption}" if caption and caption != title else title


def chunk_blocks(blocks: list[Block], title: str) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if isinstance(b, Text):
            out += [c for c in (f"{_head(title, b.heading)}\n{t}" for t in _text_chunks(b.text)) if _substantial(c, b.heading not in (None, title))]
            continue
        rows = [r for r in b.rows if any(r[1:])]  # a row without values says nothing
        cells = [c for r in rows for c in r[1:]]
        if not rows or sum(not r[0] for r in rows) * 2 > len(rows) or sum(map(bool, cells)) * 2 < len(cells):
            continue  # a PDF form grid: unlabelled rows or mostly empty cells
        head = _head(title, b.caption)
        lines, n = [], 0
        for row in rows:
            rl = [f"{row[0]} · {h}: {v}" if h and row[0] else f"{row[0] or h}: {v}" for h, v in zip(b.header[1:], row[1:]) if v] or [" · ".join(row)]
            if n and (n >= TABLE_ROWS or sum(map(len, lines)) + sum(map(len, rl)) > TABLE_CHARS):
                out.append("\n".join([head, *lines]))
                lines, n = [], 0
            lines += rl
            n += 1
        if lines:
            out.append("\n".join([head, *lines]))
    return out


class Embedder:
    """timeout defaults to the live path's 1.5 s; indexing passes a long one. keep_alive keeps the model loaded."""

    def __init__(self, ollama_url: str, keep_alive: int | str = -1, timeout: float = 1.5):
        self.url, self.keep_alive, self.timeout = ollama_url, keep_alive, timeout

    def embed_query(self, question: str) -> list[float]:
        """qwen3-embedding is instruction-tuned: queries carry the task, documents are embedded plain."""
        return self.embed([f"Instruct: {QUERY_TASK}\nQuery: {question}"])[0]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            if len(t) > EMBED_CHARS:
                log.warning("truncating a %d-character text to %d for embedding", len(t), EMBED_CHARS)
        texts = [t[:EMBED_CHARS] for t in texts]
        for i in range(0, len(texts), 32):
            r = httpx.post(f"{self.url}/api/embed", json={"model": EMBED_MODEL, "input": texts[i:i + 32], "keep_alive": self.keep_alive}, timeout=self.timeout)
            r.raise_for_status()
            out += r.json()["embeddings"]
        return out


def weaviate_client(url: str, timeout: float = 60) -> httpx.Client:
    return httpx.Client(base_url=url, timeout=timeout)


class RetrievalError(Exception):
    """Search could not run (service down or erroring); the live path answers "Not in our material"."""


class RetrievalTimeout(RetrievalError):
    pass


def ensure_schema(client: httpx.Client, collection: str = COLLECTION) -> None:
    """Idempotent: collection with multi-tenancy and the single tenant `default`."""
    if client.get(f"/v1/schema/{collection}").status_code == 404:
        txt = lambda n, tok="word": {"name": n, "dataType": ["text"], "tokenization": tok}  # noqa: E731
        r = client.post("/v1/schema", json={
            "class": collection, "vectorizer": "none", "multiTenancyConfig": {"enabled": True},
            "properties": [txt("text"), txt("title"), txt("client"), txt("doc_id", "field"), txt("chunk_id", "field"),
                           txt("client_slug", "field"), txt("kind", "field")],
        })
        if r.status_code != 422 or "already exists" not in r.text:  # lost a creation race = fine
            r.raise_for_status()
    have = client.get(f"/v1/schema/{collection}/tenants")
    have.raise_for_status()
    if TENANT not in {t["name"] for t in have.json()}:
        client.post(f"/v1/schema/{collection}/tenants", json=[{"name": TENANT}]).raise_for_status()


def put_passages(client: httpx.Client, rows: list[dict], vectors: list[list[float]], collection: str = COLLECTION) -> None:
    """rows carry text, doc_id, chunk_id, title, client, client_slug, kind. Object ids derive from chunk_id: re-puts overwrite."""
    objs = [{"class": collection, "tenant": TENANT, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, r["chunk_id"])),
             "properties": r, "vector": v} for r, v in zip(rows, vectors)]
    for i in range(0, len(objs), 100):
        res = client.post("/v1/batch/objects", json={"objects": objs[i:i + 100]})
        res.raise_for_status()
        errs = [o["result"]["errors"] for o in res.json() if o.get("result", {}).get("errors")]
        if errs:
            raise RuntimeError(f"Weaviate rejected passages: {errs[0]}")


def delete_document_passages(client: httpx.Client, doc_id: str, collection: str = COLLECTION) -> None:
    r = client.request("DELETE", "/v1/batch/objects", params={"tenant": TENANT}, json={
        "match": {"class": collection, "where": {"path": ["doc_id"], "operator": "Equal", "valueText": doc_id}}})
    r.raise_for_status()


_FIELDS = "text doc_id chunk_id title client kind"


class Retriever:
    """The live search budget is ~2.5 s in total: embed 1.5 s (Embedder.timeout) plus Weaviate 1 s."""

    def __init__(self, weaviate_url: str, embedder: Embedder, collection: str = COLLECTION, timeout: float = 1.0):
        self.client, self.embedder, self.collection = weaviate_client(weaviate_url, timeout), embedder, collection

    def _get(self, args: str, extra: str = "") -> list[dict]:
        q = f"{{Get{{{self.collection}(tenant:\"{TENANT}\",{args}){{{_FIELDS} _additional{{{extra}}}}}}}}}"
        r = self.client.post("/v1/graphql", json={"query": q})
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"Weaviate query failed: {body['errors'][0]['message']}")
        return body["data"]["Get"][self.collection] or []

    def search(self, question: str, limit: int = 3, client_slug: str | None = None) -> list[Passage]:
        """Hybrid search gated by vector distance. Firm and client passages rank ahead of reference contracts, and
        at most one reference passage is kept when any firm or client passage passed the gate."""
        try:
            vec = json.dumps(self.embedder.embed_query(question))
            where = f'where:{{path:["client_slug"],operator:Equal,valueText:{json.dumps(client_slug)}}},' if client_slug else ""
            max_d = float(os.environ.get("FLECK_RETRIEVAL_MAX_DISTANCE", "0.60"))
            hits = self._get(f"{where}hybrid:{{query:{json.dumps(question)},vector:{vec},alpha:0.5,maxVectorDistance:{max_d},properties:[\"text\",\"title\"]}},limit:{max(limit, 6)}", "score")
        except httpx.TimeoutException as e:
            raise RetrievalTimeout(f"search timed out: {type(e).__name__}") from e
        except (httpx.HTTPError, RuntimeError) as e:
            raise RetrievalError(f"search failed: {e}") from e
        own = [h for h in hits if h["kind"] != "reference"]
        picked = own + [h for h in hits if h["kind"] == "reference"][: 1 if own else limit]
        return [Passage(n=i, **{k: h[k] for k in ("doc_id", "chunk_id", "text", "title", "client", "kind")}) for i, h in enumerate(picked[:limit], 1)]
