"""
One `Worker` per Session, started on its own thread so the request that made the Run answers at once.

A Run whose worker cannot start, or whose Session was closed first, is failed with an `error` event rather than left queued.
`cancel` stops a Run its worker is running, or marks one still queued so it is never sent.
"""
import threading
from collections import defaultdict

from service import db
from service.repository import Repository
from service.supervisor import Worker


class WorkerPool:
    def __init__(self, argv, database_url, blob_store=None):
        self._store = blob_store
        self._argv = list(argv)
        self._url = database_url
        self._workers = {}
        self._closed = set()
        self._cancelled = set()
        self._locks = defaultdict(threading.Lock)

    def submit(self, owner_id, session_id, run_id, prompt, attachments=(), interactive=False, confirm=False,
               settings=None):
        threading.Thread(target=self._launch, args=(owner_id, session_id, run_id, prompt, attachments, interactive, confirm,
                                                    settings),
                         daemon=True).start()

    def cancel(self, owner_id, session_id, run_id):
        worker = self._workers.get(session_id)
        try:
            if worker is None:
                raise LookupError(run_id)
            worker.cancel(run_id)
        except LookupError:
            self._cancelled.add(run_id)
            with db.connect(self._url) as conn:
                Repository(conn).transition(owner_id, run_id, "cancelled", "queued")

    def answer(self, session_id, text):
        worker = self._workers.get(session_id)
        if worker is None or not worker.alive:
            raise RuntimeError("the Session has no worker")
        worker.answer(text)

    def close(self, session_id):
        self._closed.add(session_id)
        with self._locks[session_id]:
            worker = self._workers.pop(session_id, None)
        if worker is not None:
            worker.stop()

    def close_all(self):
        for session_id in list(self._workers):
            self.close(session_id)

    def _launch(self, owner_id, session_id, run_id, prompt, attachments, interactive, confirm, settings):
        try:
            with self._locks[session_id]:
                if session_id in self._closed:
                    raise RuntimeError("the Session was closed")
                worker = self._workers.get(session_id)
                if worker is None or not worker.alive:
                    worker = Worker(self._argv, self._url, owner_id, session_id, blob_store=self._store)
                    worker.start()
                    self._workers[session_id] = worker
                if run_id not in self._cancelled:
                    worker.send(prompt, run_id, attachments, interactive, confirm, settings)
        except Exception as error:
            with db.connect(self._url) as conn:
                Repository(conn).fail_run(owner_id, run_id, "%s: %s" % (type(error).__name__, error))
