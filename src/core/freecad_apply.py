"""
The path a Step takes from generated code to the Document: lint, Preflight,
the Qt main thread, a transaction, with Preflight behind this seam rather
than beside it, so callers see only "applied" or "failed, here is the
traceback". Document operations are marshalled to the Qt main thread and
block the caller until they return, so there are never concurrent mutations
of the Document.
"""
import os
import time
from dataclasses import dataclass, field

from core.document_tools import _SCAFFOLDING, _direct, _measurable, description
from core.events import ANSWER, QUESTION, WARNING
from core.kernel import Kernel, KernelResult, copy_of
from core.kernel.step_namespace import (NamedDoc, register_step_source, signature_of,
                                        signatures_match, step_namespace, trimmed_traceback)
from core.lint import lint
from core.prompts import read_prompt


class Closing(Exception):
    """
    The Document operation was abandoned: FreeCAD is closing.

    Raised by `Mod.bridge.main_thread.on_main_thread`, the marshaller
    the engine's own Qt-free `apply()` shares this exception type with.

    Not a `RuntimeError`: `bridge.logger._console` treats that one as "no Qt
    application" and writes straight to `FreeCAD.Console` off the main
    thread, which is the cross-thread call that segfaults.
    """


class SignatureMismatch(Exception):
    """
    A Step's replay did not land what the kernel verified.

    Raised inside the transaction, before `commitTransaction` -- a Step that
    fails this check never lands, the same as one whose code raised.
    """

    def __init__(self, landed, expected):
        self.landed = landed
        self.expected = expected
        super().__init__(
            "The replayed Step's signature did not match what the kernel "
            "verified -- expected %r, landed %r. It was rolled back rather "
            "than silently accepted." % (expected, landed)
        )


@dataclass
class ApplyResult:
    """
    All the loop ever learns about a Step: it landed, or here is why not; `drift` is set only by a `SignatureMismatch`.
    `gate` names the refusal branch -- "lint", "changed_nothing", "no_document", "ownership", "rollback" or "drift", and none where the kernel's own run failed.
    `said` is what the kernel reported of that run -- volume, stdout, geometry -- for the model's next turn.
    """

    ok: bool
    traceback: str = ""
    description: dict = field(default_factory=dict)
    created: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    drift: bool = False
    gate: str = ""
    said: dict = field(default_factory=dict)

    def as_tool_result(self) -> dict:
        if self.ok:
            return {**self.said, "status": "applied", "created": self.created,
                    "modified": self.modified, "description": self.description}
        return {**self.said, "status": "failed", "traceback": self.traceback,
                "drift": self.drift, "gate": self.gate}


def _said(rehearsal):
    said = {}
    for key in ("stdout", "volume", "rolled_back"):
        if getattr(rehearsal, key):
            said[key] = getattr(rehearsal, key)
    if rehearsal.ok:
        touched = set(rehearsal.created) | set(rehearsal.modified)
        said["deleted"] = rehearsal.deleted
        said["geometry"] = {name: g for name, g in rehearsal.geometry.items() if name in touched}
    return said


_CHANGED_NOTHING = read_prompt("changed_nothing.md")

STEP_LIMIT = 120
STEP_LABEL = "Daedalus Step"


def run_in_transaction(doc, code, label=STEP_LABEL, names=None, expected_signature=None):
    """
    Execute *code* against *doc* as one undoable unit, main thread only; the
    recompute runs inside the transaction, so one undo takes the whole Step
    back. *names* is the allocated-name map Preflight already ran this
    same Step against, carried so Apply resolves names identically. *expected_signature*
    is what Preflight verified; `None` skips the check, a mismatch aborts the
    transaction.
    """
    names = {} if names is None else names
    doc.openTransaction(label)
    try:
        register_step_source(code)
        namespace = step_namespace(NamedDoc(doc, names))
        namespace["names"] = names
        exec(compile(code, "<step>", "exec"), namespace)  # noqa: S102
        doc.recompute()
        if expected_signature:
            landed = signature_of(doc)
            if not signatures_match(landed, expected_signature):
                raise SignatureMismatch(landed, expected_signature)
    except BaseException:
        doc.abortTransaction()
        raise
    doc.commitTransaction()


_A_CORRECTION = 1

_MOVES_MATERIAL = "PartDesign::Feature"
_A_BODY = "PartDesign::Body"


