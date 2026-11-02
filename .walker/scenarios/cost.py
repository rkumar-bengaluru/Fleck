"""Off camera: what Fleck's own Claude calls cost since the last time this ran, priced by Fleck itself
(fleck_api.claude.cost_usd), as the line Walker's spend ledger reads. The watermark lives beside the footage: the
newest counted call's time, not its id, because a full shoot rebuilds the database and ids start again at 1.
Usage: uv run --all-packages python <walker>/scenarios/cost.py <mark-file>"""
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, true

from fleck_api.claude import cost_usd
from fleck_api.db import session_factory
from fleck_api.models import LlmCall

mark = Path(sys.argv[1])
try:
    last = datetime.fromisoformat(mark.read_text().strip())
except (OSError, ValueError):  # none yet, or an old id watermark: count every call (overcounts, never hides)
    last = None
usd = calls = tin = tout = 0
top = last
with session_factory()() as s:
    for r in s.scalars(select(LlmCall).where(LlmCall.created_at > last if last else true()).order_by(LlmCall.created_at)):
        top = r.created_at
        if not r.model.startswith("claude"):
            continue
        calls += 1
        tin += r.input_tokens or 0
        tout += r.output_tokens or 0
        usd += cost_usd(r.model, r.input_tokens or 0, r.output_tokens or 0) or 0
mark.parent.mkdir(parents=True, exist_ok=True)
if top:
    mark.write_text(top.isoformat())
print(f"WALKER_COST usd={usd:.4f} calls={calls} in={tin} out={tout}")
