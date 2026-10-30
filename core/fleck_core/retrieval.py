"""Chunking, embeddings (Ollama) and hybrid search (Weaviate over REST/GraphQL, vectors pushed by us)."""
import json
import os
import re
import uuid
from dataclasses import dataclass

import httpx

from .types import Passage

TENANT = "default"
COLLECTION = "Passage"
EMBED_MODEL = "qwen3-embedding:0.6b"
CHUNK_CHARS, OVERLAP_CHARS, TABLE_ROWS = 1400, 200, 8  # ~350 and ~50 tokens at 4 chars/token


@dataclass
class Text:
    text: str


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


def chunk_blocks(blocks: list[Block], title: str) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if isinstance(b, Text):
            out += [f"{title}\n{c}" for c in _text_chunks(b.text)]
            continue
        head = f"{title} · {b.caption}" if b.caption and b.caption != title else title
        for i in range(0, len(b.rows), TABLE_ROWS):
            lines = []
            for row in b.rows[i:i + TABLE_ROWS]:
                lines += [f"{row[0]} · {h}: {v}" if h else f"{row[0]}: {v}" for h, v in zip(b.header[1:], row[1:]) if v] or [" · ".join(row)]
            out.append("\n".join([head, *lines]))
    return out


class Embedder:
    def __init__(self, ollama_url: str):
        self.url = ollama_url

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            r = httpx.post(f"{self.url}/api/embed", json={"model": EMBED_MODEL, "input": texts[i:i + 32]}, timeout=120)
            r.raise_for_status()
            out += r.json()["embeddings"]
        return out


def weaviate_client(url: str) -> httpx.Client:
    return httpx.Client(base_url=url, timeout=60)


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
    def __init__(self, weaviate_url: str, embedder: Embedder, collection: str = COLLECTION):
        self.client, self.embedder, self.collection = weaviate_client(weaviate_url), embedder, collection

    def _get(self, args: str, extra: str = "") -> list[dict]:
        q = f"{{Get{{{self.collection}(tenant:\"{TENANT}\",{args}){{{_FIELDS} _additional{{{extra}}}}}}}}}"
        r = self.client.post("/v1/graphql", json={"query": q})
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"Weaviate query failed: {body['errors'][0]['message']}")
        return body["data"]["Get"][self.collection] or []

    def search(self, question: str, limit: int = 3, client_slug: str | None = None) -> list[Passage]:
        vec = json.dumps(self.embedder.embed([question])[0])
        where = f'where:{{path:["client_slug"],operator:Equal,valueText:{json.dumps(client_slug)}}},' if client_slug else ""
        near = self._get(f"{where}nearVector:{{vector:{vec}}},limit:1", "distance")
        max_d = float(os.environ.get("FLECK_RETRIEVAL_MAX_DISTANCE", "0.55"))
        if not near or near[0]["_additional"]["distance"] > max_d:
            return []
        hits = self._get(f"{where}hybrid:{{query:{json.dumps(question)},vector:{vec},alpha:0.5,properties:[\"text\",\"title\"]}},limit:{limit}", "score")
        return [Passage(n=i, **{k: h[k] for k in ("doc_id", "chunk_id", "text", "title", "client", "kind")}) for i, h in enumerate(hits, 1)]