def _is_modelling(obj):
    """
    Whether deleting this object takes a piece of the model with it.

    A sketch is not one: a Pad and its sketch are one change and go
    together, so counting both would call a correction a rebuild. A Body
    counts as one, since deleting it takes every feature with it.
    """
    derived = getattr(obj, "isDerivedFrom", None)
    if derived is not None:
        try:
            return bool(derived(_MOVES_MATERIAL) or derived(_A_BODY))
        except Exception:
            pass  # except-pass-allowed: falls through to the TypeId check below
    type_id = getattr(obj, "TypeId", "") or ""
    return type_id == _A_BODY or type_id.startswith("PartDesign::")


def refuse_without_asking(kinds):
    """
    The default answer when nothing is wired up to ask the user.

    Fails closed: with no way to get consent, the user's own work is not
    touched. The panel replaces this with a real prompt.
    """
    return False


CHAT_DOCK = "DaedalusPanel"


def lock_gui(window, locked, chat=CHAT_DOCK):
    """
    Hold the main window against the user for the run, except the chat dock
    that carries Build, Stop and the composer -- disabling the whole window
    would leave the run waiting at the Gate for an answer nobody can give.
    Qt disables a subtree with its parent, so the exemption is made widget by
    widget rather than by re-enabling the dock; a `QAction` is not a widget
    and its state is the application's own.
    """
    for child in window.children():
        if not child.isWidgetType() or child.objectName() == chat:
            continue
        child.setEnabled(not locked)


