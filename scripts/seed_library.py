"""Import clients/ into the library and index it. Runs from the host against the compose ports; safe to re-run.
Usage: uv run --package fleck-api python scripts/seed_library.py"""
import time
from pathlib import Path

from sqlalchemy import func, select

from fleck_api.db import session_factory
from fleck_api.jobs import run_once
from fleck_api.models import Document

ROOT = Path(__file__).parents[1] / "clients"

if __name__ == "__main__":
    from fleck_api.library import seed_library
    with session_factory()() as s:
        added = seed_library(s, ROOT)
        print(f"queued {len(added)} documents")
        deadline = time.time() + 600
        while time.time() < deadline:
            while run_once(s):  # the compose worker may take some; both are safe
                pass
            if not s.scalar(select(func.count()).where(Document.status.in_(("queued", "indexing")))):
                break
            time.sleep(2)
        for kind, status, n, chunks in s.execute(select(Document.kind, Document.status, func.count(), func.coalesce(func.sum(Document.chunk_count), 0)).group_by(Document.kind, Document.status)):
            print(f"{kind:10} {status:8} documents={n} chunks={chunks}")
