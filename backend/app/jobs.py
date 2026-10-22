"""Background jobs: work that outlives a request, such as an Autopilot run.

Each job runs on a small pool of worker threads with a copy of the request's context, so it acts in
the same workspace, as the same person, with the same permissions as the request that started it.
Jobs report their progress on the record they work on; nothing here is kept between restarts, so a
job interrupted by a restart is marked as stopped when its record is next read (see autopilot.view).

On one server this is enough. Running several servers (M18) needs a shared queue in its place.
"""

import contextvars
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

log = logging.getLogger(__name__)


class Jobs:
    def __init__(self, workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="datawazi-job")
        self.running: dict[str, Future] = {}

    def submit(self, key: str, work: Callable[[], None]) -> bool:
        """Start `work` unless a job with the same key is still running. Returns whether it started."""
        current = self.running.get(key)
        if current is not None and not current.done():
            return False
        context = contextvars.copy_context()
        self.running[key] = self.pool.submit(context.run, self._safely, key, work)
        return True

    def busy(self, key: str) -> bool:
        current = self.running.get(key)
        return current is not None and not current.done()

    @staticmethod
    def _safely(key: str, work: Callable[[], None]) -> None:
        try:
            work()
        except Exception:  # a job reports its own failures; this only keeps the worker alive  # noqa: BLE001
            log.exception("Background job %s failed", key)
