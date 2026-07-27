"""
The path a Step takes from generated code to the Document:

    lint  ->  Preflight  ->  the Qt main thread  ->  a transaction

Callers see "applied" or "failed, here is the traceback". Preflight sits
*behind* this, not beside it, so it never becomes a third seam.

Document operations are marshalled to the Qt main thread and block the caller
until they return, so there are never concurrent mutations of the Document.
"""
import os
import threading
from dataclasses import dataclass, field

from core.lint import lint
from core.preflight import PreflightWorker, copy_of
from core.preflight_worker import step_namespace

DISPATCH_TIMEOUT = 60.0

_dispatcher = None
_dispatcher_lock = threading.Lock()


@dataclass
class ApplyResult:
    """All the loop ever learns about a Step: it landed, or here is why not."""

    ok: bool
    traceback: str = ""
    render: str = None
    objects: list = field(default_factory=list)
    created: list = field(default_factory=list)

    def as_tool_result(self) -> dict:
        if self.ok:
            return {"status": "applied", "created": self.created,
                    "objects": self.objects, "render": self.render}
        return {"status": "failed", "traceback": self.traceback}


# ── the Qt main thread ────────────────────────────────────────────────────────


def _qt():
    try:
        from PySide2 import QtCore, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui as QtWidgets
    return QtCore, QtWidgets


class _Dispatcher:
    """
    Runs a callable on the Qt main thread and blocks until it returns.

    A queued signal to an object living in the main thread is the mechanism.
    `QTimer.singleShot(0, func)` — what the old core fell back to — creates the
    timer in the *calling* thread, so on a worker thread with its own event
    loop it fires there instead. That is a Document mutation off the main
    thread, which is how FreeCAD gets taken down.
    """

    def __init__(self):
        QtCore, QtWidgets = _qt()
        app = QtWidgets.QApplication.instance() or QtCore.QCoreApplication.instance()
        if app is None:
            raise RuntimeError("No Qt application; nothing to marshal onto.")

        class Relay(QtCore.QObject):
            job = QtCore.Signal(object)

            def __init__(self):
                super().__init__()
                self.moveToThread(app.thread())
                self.job.connect(self._run)

            @QtCore.Slot(object)
            def _run(self, run_it):
                run_it()

        self._app = app
        self._qtcore = QtCore
        self._relay = Relay()

    def on_main_thread(self, func, timeout=DISPATCH_TIMEOUT):
        QtCore = self._qtcore
        if QtCore.QThread.currentThread() == self._app.thread():
            return func()

        outcome = {}
        done = threading.Event()

        def run():
            try:
                outcome["value"] = func()
            except BaseException as exc:      # re-raised on the calling thread
                outcome["error"] = exc
            finally:
                done.set()

        self._relay.job.emit(run)
        if not done.wait(timeout):
            raise TimeoutError(
                "The Document operation did not return within %ss on the main thread." % timeout
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]


def on_main_thread(func, timeout=DISPATCH_TIMEOUT):
    """Marshal *func* onto the Qt main thread; run it here if we are already there."""
    global _dispatcher
    with _dispatcher_lock:
        if _dispatcher is None:
            _dispatcher = _Dispatcher()
    return _dispatcher.on_main_thread(func, timeout)


# ── the transaction ───────────────────────────────────────────────────────────


# A Step that raises is a failure the assistant corrects against; a Step that
# quietly does nothing was scored as a success, and the Plan moved on assuming
# an object that was never created (ADR-0010). Seen once already: code that
# defined a function to build the sketch and never called it — the two removed
# APIs inside it never even ran.
_CHANGED_NOTHING = (
    "The code ran without error but changed nothing: no object was created, "
    "deleted or modified. A Step that leaves the Document as it found it has "
    "not been done. Check that the code actually calls what it defines and "
    "assigns to the Document's objects — or, if this Step genuinely has nothing "
    "left to do, say so with {\"obsolete\": \"why\"} instead."
)


def run_in_transaction(doc, code, label="Copilot Step"):
    """
    Execute *code* against *doc* as one undoable unit. Main thread only.

    Everything the Step does — including the recompute — is inside the
    transaction, so one undo takes the whole Step back.
    """
    doc.openTransaction(label)
    try:
        exec(compile(code, "<step>", "exec"), step_namespace(doc))  # noqa: S102
        doc.recompute()
    except BaseException:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


