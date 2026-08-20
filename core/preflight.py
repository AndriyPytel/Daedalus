"""
Preflight: run a Step against a throwaway copy of the Document before the real
one sees it.

The parent half. Owns one long-lived headless FreeCAD, feeds it work over
stdin, and enforces the timeout. Worker death is an expected outcome, not an
incident — a crash or a timeout restarts the process silently and comes back
as a traceback the assistant can correct against (ADR-0002).

Nothing here imports FreeCAD. The parent is the addon's process; the only
FreeCAD in play is the child's.

Run as a script it is a smoke check:
`python -m core.preflight "doc.addObject('Part::Box','Box')"`.
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field

from core.preflight_worker import SENTINEL

DEFAULT_TIMEOUT = 30.0
STARTUP_TIMEOUT = 60.0

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflight_worker.py")

_CANDIDATES = (
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "/usr/bin/freecadcmd",
    "/usr/local/bin/freecadcmd",
    "/usr/bin/FreeCADCmd",
)


@dataclass
class PreflightResult:
    """
    What the apply Tool sees: it worked, or here is why it did not.

    `created`, `deleted` and `modified` are what the Step actually did,
    observed rather than inferred from the code.

    `held` and `failed` are an Assertion check's own measured lists
    (`core/preflight_worker.py :: _measured`) — `None` where nothing measured
    them, an ordinary Step included, rather than `[]`, which would read as
    "measured, and nothing held or failed".
    """

    ok: bool
    traceback: str = ""
    render: str = None
    objects: list = field(default_factory=list)
    created: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    held: list = None
    failed: list = None

    @classmethod
    def from_response(cls, response):
        """
        A worker's reply, as the result callers see.

        The wire shape is read here and nowhere else. It used to be spelled out
        again at the one call site, which is how a field the worker starts
        sending arrives at the parent and stops there — the two halves are
        separate processes and nothing links their spellings but this.

        `held` and `failed` are read on both branches: an Assertion check's
        `AssertionError` is exactly the branch where they carry the measured
        lists, not only the `ok` one.
        """
        held, failed = response.get("held"), response.get("failed")
        if not response.get("ok"):
            return cls(ok=False, traceback=response.get("traceback", "Preflight failed"),
                       held=held, failed=failed)
        return cls(ok=True,
                   render=response.get("render"),
                   objects=response.get("objects", []),
                   created=response.get("created", []),
                   deleted=response.get("deleted", []),
                   modified=response.get("modified", []),
                   held=held, failed=failed)


def find_freecadcmd():
    """Locate the headless FreeCAD binary, or raise saying how to point at it."""
    explicit = os.environ.get("FREECAD_CMD")
    if explicit:
        return explicit
    for path in _CANDIDATES:
        if os.path.exists(path):
            return path
    from shutil import which
    found = which("freecadcmd") or which("FreeCADCmd")
    if found:
        return found
    raise RuntimeError(
        "No headless FreeCAD found. Set FREECAD_CMD to the path of freecadcmd."
    )


class PreflightWorker:
    """
    One warm headless FreeCAD, restarted whenever it dies.

    Callers only ever see a PreflightResult. Whether that came from a clean
    run, a Python traceback, a segfault or a timeout is this class's business.
    """

    def __init__(self, freecadcmd=None, timeout=DEFAULT_TIMEOUT):
        self._freecadcmd = freecadcmd or find_freecadcmd()
        self.timeout = timeout
        self._process = None
        self._lines = None
        self._reader = None
        self._stderr = None
        self._draining = None
        self.restarts = 0

    @property
    def alive(self):
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self):
        return self._process.pid if self._process else None

    def start(self):
        """
        Bring the worker up and wait for it to say it is ready.

        The pipes are decoded as UTF-8 rather than by the locale: inside
        FreeCAD.app neither LANG nor LC_ALL is set, so the locale resolves to
        ascii and the first non-ASCII byte the worker emits kills the thread
        reading that stream.
        """
        if self.alive:
            return
        self._process = subprocess.Popen(
            [self._freecadcmd, _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=dict(os.environ, PYTHONUNBUFFERED="1"),
        )
        self._lines = queue.Queue()
        self._stderr = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._draining = threading.Thread(target=self._drain_stderr, daemon=True)
        self._draining.start()

        ready = self._await_response(STARTUP_TIMEOUT)
        if ready is None or not ready.get("ready"):
            self.kill()
            raise RuntimeError("Preflight Worker did not come up: " + self._stderr_text())

    def kill(self):
        if self._process is None:
            return
        try:
            self._process.kill()
            self._process.wait(timeout=5)
        except Exception:
            pass
        self._process = None

    def restart(self):
        """
        Silent by design — the user is never told the worker died, and silent
        about not coming back either.

        A replacement that cannot be started is a request without an answer,
        not an exception thrown at a caller who asked for a reading: the
        reading check may cost a reading and never the run. The worker stays
        dead and the next request tries again.
        """
        self.kill()
        self.restarts += 1
        try:
            self.start()
        except Exception:
            pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.kill()

    def check(self, code, document=None, render_path=None, timeout=None):
        """
        Run *code* against a fresh copy of *document* in the worker.

        Returns a PreflightResult. Never raises for anything the code did —
        a crash and a timeout are results, not exceptions.
        """
        response, why = self._ask(
            {"code": code, "document": document, "render_path": render_path},
            timeout or self.timeout, "the Step")
        if response is None:
            return PreflightResult(ok=False, traceback=why)
        return PreflightResult.from_response(response)

    def reading(self, request, timeout=None):
        """
        Put one traced outline to the solver in the worker, and hand back its reply.

        None where the worker could not answer — it died on this reading, or it
        hung — because a reading nobody could check and a reading with nothing
        wrong come to the same thing upstream: no conflict on the description.
        The dying is the point. `Sketcher.Constraint` is what segfaults
        (FCC-19), and here it is fed geometry measured off a drawing rather
        than code the model wrote (FCC-126).
        """
        response, _ = self._ask(request, timeout or self.timeout, "the reading")
        return response if response and response.get("ok") else None

    def _ask(self, request, timeout, doing):
        """
        One request down the pipe, one reply back, and a dead worker replaced.

        Hands back the reply, or None and the sentence saying why there is
        none — which the caller phrases as whatever its own result is. Never
        raises: a child that will not come up at all is one more reason for no
        reply, not an exception for a caller who asked a question.

        A worker can die between the liveness check and the write, so its last
        words are read before the restart replaces them with a live worker's
        silence.
        """
        if not self.alive:
            try:
                self.start()
            except Exception as why:
                return None, ("The Preflight Worker could not be started, so %s was "
                              "not run: %s" % (doing, why))

        try:
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, ValueError):
            died = self._died_message("before it could start")
            self.restart()
            return None, died

        response = self._await_response(timeout)
        if response is not None:
            return response, None

        timed_out = self.alive
        died = None if timed_out else self._died_message("while running " + doing)
        self.restart()
        if timed_out:
            return None, ("Preflight timed out after %ss — %s did not finish. An "
                          "unbounded loop or a runaway recompute is the usual cause."
                          % (timeout, doing))
        return None, died

    def _pump(self):
        """
        Forward tagged responses; FreeCAD's own banner on stdout is noise.

        The None put on the queue at the end is the only "the worker is gone"
        signal there is. It is sent from a finally so that a reader dying is a
        prompt visible failure rather than every pending request waiting out
        its timeout.
        """
        process = self._process
        lines = self._lines
        try:
            for line in process.stdout:
                if line.startswith(SENTINEL):
                    lines.put(line[len(SENTINEL):])
        finally:
            lines.put(None)

    def _drain_stderr(self):
        process, sink = self._process, self._stderr
        for line in process.stderr:
            sink.append(line)

    def _await_response(self, timeout):
        try:
            raw = self._lines.get(timeout=timeout)
        except queue.Empty:
            return None
        if raw is None:
            return None
        return json.loads(raw)

    def _stderr_text(self):
        return "".join(self._stderr or [])[-4000:]

    def _last_words(self):
        """
        What the worker said on the way out.

        The drain thread bound this worker's stderr when it started and only
        returns at EOF, which death is; without the join the last line is lost
        to a race instead of to the reset it used to be lost to.
        """
        if not self.alive and self._draining is not None:
            self._draining.join(timeout=1.0)
        return self._stderr_text()

    def _died_message(self, when):
        detail = self._last_words().strip()
        return (
            "The Preflight Worker died %s, so this code would have taken FreeCAD "
            "down with it. Rewrite the Step to avoid whatever crashed the core.%s"
            % (when, ("\n\n" + detail) if detail else "")
        )


def copy_of(document):
    """
    Save a live Document to a throwaway file for the worker to open.

    Returns the path; the caller owns it and should delete it.
    """
    handle, path = tempfile.mkstemp(prefix="preflight_", suffix=".FCStd")
    os.close(handle)
    document.saveCopy(path)
    return path


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "doc.addObject('Part::Box', 'Box')"
    with PreflightWorker() as worker:
        result = worker.check(code, render_path="/tmp/preflight_smoke.png")
        print("ok        :", result.ok)
        print("objects   :", [o["name"] for o in result.objects])
        print("render    :", result.render)
        if result.traceback:
            print(result.traceback)
