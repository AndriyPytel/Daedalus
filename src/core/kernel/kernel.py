"""
The kernel: a persistent headless FreeCAD, checkpointed rather than
reopened, owned by the parent half that feeds it work over stdin and
enforces the timeout; worker death is an expected outcome, not an
incident, a crash or a timeout restarts the process silently and comes
back as a traceback the assistant can correct against. A Step's code
runs directly against the kernel's live Document: a Step that succeeds
is checkpointed (`saveCopy`, no reopen), a Step that fails leaves the
Document exactly as it was by reopening the checkpoint, and `check`
called with no `document` argument continues the kernel's running
session, resuming on the last checkpoint or refusing where none exists
yet, while handing it a `document` is always a fresh handoff. Nothing
here imports FreeCAD: the parent is the addon's process, and the only
FreeCAD in play is the child's.
"""
import json
import os
import queue
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field

from core.kernel.kernel_worker import SENTINEL

DEFAULT_TIMEOUT = 30.0
STARTUP_TIMEOUT = 60.0

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel_worker.py")

_CONTINUE = object()

_CANDIDATES = (
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "/usr/bin/freecadcmd",
    "/usr/local/bin/freecadcmd",
    "/usr/bin/FreeCADCmd",
)


@dataclass
class KernelResult:
    """What a caller sees for one Step: it landed, or here is why not."""

    ok: bool
    traceback: str = ""
    render: str = None
    objects: list = field(default_factory=list)
    created: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    held: list = None
    failed: list = None
    stdout: str = ""
    geometry: dict = field(default_factory=dict)
    volume: dict = field(default_factory=dict)
    checkpoint: str = None
    names: dict = field(default_factory=dict)
    signature: dict = field(default_factory=dict)
    rolled_back: list = field(default_factory=list)
    plan_values: dict = field(default_factory=dict)

    @classmethod
    def from_response(cls, response):
        """
        A worker's reply, as the result callers see.

        The wire shape is read here and nowhere else -- a field the worker
        starts sending arrives at the parent through one spelling, since the
        two halves are separate processes and nothing else links them.

        `held` and `failed` are read on both branches -- an `AssertionError`
        carries the measured lists too, not only the `ok` branch.
        """
        held, failed = response.get("held"), response.get("failed")
        checkpoint = response.get("checkpoint")
        names = response.get("names") or {}
        plan_values = response.get("plan_values") or {}
        if not response.get("ok"):
            return cls(ok=False, traceback=response.get("traceback", "the kernel failed"),
                       held=held, failed=failed, stdout=response.get("stdout", ""),
                       volume=response.get("volume") or {},
                       checkpoint=checkpoint, names=names, plan_values=plan_values)
        return cls(ok=True,
                   render=response.get("render"),
                   objects=response.get("objects", []),
                   created=response.get("created", []),
                   deleted=response.get("deleted", []),
                   modified=response.get("modified", []),
                   held=held, failed=failed, stdout=response.get("stdout", ""),
                   geometry=response.get("geometry") or {},
                   volume=response.get("volume") or {},
                   checkpoint=checkpoint, names=names,
                   signature=response.get("signature") or {},
                   rolled_back=response.get("rolled_back") or [],
                   plan_values=plan_values)


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


