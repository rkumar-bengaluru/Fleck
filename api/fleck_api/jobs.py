import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from fleck_api.models import Job

log = logging.getLogger("fleck.jobs")
MAX_ATTEMPTS = 3
BACKOFF_S = (5, 30)  # delay after failed attempt 1, 2
STALE_AFTER = timedelta(minutes=10)  # a running job locked longer than this lost its worker
_handlers: dict[str, Callable[[Session, dict], None]] = {}


def job(kind: str):
    def deco(fn):
        _handlers[kind] = fn
        return fn
    return deco


def enqueue(session: Session, kind: str, payload: dict, run_after: datetime | None = None) -> int:
    j = Job(kind=kind, payload=payload, run_after=run_after or datetime.now(timezone.utc))
    session.add(j)
    session.commit()
    return j.id


def run_once(session: Session) -> bool:
    """Claim and run one due job. Returns False when nothing was due.

    Handlers must be idempotent: a crash after the handler commits but before the job is marked
    done (or a stale-running reclaim) runs the handler again.
    """
    # Claim in its own short transaction so the row lock is not held while the handler runs.
    j = session.scalars(
        select(Job).where(or_(
            and_(Job.status == "queued", Job.run_after <= func.now()),
            and_(Job.status == "running", Job.locked_at < func.now() - STALE_AFTER),
        ))
        .order_by(Job.run_after, Job.id).limit(1).with_for_update(skip_locked=True)
    ).first()
    if j is None:
        session.rollback()
        return False
    if j.status == "running" and j.attempts >= MAX_ATTEMPTS:  # reclaimed past the cap: give up
        j.status, j.last_error = "failed", "worker lost"
        session.commit()
        return True
    j.status, j.attempts, j.locked_at = "running", j.attempts + 1, datetime.now(timezone.utc)
    session.commit()
    try:
        handler = _handlers[j.kind]  # unknown kind -> KeyError -> normal retry/fail path
        handler(session, j.payload)
        session.commit()
        j.status = "done"
    except Exception as e:
        session.rollback()
        log.exception("job %s (%s) attempt %s failed", j.id, j.kind, j.attempts)
        j.last_error = f"{type(e).__name__}: {e}"[:2000]
        if j.attempts >= MAX_ATTEMPTS:
            j.status = "failed"
        else:
            j.status = "queued"
            j.run_after = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_S[j.attempts - 1])
    session.commit()
    return True