class ApplyPipeline:
    """
    Hand it code; it lands as one undoable unit or comes back with a reason.
    Also the keeper of the Session's Owned Objects — the only thing that knows
    what the assistant created; deleting one outside that set needs the user's
    say-so.

    `_names` is the requested-name -> allocated-name map, accumulated across
    every Step and reset by `start_run`, so a later Step reaches what an
    earlier one created, not a same-named Document object.
    """

    def __init__(self, document=None, worker=None, dispatch=None,
                 transaction=None, confirm=None, owned=None,
                 events=None, main_window=None, unattended=False, clock=time.monotonic):
        self._clock = clock
        self._document = document
        self._worker = worker
        self._dispatch = dispatch or _direct
        self._transaction = transaction or run_in_transaction
        self._confirm = confirm or refuse_without_asking
        self.owned = set(owned or ())
        self._unattended = unattended
        self.events = events
        self._main_window = main_window
        self.locked = False
        self._names = {}
        self._rules = None

    @property
    def worker(self) -> Kernel:
        if self._worker is None:
            self._worker = Kernel()
        return self._worker

    @property
    def main_window(self):
        if self._main_window is None:
            import FreeCADGui as Gui

            self._main_window = Gui.getMainWindow()
        return self._main_window

    def shutdown(self):
        """
        Let the headless FreeCAD go.

        It is a child process of the addon's, and nothing else ever ends it —
        left running, it outlives the FreeCAD the user just closed.
        """
        if self._worker is not None:
            self._worker.kill()

    def start_run(self, rules=None) -> KernelResult:
        """
        Seed the kernel from the live Document under the Domain's *rules*, the one seed of a run, and forget the names the last run allocated.

        The seed leaves a checkpoint, so a failed first Step reloads it and a restarted kernel resumes on it.
        """
        self._names = {}
        self._rules = rules
        return self._seed()

    def _seed(self) -> KernelResult:
        doc = self._dispatch(self._active)
        if doc is None:
            return self.worker.seed(rules=self._rules)
        copy = self._dispatch(lambda: copy_of(doc))
        try:
            return self.worker.seed(copy, self._rules)
        finally:
            discard(copy)

    def lock(self):
        """
        Lock the GUI for the run, which keeps its state comparable to the kernel's.

        Only a run with a window to lock calls it; `end_run` unlocks.
        """
        self._dispatch(lambda: lock_gui(self.main_window, True))
        self.locked = True

    def end_run(self):
        """Unlock the GUI: the run is over, by whichever outcome ended it."""
        self._dispatch(lambda: lock_gui(self.main_window, False))
        self.locked = False

    def _active(self):
        """
        Calls `document` if callable; returns it as given otherwise.

        No `FreeCAD.ActiveDocument` fallback: every production caller
        passes a concrete Document, so a provider answering `None`
        means there is none, not "ask the process".
        """
        return self._document() if callable(self._document) else self._document

    def apply(self, code, selection=None) -> ApplyResult:
        """
        The whole `code` call: lint with *selection*, rehearse in the kernel, refuse a Step that changes nothing, then land it on the live Document as one transaction.
        A Step the Document refuses after the kernel took it leaves the kernel ahead of the Document, so the kernel is seeded from the Document again.
        """
        checked = lint(code, selection)
        if not checked.ok:
            return ApplyResult(ok=False, traceback=checked.report(), gate="lint")

        rehearsal = self.worker.rehearse(code)
        said = _said(rehearsal)
        if not rehearsal.ok:
            return ApplyResult(ok=False, traceback=rehearsal.traceback, said=said)
        if not (rehearsal.created or rehearsal.deleted or rehearsal.modified):
            return ApplyResult(ok=False, traceback=_CHANGED_NOTHING, gate="changed_nothing",
                               said=said)

        result = self._land(code, rehearsal)
        result.said = said
        if not result.ok:
            self._seed()
        return result

    def _land(self, code, rehearsal) -> ApplyResult:
        doc = self._dispatch(self._active)
        if doc is None:
            return ApplyResult(ok=False, traceback="There is no open Document to apply this Step to.",
                               gate="no_document")

        before = set(self._dispatch(lambda: [o.Name for o in doc.Objects]))

        declined = self._refused(rehearsal.deleted, doc)
        if declined:
            return ApplyResult(ok=False, traceback=declined, gate="ownership")

        started = self._clock()
        try:
            self._dispatch(lambda: self._transaction(
                doc, code, STEP_LABEL, names=self._names,
                expected_signature=rehearsal.signature))
        except Closing:
            raise
        except SignatureMismatch as exc:
            return ApplyResult(ok=False, traceback=str(exc), drift=True, gate="drift")
        except BaseException as exc:
            return ApplyResult(
                ok=False,
                traceback="The Step passed the kernel but failed against the real Document, "
                          "so it was rolled back:\n\n" + trimmed_traceback(exc),
                gate="rollback",
            )

        took = self._clock() - started
        if took > STEP_LIMIT:
            self._narrate(WARNING, message="step took %d s on the live Document" % took)

        self._names = dict(rehearsal.names)
        self._dispatch(lambda: _show_tip(doc))
        self._dispatch(lambda: fit_view(doc))
        described = self._dispatch(lambda: description(doc))
        created = [o["name"] for o in described["features"] if o["name"] not in before]
        self.owned.update(created)
        return ApplyResult(ok=True, description=described, created=created,
                           modified=list(rehearsal.modified))

    def _narrate(self, kind, **data):
        """Say it on the stream, if a run is listening. Headless, nobody is."""
        if self.events is not None:
            self.events.emit(kind, **data)

    def _refused(self, deleted, doc):
        """
        The reason this Step may not be applied, or None. Deleting something
        theirs, and deleting the model, are separate: ownership does not
        cover the second one. `deleted` is the kernel's own run's answer
        (DAE-A-53), read against the Document before its transaction runs.
        """
        return self._not_ours(deleted) or self._a_rebuild(deleted, doc)

    def _a_rebuild(self, deleted, doc):
        """
        Ask before deleting several features of the model at once.

        One feature is a correction, several are a rebuild. A run with nobody
        to ask is held only on what the Document held before it.
        """
        modelling = self._modelling(doc, deleted)
        if self._unattended:
            modelling = [name for name in modelling if name not in self.owned]
        if len(modelling) <= _A_CORRECTION:
            return None

        wanted = "delete %d features of the existing model: %s" % (
            len(modelling), ", ".join(modelling))
        if self._allowed(wanted, {"rebuild": modelling}):
            return None

        return (
            "This Step would %s, which is a rebuild of a model that is already there rather "
            "than a change to it, and that was not approved. Edit the features that exist — "
            "a sketch's geometry and constraints, a feature's parameters, its Length, its "
            "Type, its profile — instead of deleting them and building the part again. Where "
            "a Step genuinely needs something the existing tree cannot express, add a feature "
            "to the end of it and leave the rest standing." % wanted
        )

    def _modelling(self, doc, names):
        """
        Which of *names* are the model, in the order they are given.

        A PartDesign feature is the part; a sketch, Measurement, datum,
        spreadsheet or scratch object is not, and deleting those is not on
        its own a rebuild. Read through the dispatcher: it touches the Document.
        """
        if not names:
            return []

        def read():
            found = []
            for name in sorted(names):
                obj = doc.getObject(name) if hasattr(doc, "getObject") else None
                if obj is not None and _is_modelling(obj):
                    found.append(name)
            return found

        return self._dispatch(read)

    def _allowed(self, wanted, kinds):
        """Put one deletion question to the user, and record both halves of it; a run with nobody to ask refuses without asking."""
        if self._unattended:
            return self._confirm(kinds) is True
        self._narrate(QUESTION, text="The assistant wants to " + wanted + ".")
        allowed = self._confirm(kinds) is True
        self._narrate(ANSWER, by="user",
                      text=("You allowed it to " if allowed else "You refused to let it ")
                           + wanted + ".")
        return allowed

    def _not_ours(self, deleted):
        """
        Ask before deleting anything the assistant did not create this Session.

        Asked on the stream, since it does not come through the loop and
        would vanish from a replayed Session otherwise. Only an explicit yes
        is consent — the truthy release object Stop uses is not one.
        """
        deletes = sorted(set(deleted) - self.owned)
        if not deletes:
            return None

        if self._allowed("delete " + ", ".join(deletes), {"delete": deletes}):
            return None

        return (
            "This Step would delete %s, which you created rather than I did, and that was "
            "not approved. Achieve the Intent without deleting the user's own objects, or "
            "say why it cannot be done and stop." % ", ".join(deletes)
        )