class Kernel:
    """
    One warm headless FreeCAD, restarted whenever it dies, owning one live
    Document across many Steps.

    Callers only ever see a KernelResult. Whether that came from a clean
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
        self._checkpoint = None
        self._needs_resume = False
        self._design = None
        self._plan_values = {}

    @property
    def alive(self):
        return self._process is not None and self._process.poll() is None

    @property
    def plan_values(self) -> dict:
        """The name -> number Plan's kernel session currently holds."""
        return dict(self._plan_values)

    @property
    def pid(self):
        return self._process.pid if self._process else None

    def start(self):
        """
        Bring the kernel up and wait for it to say it is ready.

        The pipes are decoded as UTF-8 rather than by the locale: inside
        FreeCAD.app neither LANG nor LC_ALL is set, so the locale resolves to
        ascii and the first non-ASCII byte the kernel emits kills the thread
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
            raise RuntimeError("The kernel did not come up: " + self._stderr_text())

    def kill(self):
        if self._process is None:
            return
        try:
            self._process.kill()
            self._process.wait(timeout=5)
        except Exception:
            pass  # except-pass-allowed: the process may already be gone
        self._process = None

    def restart(self):
        """
        Silent by design -- the user is never told the kernel died, and
        silent about not coming back either. A replacement that cannot be
        started is a request without an answer, not an exception thrown at
        a caller who asked for a Step: the Step may cost a Step and never
        the run, the kernel stays dead and the next request tries again.
        `_needs_resume` marks the replacement as blank, for `check()` to
        resume from checkpoint rather than continue onto it.
        """
        self.kill()
        self._needs_resume = True
        try:
            self.start()
        except Exception:
            pass  # except-pass-allowed: check() retries from the checkpoint, this attempt need not succeed

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.kill()

    def check(self, code, document=_CONTINUE, render_path=None, timeout=None, names=None,
              ephemeral=False, design=None, exploring=False, plan_values=None,
              capture_values=False, builds=False):
        """
        Run *code* as one Step in the kernel. `document` not given
        continues the kernel's own session, resuming on the last
        checkpoint if the worker restarted since, or refusing if no
        checkpoint exists yet to resume onto; `document` given is a fresh
        handoff from that path, always honoured; *design* and *plan_values*
        are carried into the Step's namespace, both remembered and resent
        by the kernel's own session past this call the same as *names*; and
        *exploring* marks *code* as a call no Step declares, rolled back to
        the last checkpoint with the result naming what was undone where it
        creates, deletes or modifies an object. *capture_values* marks a
        Plan-phase call whose own top-level numbers fold into *plan_values*
        rather than only reading it, *builds* marks a Step or a fix that fails
        where it changes nothing in the Document, and the return is always a
        KernelResult, never a raise for anything the code did.
        """
        if document is _CONTINUE and self._needs_resume:
            if self._checkpoint is None:
                return KernelResult(ok=False, traceback=(
                    "The kernel died before any Step had landed, so there is no "
                    "checkpoint to resume from. Hand check() an explicit document "
                    "to start a session."))
            document = self._checkpoint

        if design is not None:
            self._design = design
        elif document is not _CONTINUE:
            design = self._design

        if plan_values is not None:
            self._plan_values = dict(plan_values)
        elif document is not _CONTINUE:
            plan_values = self._plan_values

        request = {"code": code}
        if document is not _CONTINUE:
            request["document"] = document
        if render_path is not None:
            request["render_path"] = render_path
        if names:
            request["names"] = names
        if ephemeral:
            request["ephemeral"] = True
        if exploring:
            request["exploring"] = True
        if design is not None:
            request["design"] = design
        if plan_values is not None:
            request["plan_values"] = plan_values
        if capture_values:
            request["capture_values"] = True
        if builds:
            request["builds"] = True
        response, why = self._ask(request, timeout or self.timeout, "the Step")
        if response is None:
            return KernelResult(ok=False, traceback=why)
        self._checkpoint = response.get("checkpoint") or self._checkpoint
        self._needs_resume = False
        if "plan_values" in response:
            self._plan_values = dict(response.get("plan_values") or {})
        return KernelResult.from_response(response)

    def reading(self, request, timeout=None):
        """Put one traced outline to the solver in the kernel, and hand back its reply."""
        response, _ = self._ask(request, timeout or self.timeout, "the reading")
        return response if response and response.get("ok") else None

    def _ask(self, request, timeout, doing):
        """
        One request down the pipe, one reply back, and a dead kernel replaced.

        Hands back the reply, or None with why -- never raises: a child that
        won't come up is one more reason for no reply, not an exception. Read
        the kernel's last words before the restart replaces them with silence.
        """
        if not self.alive:
            try:
                self.start()
            except Exception as why:
                return None, ("The kernel could not be started, so %s was "
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
            return None, ("The kernel timed out after %ss -- %s did not finish. An "
                          "unbounded loop or a runaway recompute is the usual cause."
                          % (timeout, doing))
        return None, died

    def _pump(self):
        """
        Forward tagged responses; FreeCAD's own banner on stdout is noise.

        The None put on the queue at the end is the only "kernel is gone"
        signal there is, sent from a finally so a dead reader is a visible
        failure rather than every pending request timing out.
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
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _stderr_text(self):
        return "".join(self._stderr or [])[-4000:]

    def _last_words(self):
        """
        What the kernel said on the way out.

        The drain thread bound this kernel's stderr at start and returns only
        at EOF (death); without the join, the last line is lost to a race
        rather than cleanly to the reset.
        """
        if not self.alive and self._draining is not None:
            self._draining.join(timeout=1.0)
        return self._stderr_text()

    def _died_message(self, when):
        detail = self._last_words().strip()
        return (
            "The kernel died %s, so this code would have taken FreeCAD "
            "down with it. Rewrite the Step to avoid whatever crashed the core.%s"
            % (when, ("\n\n" + detail) if detail else "")
        )


def copy_of(document):
    """
    Save a live Document to a throwaway file for the kernel to open.

    Returns the path; the caller owns it and should delete it.
    """
    handle, path = tempfile.mkstemp(prefix="kernel_", suffix=".FCStd")
    os.close(handle)
    document.saveCopy(path)
    return path
