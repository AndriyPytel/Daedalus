"""
One `Worker` per Session, started on its own thread so the request that made the Run answers at once.

At most `max_sessions` workers live at once: a Run that finds none free parks its Session as `queued` and starts, oldest first, when a slot frees, and a worker idle for `idle_ttl` seconds is stopped while its Session stays, the next Run starting a worker that restores the last checkpoint and the earlier conversation.
A Run whose worker cannot start, or whose Session was closed first, is failed with an `error` event rather than left queued, and `cancel` stops a Run its worker is running or drops one still queued.
"""
import base64
import threading
from collections import deque

from service import db
from service.repository import Repository
from service.supervisor import Worker


class WorkerPool:
    def __init__(self, argv, database_url, blob_store=None, max_sessions=2, idle_ttl=900.0):
        self._store = blob_store
        self._argv = list(argv)
        self._url = database_url
        self._max = max_sessions
        self._ttl = idle_ttl
        self._workers = {}
        self._starting = set()
        self._waiting = deque()
        self._cancelled = set()
        self._locks = {}
        self._gate = threading.Lock()
        self._stopped = threading.Event()
        self._reaper = threading.Thread(target=self._reap, daemon=True)
        self._reaper.start()

    def submit(self, owner_id, session_id, run_id, prompt, attachments=(), interactive=False, confirm=False,
               settings=None):
        job = (owner_id, session_id, run_id, prompt, attachments, interactive, confirm, settings)
        threading.Thread(target=self._launch, args=(job,), daemon=True).start()

    def cancel(self, owner_id, session_id, run_id):
        worker = self._workers.get(session_id)
        try:
            if worker is None:
                raise LookupError(run_id)
            worker.cancel(run_id)
        except LookupError:
            with db.connect(self._url) as conn:
                if Repository(conn).transition(owner_id, run_id, "cancelled", "queued") is None:
                    return
            if self._drop(lambda job: job[2] == run_id):
                self._settle(owner_id, session_id)
            else:
                self._cancelled.add(run_id)

    def answer(self, session_id, text):
        worker = self._workers.get(session_id)
        if worker is None or not worker.alive:
            raise RuntimeError("the Session has no worker")
        worker.answer(text)

    def close(self, session_id):
        for owner_id, _, run_id, *_ in self._drop(lambda job: job[1] == session_id):
            with db.connect(self._url) as conn:
                Repository(conn).fail_run(owner_id, run_id, "RuntimeError: the Session was closed")
        with self._lock_of(session_id):
            worker = self._workers.pop(session_id, None)
        if worker is not None:
            worker.stop()
        self._locks.pop(session_id, None)
        self._drain()

    def close_all(self):
        self._stopped.set()
        self._reaper.join()
        self._drop(lambda job: True)
        for session_id in list(self._workers):
            self.close(session_id)

    def _lock_of(self, session_id):
        return self._locks.setdefault(session_id, threading.Lock())

    def _drop(self, wanted):
        with self._gate:
            dropped = [job for job in self._waiting if wanted(job)]
            for job in dropped:
                self._waiting.remove(job)
        return dropped

    def _free(self):
        return self._max - len({sid for sid, worker in self._workers.items() if worker.alive} | self._starting)

    def _reserve(self, job):
        with self._gate:
            if self._free() > 0 and not self._waiting:
                self._starting.add(job[1])
                return True
            self._park(job, "queued")
            self._waiting.append(job)
            return False

    def _drain(self):
        while True:
            with self._gate:
                if not self._waiting or self._free() <= 0:
                    return
                job = self._waiting.popleft()
                self._starting.add(job[1])
            self._launch(job, reserved=True)

    def _park(self, job, state):
        with db.connect(self._url) as conn:
            Repository(conn).set_session_state(job[0], job[1], state)

    def _settle(self, owner_id, session_id):
        worker = self._workers.get(session_id)
        with db.connect(self._url) as conn:
            Repository(conn).set_session_state(owner_id, session_id, "active" if worker is not None and worker.alive else "idle")

    def _restore(self, owner_id, session_id, run_id):
        with db.connect(self._url) as conn:
            repo = Repository(conn)
            history = repo.conversation(owner_id, session_id, run_id)
            checkpoint = repo.last_checkpoint(owner_id, session_id)
        restore = {"history": history} if history else {}
        if checkpoint is not None and self._store is not None:
            restore["checkpoint"] = base64.b64encode(self._store.get(checkpoint["key"])).decode()
        return restore

    def _launch(self, job, reserved=False):
        owner_id, session_id, run_id, prompt, attachments, interactive, confirm, settings = job
        slot, parked = reserved, False
        try:
            with self._lock_of(session_id):
                if run_id in self._cancelled:
                    self._cancelled.discard(run_id)
                    return
                with db.connect(self._url) as conn:
                    session = Repository(conn).get_session(owner_id, session_id)
                if session is None or session["closed_at"] is not None:
                    raise RuntimeError("the Session was closed")
                worker, restore = self._workers.get(session_id), None
                if worker is None or not worker.alive:
                    slot = slot or self._reserve(job)
                    if not slot:
                        parked = True
                        return
                    restore = self._restore(owner_id, session_id, run_id)
                    worker = Worker(self._argv, self._url, owner_id, session_id, blob_store=self._store)
                    worker.start()
                    self._workers[session_id] = worker
                self._settle(owner_id, session_id)
                worker.send(prompt, run_id, attachments, interactive, confirm, settings, restore)
        except Exception as error:
            with db.connect(self._url) as conn:
                Repository(conn).fail_run(owner_id, run_id, "%s: %s" % (type(error).__name__, error))
        finally:
            if slot:
                with self._gate:
                    self._starting.discard(session_id)
            if not parked:
                self._settle(owner_id, session_id)
                if not reserved:
                    self._drain()

    def _reap(self):
        while not self._stopped.wait(min(1.0, self._ttl / 4)):
            for session_id in list(self._workers):
                self._expire(session_id)
            self._drain()

    def _expire(self, session_id):
        lock = self._locks.get(session_id)
        if lock is None:
            return
        with lock:
            worker = self._workers.get(session_id)
            idle = None if worker is None else worker.idle_for
            if idle is None or (worker.alive and idle < self._ttl):
                return
            del self._workers[session_id]
            worker.stop()
        self._settle(worker.owner_id, session_id)
