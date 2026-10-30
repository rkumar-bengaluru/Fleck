import logging
import time

from fleck_api import calendar_sync, library  # noqa: F401  (importing registers the job handlers)
from fleck_api.db import session_factory
from fleck_api.jobs import run_once

POLL_S = 1.0
log = logging.getLogger("fleck.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    make = session_factory()
    next_schedule = 0.0
    while True:
        try:
            with make() as s:
                if time.monotonic() >= next_schedule:
                    next_schedule = time.monotonic() + calendar_sync.SCHEDULE_S
                    calendar_sync.schedule_syncs(s)
                if run_once(s):
                    continue
        except Exception:  # DB down etc.: keep the worker alive
            log.exception("worker loop error")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
