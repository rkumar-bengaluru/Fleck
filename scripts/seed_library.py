"""Import clients/ into the library: create rows, upload originals, queue indexing, wait for the compose worker.
The worker indexes everything, so host and image code never mix. Safe to re-run.
Usage: uv run --package fleck-api python scripts/seed_library.py [--force] [--timeout SECONDS]
--force re-queues every document (e.g. after a chunking change)."""
import sys
import time
from pathlib import Path

from sqlalchemy import func, select, update

from fleck_api.db import session_factory
from fleck_api.models import Document

ROOT = Path(__file__).parents[1] / "clients"

if __name__ == "__main__":
    from fleck_api.library import SeedError, seed_library
    timeout = float(sys.argv[sys.argv.index("--timeout") + 1]) if "--timeout" in sys.argv else 600
    with session_factory()() as s:
        if "--force" in sys.argv:
            s.execute(update(Document).values(content_hash=None))
            s.commit()
        removed: list[str] = []
        try:
            added = seed_library(s, ROOT, removed)
        except SeedError as e:
            raise SystemExit(f"seed-library: {e}")
        for path in removed:
            print(f"removed {path}")
        print(f"queued {len(added)} documents, removed {len(removed)}")
        deadline = time.time() + timeout
        while s.scalar(select(func.count()).where(Document.status.in_(("queued", "indexing")))):
            if time.time() > deadline:
                raise SystemExit(f"seed-library: still indexing after {timeout:.0f} s; is the worker running? (make up)")
            time.sleep(2)
            s.rollback()
        rows = s.execute(select(Document.kind, Document.status, func.count(), func.coalesce(func.sum(Document.chunk_count), 0)).group_by(Document.kind, Document.status)).all()
        for kind, status, n, chunks in rows:
            print(f"{kind:10} {status:8} documents={n} chunks={chunks}")
        if any(status == "failed" for _, status, _, _ in rows):
            raise SystemExit("seed-library: some documents failed; see the error column in the library")
