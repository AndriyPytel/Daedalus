"""
The gateway side of the worker channel: a `Worker` spawns one configured argv per Session and stores its events through `Repository`, so each notifies, and a `cost` event adds its `usd` to the Run.
A worker that exits with a Run active fails that Run only, `cancel` asks the worker to stop and kills its process group after `CANCEL_TIMEOUT` so the Run ends `cancelled` either way, and a `question` from a Run sent `interactive` moves it to `needs-answer` until `answer`.
When the worker ends, by itself or by `stop`, its process group is killed.
"""
import json
import os
import signal
import subprocess
import threading

from service import db
from service.repository import Repository

TERMINAL = ("completed", "blocked", "budget_exhausted", "stopped", "aborted", "failed", "cancelled")
READY_TIMEOUT = 60.0
CANCEL_TIMEOUT = 30.0


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
        self._interactive = False
        self._cancelling = False

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

    def send(self, prompt, run_id=None, interactive=False, confirm=False):
        with self._lock:
            if not self.alive:
                raise RuntimeError("the worker has exited")
            if self._run_id is not None:
                raise RuntimeError("a Run is already active in this Session")
            if run_id is None:
                run_id = self._repo.create_run(self.owner_id, self.session_id, prompt)["id"]
            self._repo.update_run(self.owner_id, run_id, state="running")
            self._run_id, self._last_kind = run_id, None
            self._interactive, self._cancelling = interactive, False
        self._write(op="send", prompt=prompt, interactive=interactive, confirm=confirm)
        return run_id

    def cancel(self, run_id):
        with self._lock:
            if self._run_id != run_id:
                raise LookupError(run_id)
            self._cancelling = True
        self._write(op="cancel")
        timer = threading.Timer(CANCEL_TIMEOUT, self._force_cancel, (run_id,))
        timer.daemon = True
        timer.start()

    def answer(self, text):
        self._write(op="answer", text=text)

    def _write(self, **message):
        try:
            self._process.stdin.write((json.dumps(message) + "\n").encode())
            self._process.stdin.flush()
        except OSError:
            pass  # except-pass-allowed: the reader sees the worker's end and fails the Run

    def _force_cancel(self, run_id):
        with self._lock:
            if self._run_id != run_id:
                return
            self._finish("cancelled", "cancelled", {"reason": "the worker did not stop in time and was killed"})
        self._kill_group()

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
                usd = (message.get("data") or {}).get("usd")
                if message["kind"] == "cost" and isinstance(usd, (int, float)) and not isinstance(usd, bool):
                    self._repo.add_run_cost(self.owner_id, self._run_id, usd)
            elif op == "question" and self._run_id is not None and self._interactive:
                self._repo.transition(self.owner_id, self._run_id, "needs-answer", "running")
            elif op == "done" and self._run_id is not None:
                if self._cancelling and self._last_kind in ("stopped", "budget_exhausted", "aborted"):
                    self._finish("cancelled", "cancelled", {"reason": "cancelled by the client"})
                else:
                    self._repo.update_run(self.owner_id, self._run_id,
                                          state=self._last_kind if self._last_kind in TERMINAL else "completed")
                    self._run_id = None
            elif op == "error" and self._run_id is not None:
                self._finish("error", "failed", {"message": str(message.get("message"))})

    def _finish(self, kind, state, payload):
        self._repo.append_event(self.owner_id, self._run_id, kind, payload)
        self._repo.update_run(self.owner_id, self._run_id, state=state)
        self._run_id = None
