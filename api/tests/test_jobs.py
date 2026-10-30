from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from fleck_api import jobs
from fleck_api.models import Base, Job


def test_migration_creates_every_orm_table(make_session):
    with make_session() as s:
        have = {r[0] for r in s.execute(text("select tablename from pg_tables where schemaname='public'"))}
    assert set(Base.metadata.tables) <= have


def test_job_runs_once_and_marks_done(make_session):
    calls = []
    jobs._handlers["t_ok"] = lambda s, p: calls.append(p)
    with make_session() as s:
        jid = jobs.enqueue(s, "t_ok", {"a": 1})
        assert jobs.run_once(s) is True
        assert jobs.run_once(s) is False
        j = s.get(Job, jid)
        assert (j.status, j.attempts, calls) == ("done", 1, [{"a": 1}])


def test_failing_job_retries_three_times_then_failed(make_session):
    def boom(s, p):
        raise ValueError("nope")
    jobs._handlers["t_fail"] = boom
    with make_session() as s:
        jid = jobs.enqueue(s, "t_fail", {})
        for attempt in (1, 2, 3):
            assert jobs.run_once(s) is True
            j = s.get(Job, jid)
            assert j.attempts == attempt
            if attempt < 3:
                assert j.status == "queued" and j.run_after > datetime.now(timezone.utc)  # backed off
                j.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
                s.commit()
        assert (j.status, j.last_error) == ("failed", "ValueError: nope")
        assert jobs.run_once(s) is False


def test_two_workers_never_claim_same_job(make_session):
    ran = []
    jobs._handlers["t_once"] = lambda s, p: ran.append(p)
    with make_session() as a, make_session() as b:
        jobs.enqueue(a, "t_once", {})
        # Worker a holds the row lock mid-claim: b must skip it, not block or double-claim.
        held = a.scalars(select(Job).where(Job.status == "queued").with_for_update(skip_locked=True)).all()
        assert len(held) == 1
        assert jobs.run_once(b) is False
        a.rollback()  # a lets go; now exactly one claim succeeds
        assert jobs.run_once(b) is True
        assert jobs.run_once(a) is False
    assert len(ran) == 1


def test_future_job_not_claimed(make_session):
    with make_session() as s:
        jobs.enqueue(s, "t_ok", {}, run_after=datetime.now(timezone.utc) + timedelta(hours=1))
        assert jobs.run_once(s) is False


def test_stale_running_job_is_reclaimed_then_failed_past_cap(make_session):
    jobs._handlers["t_ok"] = lambda s, p: None
    with make_session() as s:
        jid = jobs.enqueue(s, "t_ok", {})
        j = s.get(Job, jid)
        j.status, j.attempts, j.locked_at = "running", 1, datetime.now(timezone.utc) - timedelta(minutes=5)
        s.commit()
        assert jobs.run_once(s) is False  # lock still fresh
        j.locked_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        s.commit()
        assert jobs.run_once(s) is True
        assert (j.status, j.attempts) == ("done", 2)
        j.status, j.attempts, j.locked_at = "running", 3, datetime.now(timezone.utc) - timedelta(minutes=11)
        s.commit()
        assert jobs.run_once(s) is True
        assert (j.status, j.last_error) == ("failed", "worker lost")
