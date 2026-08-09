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

from core.events import ANSWER, QUESTION
from core.freecad_tools import description
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
    description: dict = field(default_factory=dict)
    created: list = field(default_factory=list)

    def as_tool_result(self) -> dict:
        if self.ok:
            return {"status": "applied", "created": self.created,
                    "description": self.description}
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
    anything. A Step that would delete an object outside that set needs the
    user's say-so; modifying one does not, because the Plan Gate already
    approved the Step that said so.

    `dispatch`, `worker`, `transaction` and `confirm` are injected so Level 1
    can drive the whole pipeline without Qt or a headless FreeCAD.

    `events` is the running Session's stream, set per run because the pipeline
    outlives one. It is here for one reason: the Owned Object question is the
    only thing the user is ever asked that does not go through the loop, so
    without it a Session that authorised a deletion could not say so when it
    was replayed.
    """

    def __init__(self, document=None, worker=None, dispatch=on_main_thread,
                 transaction=None, render_dir=None, confirm=None, owned=None,
                 events=None):
        self._document = document
        self._worker = worker
        self._dispatch = dispatch
        self._transaction = transaction or run_in_transaction
        self._render_dir = render_dir
        self._renders = 0
        self._confirm = confirm or refuse_without_asking
        self.owned = set(owned or ())
        self.events = events

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
            # No render_path: Preflight no longer renders (ADR-0015). A
            # rasterisation per Step, in a headless process, for a picture
            # whose only reader has the real 3D view open beside it.
            preflighted = self.worker.check(code, document=copy)
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

        described = self._dispatch(lambda: description(doc))
        created = [o["name"] for o in described["features"] if o["name"] not in before]
        self.owned.update(created)
        return ApplyResult(ok=True, description=described, created=created)

    def check_assertions(self, code):
        """
        Run read-only checks against a copy of the finished Document (FCC-76).

        Deliberately past `apply`: that refuses code which changed nothing and
        wraps it in a transaction on the live Document, and both are wrong for
        a check whose whole point is to change nothing. The copy is what makes
        it read-only — this process never touches the live Document at all, so
        even a check that tried to write could not.
        """
        doc = self._dispatch(self._active)
        if doc is None:
            return {"ok": False, "traceback": "There is no open Document to check."}

        copy = self._dispatch(lambda: copy_of(doc))
        try:
            checked = self.worker.check(code, document=copy)
        finally:
            _discard(copy)
        return {"ok": bool(checked.ok), "traceback": checked.traceback or ""}

    def review_views(self):
        """
        The views Review judges, captured once at the end of a run.

        Four viewport views where there is a GUI. Where there is not, the
        Worker's rasteriser instead — one isometry, called once, so FCC-46's
        per-pixel depth test and edges are not thrown away (ADR-0015).
        """
        from core.freecad_tools import REVIEW_VIEWS, capture_review_views

        paths = [self._render_path() for _ in REVIEW_VIEWS]
        views = self._dispatch(lambda: capture_review_views(paths))
        return views or self._rasterised()

    def _rasterised(self):
        """
        The headless fallback: the Worker draws the Document once.

        A Step of `pass` because the Worker renders what a Step left behind,
        and what this wants a picture of is the Document as it now stands. One
        call per run rather than one per Step, which is the whole point.
        """
        doc = self._dispatch(self._active)
        if doc is None:
            return []
        copy = self._dispatch(lambda: copy_of(doc))
        try:
            drawn = self.worker.check("pass", document=copy,
                                      render_path=self._render_path())
        finally:
            _discard(copy)
        return [drawn.render] if drawn.ok and drawn.render else []

    def _narrate(self, kind, **data):
        """Say it on the stream, if a run is listening. Headless, nobody is."""
        if self.events is not None:
            self.events.emit(kind, **data)

    def _not_ours(self, preflighted):
        """
        Ask before deleting anything the assistant did not create this Session.

        Modification is not asked about: the Plan Gate approved a Step whose
        prose said the object changes, and asking again per Step is asking twice
        for the same consent. Deletion is never in that prose — Execute decides
        it on its own — so it keeps the question.

        Preflight already ran the Step against a copy, so this is what the code
        *did*, not what reading it suggested it might do.

        Asked and answered on the stream, like every other question put to the
        user. It is the one that does not come through the loop, so it is the
        one that would otherwise vanish from a replayed Session — leaving a
        deleted object with nothing anywhere saying it was allowed.
        """
        deletes = sorted(set(preflighted.deleted) - self.owned)
        if not deletes:
            return None

        wanted = "delete " + ", ".join(deletes)
        self._narrate(QUESTION, text="The assistant wants to " + wanted + ".")

        # Only an explicit yes is one. Whoever asks releases waiting questions
        # somehow when a run is stopped, and the panel releases this one with
        # the Plan Gate's answer — an object, and so truthy. Read as consent,
        # that let Stop delete the user's own work.
        allowed = self._confirm({"delete": deletes}) is True
        self._narrate(ANSWER, by="user",
                      text=("You allowed it to " if allowed else "You refused to let it ")
                           + wanted + ".")
        if allowed:
            return None

        return (
            "This Step would delete %s, which you created rather than I did, and that was "
            "not approved. Achieve the Intent without deleting the user's own objects, or "
            "say why it cannot be done and stop." % ", ".join(deletes)
        )


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass
