"""
The kernel: a persistent headless FreeCAD, checkpointed rather than reopened.

The parent half. Owns one long-lived headless FreeCAD, feeds it work over
stdin, and enforces the timeout. Worker death is an expected outcome, not an
incident -- a crash or a timeout restarts the process silently and comes back
as a traceback the assistant can correct against (ADR-0002).

A Step's code runs directly against the kernel's live Document. A Step that
succeeds is checkpointed (`saveCopy`, no reopen); a Step that fails leaves
the Document exactly as it was, by reopening the checkpoint. `check` called
with no `document` argument at all continues the kernel's running session;
handing it a `document` -- a path, or explicit `None` for an empty Document
-- is a fresh handoff that starts the session over.

Nothing here imports FreeCAD. The parent is the addon's process; the only
FreeCAD in play is the child's.

Run as a script it is a smoke check:
`python -m core.kernel "doc.addObject('Part::Box','Box')"`.
"""
import json
import os
import queue
import subprocess
import sys
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
    """
    What a caller sees for one Step: it landed, or here is why not.

    `created`, `deleted` and `modified` are what the Step actually did,
    observed rather than inferred from the code.

    `held` and `failed` are an Assertion check's own measured lists
    (`core/kernel/kernel_worker.py :: _measured`) -- `None` where nothing measured
    them, an ordinary Step included, rather than `[]`, which would read as
    "measured, and nothing held or failed".

    `geometry` is every feature's Volume and BoundBox (FCC-260), always
    present on a successful Step and empty where the kernel never reached a
    finished Document to measure at all.

    `checkpoint` is the path of the file the kernel's Document was just saved
    to (on success) or reloaded from (on failure) -- the on-disk state a
    caller can reopen and compare against, per this ticket's own TESTING
    STRATEGY.

    `names` is the requested-name -> allocated-name map after this Step, what a later Step's `doc.getObject(names[...])` addresses instead of a literal string a name collision may have moved (FCC-302). `signature` is what a replay of this Step's own code must reproduce exactly -- object count, names, total volume, and every `App::Link`/`App::LinkGroup`/`ExternalGeometry` reference's resolved target -- so a Step that ran over the wrong object is caught even where the first three agree.
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
    geometry: dict = field(default_factory=dict)
    checkpoint: str = None
    names: dict = field(default_factory=dict)
    signature: dict = field(default_factory=dict)

    @classmethod
    def from_response(cls, response):
        """
        A worker's reply, as the result callers see.

        The wire shape is read here and nowhere else. It used to be spelled out
        again at the one call site, which is how a field the worker starts
        sending arrives at the parent and stops there -- the two halves are
        separate processes and nothing links their spellings but this.

        `held` and `failed` are read on both branches: an Assertion check's
        `AssertionError` is exactly the branch where they carry the measured
        lists, not only the `ok` one.
        """
        held, failed = response.get("held"), response.get("failed")
        checkpoint = response.get("checkpoint")
        names = response.get("names") or {}
        if not response.get("ok"):
            return cls(ok=False, traceback=response.get("traceback", "the kernel failed"),
                       held=held, failed=failed, checkpoint=checkpoint, names=names)
        return cls(ok=True,
                   render=response.get("render"),
                   objects=response.get("objects", []),
                   created=response.get("created", []),
                   deleted=response.get("deleted", []),
                   modified=response.get("modified", []),
                   held=held, failed=failed,
                   geometry=response.get("geometry") or {},
                   checkpoint=checkpoint, names=names,
                   signature=response.get("signature") or {})


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

    @property
    def alive(self):
        return self._process is not None and self._process.poll() is None

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
            pass
        self._process = None

    def restart(self):
        """
        Silent by design -- the user is never told the kernel died, and silent
        about not coming back either.

        A replacement that cannot be started is a request without an answer,
        not an exception thrown at a caller who asked for a Step: the Step
        may cost a Step and never the run. The kernel stays dead and the next
        request tries again.
        """
        self.kill()
        try:
            self.start()
        except Exception:
            pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.kill()

    def check(self, code, document=_CONTINUE, render_path=None, timeout=None, names=None,
              ephemeral=False):
        """
        Run *code* as one Step in the kernel.

        `document` not given at all is the one call this class ever treats
        specially: it continues the kernel's own running session rather than
        reloading it, which is the whole point of this class over the worker
        it replaces. Passing `document` -- an explicit path, or explicit
        `None` for an empty Document -- is a fresh handoff: the session
        (Document and checkpoint) starts over from it -- the name map does
        not, when *names* hands back what an earlier Step already allocated
        (FCC-302): a caller replaying against the Document's own true state
        takes a fresh handoff on every Step, but a later Step's code still
        needs what an earlier one's name collided into.

        `ephemeral=True` skips checkpoint writes entirely: the caller will
        discard this session immediately and never reload the checkpoint.

        Returns a KernelResult. Never raises for anything the code did -- a
        crash and a timeout are results, not exceptions.
        """
        request = {"code": code}
        if document is not _CONTINUE:
            request["document"] = document
        if render_path is not None:
            request["render_path"] = render_path
        if names:
            request["names"] = names
        if ephemeral:
            request["ephemeral"] = True
        response, why = self._ask(request, timeout or self.timeout, "the Step")
        if response is None:
            return KernelResult(ok=False, traceback=why)
        return KernelResult.from_response(response)

    def reading(self, request, timeout=None):
        """
        Put one traced outline to the solver in the kernel, and hand back its
        reply.

        None where the kernel could not answer -- it died on this reading, or
        it hung -- because a reading nobody could check and a reading with
        nothing wrong come to the same thing upstream: no conflict on the
        description. The dying is the point. `Sketcher.Constraint` is what
        segfaults (FCC-19), and here it is fed geometry measured off a
        drawing rather than code the model wrote (FCC-126).
        """
        response, _ = self._ask(request, timeout or self.timeout, "the reading")
        return response if response and response.get("ok") else None

    def _ask(self, request, timeout, doing):
        """
        One request down the pipe, one reply back, and a dead kernel replaced.

        Hands back the reply, or None and the sentence saying why there is
        none -- which the caller phrases as whatever its own result is. Never
        raises: a child that will not come up at all is one more reason for no
        reply, not an exception for a caller who asked a question.

        A kernel can die between the liveness check and the write, so its
        last words are read before the restart replaces them with a live
        kernel's silence.
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

        The None put on the queue at the end is the only "the kernel is gone"
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
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _stderr_text(self):
        return "".join(self._stderr or [])[-4000:]

    def _last_words(self):
        """
        What the kernel said on the way out.

        The drain thread bound this kernel's stderr when it started and only
        returns at EOF, which death is; without the join the last line is lost
        to a race instead of to the reset it used to be lost to.
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


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "doc.addObject('Part::Box', 'Box')"
    with Kernel() as kernel:
        result = kernel.check(code, render_path="/tmp/kernel_smoke.png")
        print("ok        :", result.ok)
        print("objects   :", [o["name"] for o in result.objects])
        print("render    :", result.render)
        if result.traceback:
            print(result.traceback)
