"""Background jobs: work that outlives a request, such as an Autopilot run or a scheduled refresh.

Each job runs on a small pool of worker threads with a copy of the context it was submitted in, so it
acts in the same workspace, as the same person, with the same permissions. Jobs are keyed: one job per
key runs at a time (one refresh per dataset, say). Submitting with queue=True while one is running runs
the new work once the current job finishes. Jobs report progress on the record they work on; nothing
here is kept between restarts, so a job interrupted by a restart is reported as such by whoever reads
that record (see autopilot.view).

On one server this is enough. Running several servers (M18) needs a shared queue in its place.
"""

import contextvars
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)


class Jobs:
    def __init__(self, workers: int = 4):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="datawazi-job")
        self.lock = threading.Lock()
        self.running: set[str] = set()
        self.queued: dict[str, tuple[contextvars.Context, Callable[[], None]]] = {}

    def submit(self, key: str, work: Callable[[], None], queue: bool = False) -> bool:
        """Start `work` unless a job with this key is running (then, with queue, run it next). Returns whether it started."""
        context = contextvars.copy_context()
        with self.lock:
            if key in self.running:
                if queue:
                    self.queued[key] = (context, work)  # only the latest waiting job is kept
                return False
            self.running.add(key)
        self.pool.submit(self._run, key, context, work)
        return True

    def busy(self, key: str) -> bool:
        with self.lock:
            return key in self.running

    def _run(self, key: str, context: contextvars.Context, work: Callable[[], None]) -> None:
        while True:
            try:
                context.run(work)
            except Exception:  # a job reports its own failures; this only keeps the worker alive  # noqa: BLE001
                log.exception("Background job %s failed", key)
            with self.lock:
                waiting = self.queued.pop(key, None)
                if waiting is None:
                    self.running.discard(key)
                    return
            context, work = waiting
