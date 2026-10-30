import logging
import time

from fleck_api.db import session_factory
from fleck_api.jobs import run_once

POLL_S = 1.0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    make = session_factory()
    while True:
        try:
            with make() as s:
                if run_once(s):
                    continue
        except Exception:  # DB down etc.: keep the worker alive
            logging.getLogger("fleck.worker").exception("worker loop error")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