# ── the pipeline ──────────────────────────────────────────────────────────────


def refuse_without_asking(kinds):
    """
    The default answer when nothing is wired up to ask the user.

    Fails closed: with no way to get consent, the user's own work is not
    touched. The panel replaces this with a real prompt.
    """
    return False


class ApplyPipeline:
    """
    Hand it code; it lands as one undoable unit or comes back with a reason.

    Also the keeper of the Session's Owned Objects: it is the only thing that
    knows what the assistant created, because it is the only thing that applies
    anything. A Step that would modify or delete an object outside that set
    needs the user's say-so, whatever the Plan Gate already approved.

    `dispatch`, `worker`, `transaction` and `confirm` are injected so Level 1
    can drive the whole pipeline without Qt or a headless FreeCAD.
    """

    def __init__(self, document=None, worker=None, dispatch=on_main_thread,
                 transaction=None, render_dir=None, confirm=None, owned=None):
        self._document = document
        self._worker = worker
        self._dispatch = dispatch
        self._transaction = transaction or run_in_transaction
        self._render_dir = render_dir
        self._renders = 0
        self._confirm = confirm or refuse_without_asking
        # Owned Objects, for the lifetime of this Session. Anything not in here
        # belongs to the user — including everything that existed before we did.
        self.owned = set(owned or ())

    @property
    def worker(self) -> PreflightWorker:
        if self._worker is None:
            self._worker = PreflightWorker()
        return self._worker

    def _active(self):
        doc = self._document() if callable(self._document) else self._document
        if doc is not None:
            return doc
        import FreeCAD

        return FreeCAD.ActiveDocument

    def _render_path(self):
        import tempfile

        directory = self._render_dir or tempfile.gettempdir()
        self._renders += 1
        return os.path.join(directory, "copilot_step_%d.png" % self._renders)

    def apply(self, code, selection=None, label="Copilot Step") -> ApplyResult:
        checked = lint(code, selection)
        if not checked.ok:
            return ApplyResult(ok=False, traceback=checked.report())

        doc = self._dispatch(self._active)
        if doc is None:
            return ApplyResult(ok=False, traceback="There is no open Document to apply this Step to.")

        before = set(self._dispatch(lambda: [o.Name for o in doc.Objects]))

        copy = self._dispatch(lambda: copy_of(doc))
        try:
            preflighted = self.worker.check(code, document=copy, render_path=self._render_path())
        finally:
            _discard(copy)

        if not preflighted.ok:
            return ApplyResult(ok=False, traceback=preflighted.traceback)

        declined = self._not_ours(preflighted)
        if declined:
            return ApplyResult(ok=False, traceback=declined)

        if not (preflighted.created or preflighted.deleted or preflighted.modified):
            return ApplyResult(ok=False, traceback=_CHANGED_NOTHING)

        try:
            self._dispatch(lambda: self._transaction(doc, code, label))
        except BaseException as exc:
            import traceback as tb
            return ApplyResult(
                ok=False,
                traceback="The Step passed Preflight but failed against the real Document, "
                          "so it was rolled back:\n\n" + "".join(
                              tb.format_exception(type(exc), exc, exc.__traceback__)),
            )

        after = self._dispatch(lambda: [{"name": o.Name, "label": o.Label, "type": o.TypeId}
                                        for o in doc.Objects])
        created = [o["name"] for o in after if o["name"] not in before]
        self.owned.update(created)
        return ApplyResult(ok=True, render=preflighted.render, objects=after, created=created)

    def _not_ours(self, preflighted):
        """
        Ask before touching anything the assistant did not create this Session.

        Preflight already ran the Step against a copy, so this is what the code
        *did*, not what reading it suggested it might do.
        """
        modifies = sorted(set(preflighted.modified) - self.owned)
        deletes = sorted(set(preflighted.deleted) - self.owned)
        if not modifies and not deletes:
            return None

        kinds = {"modify": modifies, "delete": deletes}
        if self._confirm(kinds):
            return None

        wanted = []
        if modifies:
            wanted.append("modify " + ", ".join(modifies))
        if deletes:
            wanted.append("delete " + ", ".join(deletes))
        return (
            "This Step would %s, which you created rather than I did, and that was not "
            "approved. Achieve the Intent without changing the user's own objects, or say "
            "why it cannot be done and stop." % " and ".join(wanted)
        )


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass
