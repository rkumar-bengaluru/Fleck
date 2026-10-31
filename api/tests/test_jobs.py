from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from fleck_api import jobs
from fleck_api.models import Base, Job, Meeting


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
                assert j.status == "queued" and s.scalar(select(Job.run_after > func.now()))  # backed off
                j.run_after = func.now() - timedelta(seconds=1)
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
        j.locked_at = func.now() - timedelta(minutes=11)
        s.commit()
        assert jobs.run_once(s) is True
        assert (j.status, j.attempts) == ("done", 2)
        j.status, j.attempts, j.locked_at = "running", 3, datetime.now(timezone.utc) - timedelta(minutes=11)
        s.commit()
        assert jobs.run_once(s) is True
        assert (j.status, j.last_error) == ("failed", "worker lost")


def test_on_fail_hook_marks_meeting_failed(make_session):
    def boom(s, p):
        raise RuntimeError("no audio")

    def mark(s, p, error):
        s.get(Meeting, p["meeting_id"]).status = "failed"
        s.get(Meeting, p["meeting_id"]).failure_reason = error

    jobs.job("t_hook", on_fail=mark)(boom)
    with make_session() as s:
        m = Meeting(title="m")
        s.add(m)
        s.commit()
        jid = jobs.enqueue(s, "t_hook", {"meeting_id": m.id})
        for _ in range(3):
            jobs.run_once(s)
            s.execute(Job.__table__.update().values(run_after=func.now()))
            s.commit()
        assert s.get(Job, jid).status == "failed"
        s.refresh(m)
        assert (m.status, m.failure_reason) == ("failed", "RuntimeError: no audio")


def test_on_fail_that_raises_is_logged_not_fatal(make_session):
    def bad(s, p, error):
        raise RuntimeError("hook broke")

    jobs.job("t_hook2", on_fail=bad)(lambda s, p: 1 / 0)
    with make_session() as s:
        jid = jobs.enqueue(s, "t_hook2", {})
        s.execute(Job.__table__.update().values(attempts=2))
        s.commit()
        assert jobs.run_once(s) is True
        assert s.get(Job, jid).status == "failed"
