"""
Preflight: run a Step against a throwaway copy of the Document before the real
one sees it.

The parent half. Owns one long-lived headless FreeCAD, feeds it work over
stdin, and enforces the timeout. Worker death is an expected outcome, not an
incident — a crash or a timeout restarts the process silently and comes back
as a traceback the assistant can correct against (ADR-0002).

Nothing here imports FreeCAD. The parent is the addon's process; the only
FreeCAD in play is the child's.
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
    """What the apply Tool sees: it worked, or here is why it did not."""

    ok: bool
    traceback: str = ""
    render: str = None
    objects: list = field(default_factory=list)
    saved: str = None
    # What the Step actually did, observed rather than inferred from the code.
    created: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    modified: list = field(default_factory=list)


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
        self.restarts = 0

    # ── process lifecycle ────────────────────────────────────────────────────

    @property
    def alive(self):
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self):
        return self._process.pid if self._process else None

    def start(self):
        if self.alive:
            return
        self._process = subprocess.Popen(
            [self._freecadcmd, _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=dict(os.environ, PYTHONUNBUFFERED="1"),
        )
        self._lines = queue.Queue()
        self._stderr = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

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
        """Silent by design — the user is never told the worker died."""
        self.kill()
        self.restarts += 1
        self.start()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.kill()

    # ── the one thing this class is for ──────────────────────────────────────

    def check(self, code, document=None, render_path=None, save_to=None, timeout=None):
        """
        Run *code* against a fresh copy of *document* in the worker.

        Returns a PreflightResult. Never raises for anything the code did —
        a crash and a timeout are results, not exceptions.
        """
        if not self.alive:
            self.start()

        request = {"code": code, "document": document,
                   "render_path": render_path, "save_to": save_to}
        try:
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, ValueError):
            # It died between the liveness check and the write.
            self.restart()
            return PreflightResult(ok=False, traceback=self._died_message("before it could start"))

        response = self._await_response(timeout or self.timeout)

        if response is None:
            timed_out = self.alive
            self.restart()
            if timed_out:
                return PreflightResult(
                    ok=False,
                    traceback="Preflight timed out after %ss. The Step did not finish — "
                              "an unbounded loop or a runaway recompute is the usual cause."
                              % (timeout or self.timeout),
                )
            return PreflightResult(ok=False, traceback=self._died_message("while running the Step"))

        if response.get("ok"):
            return PreflightResult(ok=True, render=response.get("render"),
                                   objects=response.get("objects", []),
                                   saved=response.get("saved"),
                                   created=response.get("created", []),
                                   deleted=response.get("deleted", []),
                                   modified=response.get("modified", []))
        return PreflightResult(ok=False, traceback=response.get("traceback", "Preflight failed"))

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _pump(self):
        """Forward tagged responses; FreeCAD's own banner on stdout is noise."""
        process = self._process
        lines = self._lines
        for line in process.stdout:
            if line.startswith(SENTINEL):
                lines.put(line[len(SENTINEL):])
        lines.put(None)   # EOF: the worker is gone

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

    def _died_message(self, when):
        detail = self._stderr_text().strip()
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
    # Smoke check: python -m core.preflight "doc.addObject('Part::Box','Box')"
    code = sys.argv[1] if len(sys.argv) > 1 else "doc.addObject('Part::Box', 'Box')"
    with PreflightWorker() as worker:
        result = worker.check(code, render_path="/tmp/preflight_smoke.png")
        print("ok        :", result.ok)
        print("objects   :", [o["name"] for o in result.objects])
        print("render    :", result.render)
        if result.traceback:
            print(result.traceback)
