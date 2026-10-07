"""
The gateway side of the worker channel: a `Worker` spawns one configured argv per Session and stores its events through `Repository`, so each notifies.
A line without its newline or that is not JSON is dropped, and a worker
that exits with a Run active fails that Run only. When the worker ends, by itself or by `stop`, its process group is killed.
"""
import json
import os
import signal
import subprocess
import threading

from service import db
from service.repository import Repository

TERMINAL = ("completed", "blocked", "budget_exhausted", "stopped", "aborted", "failed")
READY_TIMEOUT = 60.0


class Worker:
    def __init__(self, argv, database_url, owner_id, session_id, ready_timeout=READY_TIMEOUT):
        self._argv = list(argv)
        self._url = database_url
        self.owner_id = owner_id
        self.session_id = session_id
        self._ready_timeout = ready_timeout
        self._process = None
        self._reader = None
        self._conn = None
        self._repo = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._run_id = None
        self._last_kind = None

    @property
    def pid(self):
        return self._process.pid

    @property
    def alive(self):
        return self._process is not None and self._process.poll() is None

    def start(self):
        self._conn = db.connect(self._url)
        self._repo = Repository(self._conn)
        self._process = subprocess.Popen(self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         start_new_session=True)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        threading.Thread(target=self._reap, daemon=True).start()
        if not self._ready.wait(self._ready_timeout) or not self.alive:
            self.stop(timeout=0)
            raise RuntimeError("the worker %r did not become ready" % self._argv)

    def send(self, prompt):
        with self._lock:
            if not self.alive:
                raise RuntimeError("the worker has exited")
            if self._run_id is not None:
                raise RuntimeError("a Run is already active in this Session")
            run_id = self._repo.create_run(self.owner_id, self.session_id)["id"]
            self._repo.update_run(self.owner_id, run_id, state="running")
            self._run_id, self._last_kind = run_id, None
        try:
            self._process.stdin.write((json.dumps({"op": "send", "prompt": prompt}) + "\n").encode())
            self._process.stdin.flush()
        except OSError:
            pass  # except-pass-allowed: the reader sees the worker's end and fails the Run
        return run_id

    def stop(self, timeout=5.0):
        if self._process is None:
            return
        try:
            self._process.stdin.close()
        except OSError:
            pass  # except-pass-allowed: the worker is already gone
        try:
            self._process.wait(timeout)
        except subprocess.TimeoutExpired:
            pass  # except-pass-allowed: the group kill below ends it
        self._kill_group()
        self._process.wait()
        self._reader.join()
        self._conn.close()

    def _kill_group(self):
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # except-pass-allowed: nothing of the group is left

    def _reap(self):
        self._process.wait()
        self._kill_group()

    def _read(self):
        for raw in self._process.stdout:
            if not raw.endswith(b"\n"):
                break
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if isinstance(message, dict):
                self._handle(message)
        self._process.wait()
        with self._lock:
            if self._run_id is not None:
                self._finish("failed", "failed", {"reason": "the worker exited with code %s" % self._process.returncode})
        self._ready.set()

    def _handle(self, message):
        op = message.get("op")
        with self._lock:
            if op == "ready":
                self._ready.set()
            elif op == "event" and self._run_id is not None and isinstance(message.get("kind"), str):
                self._repo.append_event(self.owner_id, self._run_id, message["kind"], message.get("data") or {})
                self._last_kind = message["kind"]
            elif op == "done" and self._run_id is not None:
                self._repo.update_run(self.owner_id, self._run_id,
                                      state=self._last_kind if self._last_kind in TERMINAL else "completed")
                self._run_id = None
            elif op == "error" and self._run_id is not None:
                self._finish("error", "failed", {"message": str(message.get("message"))})

    def _finish(self, kind, state, payload):
        self._repo.append_event(self.owner_id, self._run_id, kind, payload)
        self._repo.update_run(self.owner_id, self._run_id, state=state)
        self._run_id = None