def discard(path):
    try:
        os.remove(path)
    except OSError:
        pass  # except-pass-allowed: best-effort delete, already-gone is fine


_A_3D_VIEW = "Gui::View3DInventor"


def _active_view(document=None):
    """
    The Document's 3D view — the one with a camera, not the tab in front.

    A Spreadsheet or TechDraw page of the same Document has no camera, so
    falls back to `mdiViewsOfType`.
    """
    try:
        import FreeCADGui
        gui_document = (FreeCADGui.getDocument(document.Name) if document is not None
                        else FreeCADGui.ActiveDocument)
        view = gui_document.ActiveView
        if hasattr(view, "getCamera"):
            return view
        views = gui_document.mdiViewsOfType(_A_3D_VIEW)
        return views[0] if views else None
    except (ImportError, AttributeError):
        return None


def _scaffolding(obj):
    """
    Whether *obj* is a Shape past `_SCAFFOLDING` — an Origin plane or axis,
    almost always. Kept separate from `_measurable`'s same test because
    `fit_view` only cares about the oversized case: no `Shape` at all, or no
    `Faces`, is not scaffolding, just not a solid, and hiding it would change
    what the camera fits to for no reason.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return False
    bound = shape.BoundBox
    return bound.isValid() and bound.DiagonalLength > _SCAFFOLDING


def _hide_scaffolding(document):
    hidden = []
    for obj in document.Objects:
        if not _scaffolding(obj):
            continue
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None or not view_object.Visibility:
            continue
        view_object.Visibility = False
        hidden.append(view_object)
    return hidden


def _show_tip(document):
    """
    Sets Visibility=True on each Body's current Tip and False on every other
    measurable feature it owns, so a run cut off mid-Step never leaves the
    modelled geometry hidden with nothing to correct it. Origin planes and
    other scaffolding fail `_measurable` already and are left untouched.
    """
    for obj in getattr(document, "Objects", None) or ():
        if getattr(obj, "TypeId", "") != _A_BODY:
            continue
        tip = getattr(obj, "Tip", None)
        for feature in getattr(obj, "Group", None) or ():
            if _measurable(feature) is None:
                continue
            view_object = getattr(feature, "ViewObject", None)
            if view_object is None:
                continue
            view_object.Visibility = feature is tip


def fit_view(document=None):
    """
    Put everything the Document shows back inside the viewport, since a 3 mm
    bracket and a 900 mm frame both arrive off to one side, too small to
    judge unscreened. A Body's Origin carries three planes and three axes,
    each 1e100 mm across, and one visible Origin turns "fit the part" into
    "fit a plane 1e100 mm across", so anything past `_SCAFFOLDING` is hidden
    before `fitAll()` runs.
    """
    view = _active_view(document)
    if view is None:
        return
    hidden = _hide_scaffolding(document) if document is not None and hasattr(document, "Objects") else []
    try:
        view.fitAll()
    finally:
        for view_object in hidden:
            view_object.Visibility = True
