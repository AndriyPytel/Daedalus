"""
One `Worker` per Session, started on its own thread so the request that made the Run answers at once.

A Run whose worker cannot start, or whose Session was closed first, is failed with an `error` event rather than left queued.
"""
import threading
from collections import defaultdict

from service import db
from service.repository import Repository
from service.supervisor import Worker


class WorkerPool:
    def __init__(self, argv, database_url):
        self._argv = list(argv)
        self._url = database_url
        self._workers = {}
        self._closed = set()
        self._locks = defaultdict(threading.Lock)

    def submit(self, owner_id, session_id, run_id, prompt):
        threading.Thread(target=self._launch, args=(owner_id, session_id, run_id, prompt), daemon=True).start()

    def close(self, session_id):
        self._closed.add(session_id)
        with self._locks[session_id]:
            worker = self._workers.pop(session_id, None)
        if worker is not None:
            worker.stop()

    def close_all(self):
        for session_id in list(self._workers):
            self.close(session_id)

    def _launch(self, owner_id, session_id, run_id, prompt):
        try:
            with self._locks[session_id]:
                if session_id in self._closed:
                    raise RuntimeError("the Session was closed")
                worker = self._workers.get(session_id)
                if worker is None or not worker.alive:
                    worker = Worker(self._argv, self._url, owner_id, session_id)
                    worker.start()
                    self._workers[session_id] = worker
                worker.send(prompt, run_id)
        except Exception as error:
            with db.connect(self._url) as conn:
                Repository(conn).fail_run(owner_id, run_id, "%s: %s" % (type(error).__name__, error))
