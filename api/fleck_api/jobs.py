import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from fleck_api.models import Job

log = logging.getLogger("fleck.jobs")
MAX_ATTEMPTS = 3
BACKOFF_S = (5, 30)  # delay after failed attempt 1, 2
STALE_AFTER = timedelta(minutes=10)  # a running job locked longer than this lost its worker
_handlers: dict[str, Callable[[Session, dict], None]] = {}
_on_fail: dict[str, Callable[[Session, dict, str], None]] = {}


def job(kind: str, on_fail: Callable[[Session, dict, str], None] | None = None):
    """Register a handler. on_fail(session, payload, error) runs once when the job finally fails."""
    def deco(fn):
        _handlers[kind] = fn
        if on_fail:
            _on_fail[kind] = on_fail
        return fn
    return deco


def enqueue(session: Session, kind: str, payload: dict, run_after: datetime | None = None) -> int:
    # run_after=None lets Postgres stamp now(); all scheduling comparisons use the DB clock.
    j = Job(kind=kind, payload=payload, **({"run_after": run_after} if run_after else {}))
    session.add(j)
    session.commit()
    return j.id


def _fail(session: Session, j: Job, error: str) -> None:
    j.status, j.last_error = "failed", error[:2000]
    session.commit()
    hook = _on_fail.get(j.kind)
    if hook:
        try:
            hook(session, j.payload, error)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("on_fail for job %s (%s) raised", j.id, j.kind)


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
        .execution_options(populate_existing=True)  # read the locked row, never a stale identity-map copy
    ).first()
    if j is None:
        session.rollback()
        return False
    if j.status == "running" and j.attempts >= MAX_ATTEMPTS:  # reclaimed past the cap: give up
        _fail(session, j, "worker lost")
        return True
    j.status, j.attempts, j.locked_at = "running", j.attempts + 1, func.now()
    session.commit()
    try:
        handler = _handlers[j.kind]  # unknown kind -> KeyError -> normal retry/fail path
        handler(session, j.payload)
        session.commit()
        j.status = "done"
    except Exception as e:
        session.rollback()
        log.exception("job %s (%s) attempt %s failed", j.id, j.kind, j.attempts)
        err = f"{type(e).__name__}: {e}"
        if j.attempts >= MAX_ATTEMPTS:
            _fail(session, j, err)
            return True
        j.last_error, j.status = err[:2000], "queued"
        j.run_after = func.now() + timedelta(seconds=BACKOFF_S[j.attempts - 1])  # DB clock
    session.commit()
    return True
