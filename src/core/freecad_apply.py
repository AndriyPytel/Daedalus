"""
The path a Step takes from generated code to the Document: lint, Preflight,
the Qt main thread, a transaction, with Preflight behind this seam rather
than beside it, so callers see only "applied" or "failed, here is the
traceback". Document operations are marshalled to the Qt main thread and
block the caller until they return, so there are never concurrent mutations
of the Document. Also the FreeCAD-backed Tools, the only code in the addon
that touches the Document: API knowledge comes from introspecting the
running FreeCAD -- property lists, supported types, real dependencies --
never a documentation corpus that mixes versions, and `SEED_TRAPS` names the
cases where introspection reports something that exists but is not correct,
each carrying the script that reproduces it; `RETIRED_TRAPS` keeps the ones a
script disproved, with why.
"""
import math
import os
import threading
from dataclasses import dataclass, field

from core.events import ANSWER, QUESTION
from core.freecad_traps import Trap
from core.kernel import Kernel, KernelResult, copy_of
from core.kernel.kernel_worker import (_NamedDoc, register_step_source, signature_of,
                                signatures_match, step_namespace, trimmed_traceback)
from core.lint import OP_TYPEIDS, _TYPEID_REASON, lint, op_typeid_mismatch
from core.prompts import read_prompt
from core.tools import NO_PARAMETERS, ToolError, ToolRegistry


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
    All the loop ever learns about a Step: it landed, or here is why not.
    `modified` is Preflight's own measured list, carried past the
    `_CHANGED_NOTHING` check rather than dropped there; `drift` is set only
    by a `SignatureMismatch`, the GUI Document disagreeing with what the
    kernel verified. `gate` names which refusal branch built this result --
    "lint", "op_mismatch", "changed_nothing", "no_document", "ownership",
    "rollback" or "drift".
    """

    ok: bool
    traceback: str = ""
    description: dict = field(default_factory=dict)
    created: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    drift: bool = False
    gate: str = ""

    def as_tool_result(self) -> dict:
        if self.ok:
            return {"status": "applied", "created": self.created,
                    "modified": self.modified, "description": self.description}
        return {"status": "failed", "traceback": self.traceback, "drift": self.drift,
                "gate": self.gate}


_CHANGED_NOTHING = read_prompt("changed_nothing.md")


def _direct(func, timeout=None):
    """The identity dispatch: run *func* right here, no Qt, no marshalling."""
    return func()


def run_in_transaction(doc, code, label="Daedalus Step", names=None, expected_signature=None,
                       design=None, plan_values=None):
    """
    Execute *code* against *doc* as one undoable unit, main thread only; the
    recompute runs inside the transaction, so one undo takes the whole Step
    back. *names* and *design* are the allocated-name map and the Design
    model Preflight already ran this same Step against, both carried in the
    same way so Apply resolves them identically, and *plan_values* the
    Plan-frozen numbers a Step reads by name the same way. *expected_signature*
    is what Preflight verified; `None` skips the check, a mismatch aborts the
    transaction.
    """
    names = {} if names is None else names
    doc.openTransaction(label)
    try:
        register_step_source(code)
        namespace = step_namespace(_NamedDoc(doc, names), design=design,
                                   plan_values=plan_values)
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
                 transaction=None, render_dir=None, confirm=None, owned=None,
                 events=None, main_window=None):
        self._document = document
        self._worker = worker
        self._dispatch = dispatch or _direct
        self._transaction = transaction or run_in_transaction
        self._render_dir = render_dir
        self._renders = 0
        self._confirm = confirm or refuse_without_asking
        self.owned = set(owned or ())
        self.events = events
        self._main_window = main_window
        self.locked = False
        self._names = {}

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

    def start_run(self) -> KernelResult:
        """
        Snapshot the GUI's current Document into the kernel and lock the GUI.

        Locking the main window keeps the GUI's state comparable to the
        kernel's for the rest of the run.
        """
        doc = self._dispatch(self._active)
        if doc is None:
            seeded = self.worker.check("pass", document=None, ephemeral=True)
        else:
            copy = self._dispatch(lambda: copy_of(doc))
            try:
                seeded = self.worker.check("pass", document=copy, ephemeral=True)
            finally:
                discard(copy)
        self._dispatch(lambda: lock_gui(self.main_window, True))
        self.locked = True
        self._names = {}
        return seeded

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

    def _render_path(self):
        """
        The next render's path, in a directory of this pipeline's own, so two
        runs at once never overwrite or review each other's pictures.
        """
        if self._render_dir is None:
            import tempfile

            self._render_dir = tempfile.mkdtemp(prefix="fcc-renders-")
        self._renders += 1
        return os.path.join(self._render_dir, "daedalus_step_%d.png" % self._renders)

    def apply(self, code, selection=None, label="Daedalus Step", op=None,
              kernel_result=None, design=None, plan_values=None,
              design_model=True) -> ApplyResult:
        """
        Lint *code* and land it on the live Document as one transaction.
        `kernel_result` is what the kernel already found running this Step
        for real -- names created/deleted/modified, its name map, its
        Document's signature -- so the kernel's run is the rehearsal, rolled
        back on a signature disagreement, with no second rehearsal here.
        `op` is checked against what the kernel built, not this code's text,
        and `design` and `plan_values` are the Design model and the Plan's
        frozen numbers Preflight ran this Step against, carried into the same
        transaction so Apply resolves them identically; *design_model* false is a
        caller with no `design` in scope, to whom the Design-model lint rules do not apply.
        """
        kernel_result = kernel_result or {}
        checked = lint(code, selection, design_model)
        if not checked.ok:
            return ApplyResult(ok=False, traceback=checked.report(), gate="lint")

        mismatch = op_typeid_mismatch(op, kernel_result.get("created"),
                                      kernel_result.get("objects"))
        if mismatch:
            return ApplyResult(ok=False, traceback=_TYPEID_REASON % (
                mismatch, mismatch, op, " or ".join(OP_TYPEIDS[op])), gate="op_mismatch")

        if not (kernel_result.get("created") or kernel_result.get("deleted")
                or kernel_result.get("modified")):
            return ApplyResult(ok=False, traceback=_CHANGED_NOTHING, gate="changed_nothing")

        doc = self._dispatch(self._active)
        if doc is None:
            return ApplyResult(ok=False, traceback="There is no open Document to apply this Step to.",
                               gate="no_document")

        before = set(self._dispatch(lambda: [o.Name for o in doc.Objects]))

        declined = self._refused(kernel_result.get("deleted") or (), doc)
        if declined:
            return ApplyResult(ok=False, traceback=declined, gate="ownership")

        try:
            self._dispatch(lambda: self._transaction(
                doc, code, label, names=self._names,
                expected_signature=kernel_result.get("signature"), design=design,
                plan_values=plan_values))
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

        self._names = dict(kernel_result.get("names") or {})
        self._dispatch(lambda: _show_tip(doc))
        self._dispatch(lambda: fit_view(doc))
        described = self._dispatch(lambda: description(doc))
        created = [o["name"] for o in described["features"] if o["name"] not in before]
        self.owned.update(created)
        return ApplyResult(ok=True, description=described, created=created,
                           modified=list(kernel_result.get("modified") or ()))

    def undo_step(self):
        """
        Undo the last Step's transaction on the live Document -- the same
        undo entry `apply` committed. A Plan revision that drops the Step
        it belongs to calls this once per Step dropped; nothing else does.
        """
        doc = self._dispatch(self._active)
        if doc is not None:
            self._dispatch(doc.undo)
        return {"object_count": len(doc.Objects) if doc is not None else 0}

    def undo_depth(self):
        """
        How many Steps the live Document could still undo -- `UndoMode`'s
        stack drops its oldest entries once `MaxUndoSize` is spent, so this
        can be smaller than every Step ever applied this run.
        """
        doc = self._dispatch(self._active)
        return len(doc.UndoNames) if doc is not None else 0

    def check_assertions(self, code):
        """
        Run read-only checks against a copy of the finished Document.

        The copy is what makes it read-only — this process never touches the
        live Document, so even a check that tried to write could not.
        """
        doc = self._dispatch(self._active)
        if doc is None:
            return {"ok": False, "traceback": "There is no open Document to check."}

        copy = self._dispatch(lambda: copy_of(doc))
        try:
            checked = self.worker.check(code, document=copy, ephemeral=True, geometry=True)
        finally:
            discard(copy)
        return {"ok": bool(checked.ok), "traceback": checked.traceback or "",
                "held": checked.held, "failed": checked.failed,
                "defects": checked.defects,
                "geometry": checked.geometry}

    def review_views(self):
        """
        The views Review judges, captured once at the end of a run.
        """
        doc = self._dispatch(self._active)
        paths = [self._render_path() for _ in REVIEW_VIEWS]
        views = capture_review_views(paths, doc, dispatch=self._dispatch)
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
                                      render_path=self._render_path(),
                                      ephemeral=True)
        finally:
            discard(copy)
        return [drawn.render] if drawn.ok and drawn.render else []

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
        Ask before deleting the feature tree, whoever built it.

        Ownership is not the question; how much of the model goes is.
        Deleting one feature is a correction; deleting several is a rebuild.
        """
        modelling = self._modelling(doc, deleted)
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
        """Put one deletion question to the user, and record both halves of it."""
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


SEED_TRAPS = [
    Trap(
        claim='Sketch attachment goes through AttachmentSupport + MapMode, not Placement. Setting Placement on an attached sketch is silently overridden on recompute.',
        script="""
plane = doc.addObject("App::Plane", "XY_Plane")
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(plane, "")]
sk.MapMode = "FlatFace"
doc.recompute()
attached_placement = App.Placement(sk.Placement)
sk.Placement = App.Placement(App.Vector(50, 50, 50), App.Rotation())
doc.recompute()
assert sk.Placement.Base == attached_placement.Base, "Placement stuck where it was set, expected the attachment to override it"
""",
    ),
    Trap(
        claim='sketch.Support and sketch.Plane were removed in FreeCAD 1.0. AttachmentSupport replaces Support.',
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
try:
    sk.Support
    assert False, "Support still exists on Sketcher::SketchObject"
except AttributeError:
    pass
try:
    sk.Plane
    assert False, "Plane still exists on Sketcher::SketchObject"
except AttributeError:
    pass
sk.AttachmentSupport
""",
    ),
    Trap(
        claim="A PartDesign::Pad or Pocket left off the Body via Body.addObject computes its own Shape from Profile regardless, and looks completely correct -- Shape.isNull() False, a sane Volume -- which is exactly what a check against the feature's own Shape misses. Body.Shape stays null until the feature is Body.addObject'd, and reading Body.Shape.Volume off it raises 'shape is invalid', not a wrong number. Check the Body's Shape, never the feature's own.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
sk = doc.addObject("Sketcher::SketchObject", "S")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
doc.recompute()
assert not pad.Shape.isNull()
assert pad.Shape.Volume > 0
try:
    body.Shape.Volume
    assert False, "Body.Shape.Volume did not raise for a feature never added to the Body"
except Exception as e:
    assert "invalid" in str(e).lower()
doc.removeObject(pad.Name)
""",
    ),
    Trap(
        claim="A sketch is a 'Sketcher::SketchObject'; there is no 'PartDesign::Sketch'. Its geometry comes from the Part module — Part.Circle, Part.LineSegment, Part.ArcOfCircle — which is why sketch.addGeometry takes a Part object. The Sketcher module holds constraints, not geometry.",
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
assert sk.TypeId == "Sketcher::SketchObject"
try:
    doc.addObject("PartDesign::Sketch", "Bad")
    assert False, "PartDesign::Sketch exists as a type"
except Exception:
    pass
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5))
sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
sk.addGeometry(Part.ArcOfCircle(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), 0, 1))
doc.recompute()
assert len(sk.Geometry) == 3
try:
    Sketcher.Circle
    assert False, "Sketcher module carries geometry constructors"
except AttributeError:
    pass
""",
    ),
    Trap(
        claim='Body.addObject moves Body.Tip to the feature you just added. Capture the base feature before adding, or a dressup such as PartDesign::Fillet will end up referencing itself and fail to recompute.',
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()
assert body.Tip.Name == pad.Name, "Body.Tip did not move to the feature just added"
""",
    ),
    Trap(
        claim="The constraint types this build has are Angle, Block, Coincident, Diameter, Distance, DistanceX, DistanceY, Equal, Horizontal, InternalAlignment, Parallel, Perpendicular, PointOnObject, Radius, SnellsLaw, Symmetric, Tangent, TangentViaPoint, Vertical and Weight. There is no 'Concentric': concentricity is Coincident between the two centres — Sketcher.Constraint('Coincident', a, 3, b, 3), where the position id is 0 for the edge itself, 1 start, 2 end, 3 centre. A type name this build does not have, or one of these at the wrong number of arguments, takes the FreeCAD core down inside the C++ constructor rather than raising, so there is no traceback to correct against.",
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5))
sk.addGeometry(Part.Circle(App.Vector(20, 0, 0), App.Vector(0, 0, 1), 5))
Sketcher.Constraint("Concentric", 0, 3, 1, 3)
""",
    ),
    Trap(
        claim='A material is assigned as obj.ShapeMaterial = Materials.MaterialManager().getMaterial(uuid). The property is a Materials::PropertyMaterial and takes a Materials.Material and nothing else. App.Material is the old appearance-only material and has no Name or Density. A Materials.Material cannot be built, subscripted or written to from here, so a density is chosen by choosing the card that already carries it — call the materials Tool for the uuid rather than guessing one.',
        script="""
box = doc.addObject("Part::Box", "Box")
mgr = Materials.MaterialManager()
uuid = next(iter(mgr.Materials.keys()))
mat = mgr.getMaterial(uuid)
box.ShapeMaterial = mat
assert box.ShapeMaterial.Name
old = App.Material()
try:
    old.Name
    assert False, "the old App.Material carries a Name"
except AttributeError:
    pass
try:
    old.Density
    assert False, "the old App.Material carries a Density"
except AttributeError:
    pass
""",
    ),
    Trap(
        claim='A thread is PartDesign::AdditiveHelix sweeping a profile along a helical path, or PartDesign::Hole with Threaded set; this build has both, and PartDesign::Helix and PartDesign::AdditivePipe besides. Lofting one V-section per turn builds a stack of flats that is not a helical surface, and nothing downstream reports it.',
        script="""
types = doc.supportedTypes()
for t in ("PartDesign::AdditiveHelix", "PartDesign::Hole", "PartDesign::Helix", "PartDesign::AdditivePipe"):
    assert t in types, t
hole = doc.addObject("PartDesign::Hole", "Hole")
assert hasattr(hole, "Threaded")
doc.removeObject(hole.Name)
""",
    ),
    Trap(
        claim="A Pocket travels along the *negative* of its sketch normal, and a Pad along the positive, unless Reversed is True. A Pocket whose sketch normal points into the material therefore cuts away from the solid, meets nothing, and hands back the shape it was given — and FreeCAD reports it Up-to-date and Valid, because a Cut with nothing to cut is not an error. Check the sign against the Body's BoundBox: the sketch sits on the face the cut starts from with its normal pointing out of the material, or it carries Reversed=True. For a hole that goes through, Type='ThroughAll' says so and cannot run short or run the wrong way, where Length=bb.YLength is a distance in a direction you have to have got right.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
skpad = doc.addObject("Sketcher::SketchObject", "SPad")
skpad.AttachmentSupport = [(xyplane, "")]
skpad.MapMode = "FlatFace"
skpad.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(skpad)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = skpad
pad.Length = 10
body.addObject(pad)
doc.recompute()
before = pad.Shape.Volume

top_index = None
for i, f in enumerate(pad.Shape.Faces):
    if abs(f.CenterOfMass.z - 10) < 1e-6:
        top_index = i + 1
        break

sk2 = doc.addObject("Sketcher::SketchObject", "SPocket")
sk2.AttachmentSupport = [(pad, "Face%d" % top_index)]
sk2.MapMode = "FlatFace"
sk2.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
body.addObject(sk2)
sk2.AttachmentOffset = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(1, 0, 0), 180))
doc.recompute()

pocket = doc.addObject("PartDesign::Pocket", "Pocket")
pocket.Profile = sk2
pocket.Length = 5
pocket.Reversed = False
body.addObject(pocket)
doc.recompute()
assert pocket.Shape.Volume == before, "flipped normal, Reversed=False cut material after all"
pocket.Reversed = True
doc.recompute()
assert pocket.Shape.Volume < before, "Reversed=True did not fix a Pocket cutting the wrong way"
""",
    ),
    Trap(
        claim="PartDesign::Fillet with UseAllEdges set rounds every edge of the base feature, which is a different part from one with the edges the drawing marks rounded. Where only some edges are meant, select them by computing which — off face normals, edge length, the plane an edge lies in — and pass that list in Base. None of those three tells a rim's inner edges from its outer ones — a slot's hole rim and the plate's own edge share length, plane and z. Only the two faces either side of an edge say which way a fillet on it pulls, and Preflight refuses a Base that mixes them, naming the edges it will not carry together.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(40, 0, 0)))
sk.addGeometry(Part.LineSegment(App.Vector(40, 0, 0), App.Vector(40, 40, 0)))
sk.addGeometry(Part.LineSegment(App.Vector(40, 40, 0), App.Vector(0, 40, 0)))
sk.addGeometry(Part.LineSegment(App.Vector(0, 40, 0), App.Vector(0, 0, 0)))
sk.addGeometry(Part.Circle(App.Vector(20, 20, 0), App.Vector(0, 0, 1), 6.366197723675814))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()

def top_edge(length, is_outer):
    for i, e in enumerate(pad.Shape.Edges):
        if abs(e.CenterOfMass.z - 10) < 1e-6 and abs(e.Length - length) < 1e-3:
            outer = e.CenterOfMass.distanceToPoint(App.Vector(20, 20, 10)) > 15
            if outer == is_outer:
                return "Edge%d" % (i + 1)
    return None

outer_edge = top_edge(40, True)
hole_edge = top_edge(40, False)
assert outer_edge and hole_edge, "the plate's outer rim and the hole's rim do not share a same-length, same-plane edge to test with"

fillet = doc.addObject("PartDesign::Fillet", "Fillet")
fillet.Base = (pad, [outer_edge, hole_edge])
body.addObject(fillet)
doc.recompute()
""",
    ),
    Trap(
        claim="Shape.BoundBox is taken off the shape's triangulation, so on a curved face it reports the tessellation and not the surface — and lands inside the true extreme, by the sagitta of whatever chord the mesh happened to use. The BoundBox of a pad with R25 rounded ends read XMin=0.182278 where the face is tangent at 0, and the five sketches placed at that XMin each built their feature 0.18 mm off, in a Document that reopens with XMin=0.0 and cannot be made to show it again. Read it for the sign of a direction and for which side of the solid you are on; never for a coordinate a feature is placed at. Shape.optimalBoundingBox(False) is exact where a box is genuinely what is wanted — its default triangulates and inherits the same tessellation padding as BoundBox once the shape carries a mesh — and the face that carries the number — Surface.Center, Surface.Radius, a Vertex Point — is better still.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 25))
body.addObject(sk)
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()

shape = pad.Shape.copy()
shape.tessellate(1.0)
assert shape.BoundBox.YMax < 25.0 - 1e-3, "BoundBox no longer reads off the triangulation"
assert abs(shape.optimalBoundingBox(False).YMax - 25.0) < 1e-6
faces = [f for f in pad.Shape.Faces if type(f.Surface).__name__ == "Cylinder"]
assert abs(faces[0].Surface.Radius - 25.0) < 1e-9
""",
    ),
    Trap(
        claim="App.Vector's methods are not all alike about the vector they are called on: multiply, normalize, scale and projectToPlane change it in place and hand it back, while add, sub and cross return a new one and leave it alone. So v.multiply(k) is not v scaled into a result, it is v scaled, and every later reader of v gets the scaled one. A recorded run's acceptance checks wrote unit(v) as v.multiply(1.0/n) and projected with zdir.multiply(dot(raw, zdir)): the projection zeroed zdir where the dot was 0 and left xdir 51.5 times too long, and 28 of 34 entries came back unmet on a part that met them. Where a vector is needed again, do the arithmetic with the operators — v * k, a - b, a + b — or build a new App.Vector; never call one of the in-place methods on a vector something else still reads.",
        script="""
v = App.Vector(3, 0, 0)
w = v.normalize()
assert v.Length == 1, "normalize did not change v in place"
assert w.Length == 1

u = App.Vector(2, 0, 0)
r = u.multiply(3)
assert u == App.Vector(6, 0, 0), "multiply did not change u in place"
assert r == u

a = App.Vector(1, 0, 0)
b = App.Vector(0, 1, 0)
c = a.add(b)
assert a == App.Vector(1, 0, 0), "add changed a in place, expected it to leave a alone"
assert c == App.Vector(1, 1, 0)

x = App.Vector(1, 0, 0)
y = App.Vector(0, 1, 0)
s = x - y
assert x == App.Vector(1, 0, 0), "subtraction operator changed its left operand"
assert s == App.Vector(1, -1, 0)
""",
    ),
    Trap(
        claim="Body.addObject hands back a *list* of what it added, not the object. `sk = body.addObject(doc.addObject('Sketcher::SketchObject','S'))` therefore binds a list, and the next line — sk.AttachmentSupport, sk.addGeometry — dies on 'list' object has no attribute. Add in one statement and keep the object in another: `sk = doc.addObject(...)` then `body.addObject(sk)`.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
added = body.addObject(doc.addObject("Sketcher::SketchObject", "S"))
assert isinstance(added, list), "Body.addObject stopped returning a list"
try:
    added.AttachmentSupport
    assert False, "a list unexpectedly exposes AttachmentSupport"
except AttributeError:
    pass
""",
    ),
    Trap(
        claim='A sketched curve that is not a line, an arc or a circle is a Part.BSplineCurve through points computed from its own equation — an involute flank, a cam profile, a cycloid. A chain of Part.LineSegment approximates it, and the approximation is what gets built and measured.',
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
curve = Part.BSplineCurve()
curve.interpolate([App.Vector(0, 0, 0), App.Vector(10, 5, 0), App.Vector(20, 0, 0)])
n = sk.addGeometry(curve, False)
doc.recompute()
assert isinstance(sk.Geometry[n], Part.BSplineCurve)
""",
    ),
    Trap(
        claim="A PropertyEnumeration setter — Pocket.Type, Pad.Type, and others — raises ValueError on any string outside its own accepted list, and that list is per property, not a spelling shared across the module. Read the real set first with obj.getEnumerationsOfProperty('Type') and set one of those; a spelling that works on one enum property is not evidence it works on another.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()

sk2 = doc.addObject("Sketcher::SketchObject", "S2")
sk2.AttachmentSupport = [(pad, "Face3")]
sk2.MapMode = "FlatFace"
sk2.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
body.addObject(sk2)
doc.recompute()
pocket = doc.addObject("PartDesign::Pocket", "Pocket")
pocket.Profile = sk2
body.addObject(pocket)
doc.recompute()

valid_types = pocket.getEnumerationsOfProperty("Type")
assert "ThroughAll" in valid_types
try:
    pocket.Type = "NotARealType"
    assert False, "an invalid Type string was accepted"
except ValueError:
    pass
pocket.Type = "ThroughAll"
doc.recompute()
""",
    ),
    Trap(
        claim="PartDesign::Fillet fails on a radius the material cannot carry with 'BRep_API: command not done', a message that names the failed OCC call and nothing about the radius that caused it. A full round needs less than half the thickness of what it rounds; check the radius against the Body's wall thickness before setting it, not after the recompute fails with no way back to why.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 4
body.addObject(pad)
doc.recompute()

top_edge = None
for i, e in enumerate(pad.Shape.Edges):
    if abs(e.CenterOfMass.z - 4) < 1e-6:
        top_edge = "Edge%d" % (i + 1)
        break

fillet = doc.addObject("PartDesign::Fillet", "Fillet")
fillet.Base = (pad, [top_edge])
fillet.Radius = 4.5
body.addObject(fillet)
doc.recompute()
""",
    ),
    Trap(
        claim='A Pad or Pocket sketch with more than one closed wire builds every wire — if the wires do not touch, the feature leaves the Body in more than one solid, and FreeCAD only reports it as a plain RuntimeError once the recompute runs, with no earlier signal. A second piece of geometry that does not touch the first belongs in its own Body, not a second wire in the same sketch.',
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5))
sk.addGeometry(Part.Circle(App.Vector(50, 0, 0), App.Vector(0, 0, 1), 5))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()
""",
    ),
]

RETIRED_TRAPS = (
    (
        'There is no active Body outside the GUI. ActiveObject, setActiveObject and activeView do not exist on App.Document; nothing needs activating, Body.addObject is what puts a feature in a Body.',
        'doc.ActiveObject exists on App.Document and returns None headless; only setActiveObject and activeView are actually absent. core.lint already rejects all four ActiveObject/setActiveObject/activeView/getActiveObject spellings outright, so nothing the trap defended is left unguarded.',
    ),
)

TRAPS = tuple(trap.claim for trap in SEED_TRAPS)


_SNAPSHOT_LIMIT = 200
_MATERIALS_LIMIT = 40

_GEOMETRY_MODULES = ("Part", "PartDesign", "Sketcher")


def _load_geometry_modules():
    """
    Import the geometry modules, so the Document will admit their types.

    `supportedTypes()` only reports types whose module has been imported —
    unimported, a session that never opened PartDesign is told PartDesign::Pad
    does not exist, a confident, wrong absence.
    """
    for module in _GEOMETRY_MODULES:
        try:
            __import__(module)
        except ImportError:
            pass  # except-pass-allowed: an unavailable workbench is not installed, not an error


def _selection(dispatch=_direct):
    """
    What the user has selected right now, sub-element names included -- the
    one sanctioned source of an `Edge12`; headless has no GUI and therefore
    no selection. Read through *dispatch*, since the worker thread that
    calls this cannot touch Qt directly.
    """
    try:
        import FreeCADGui

        selection = dispatch(FreeCADGui.Selection.getSelectionEx)
    except (ImportError, AttributeError):
        return []

    return [
        {"object": sel.ObjectName, "elements": list(sel.SubElementNames)}
        for sel in selection
    ]


_OPPOSITE_ISOMETRIC = "viewIsometricOpposite"
"""
Not a `FreeCADGui` button — `libFreeCADGui` names `viewFront`, `viewTop`,
`viewRight`, `viewRear`, `viewBottom`, `viewLeft`, `viewIsometric`,
`viewDimetric` and `viewTrimetric`, and none of them is the corner
diagonally across from isometric's. `capture_review_views` builds it instead,
from whatever orientation `viewIsometric` is already holding.
"""

_SECTION_VIEW = "section"
"""
Not a `FreeCADGui` orientation either — `capture_review_views` builds the
cutaway itself, clipping through whatever `_section_plane` finds to be the
part's own interior, the way FreeCAD's own View > Cross-sections does
(`View3DInventorPy.toggleClippingPlane`).
"""

REVIEW_VIEWS = ("viewIsometric", _OPPOSITE_ISOMETRIC, "viewTop", "viewFront", _SECTION_VIEW)


_A_3D_VIEW = "Gui::View3DInventor"


def active_document():
    """Whichever Document is in focus right now, or None with nothing open."""
    import FreeCAD
    return FreeCAD.ActiveDocument


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


def _cavity_center(box, material):
    """
    The centre of mass of the largest void inside `box`, or None.

    Measured: ~0.11s for a 200x200x30 plate cut by forty 8mm bores.
    """
    import FreeCAD
    import Part

    solid = Part.makeBox(box.XLength, box.YLength, box.ZLength,
                          FreeCAD.Vector(box.XMin, box.YMin, box.ZMin))
    try:
        voids = [void for void in solid.cut(material).Solids if void.Volume > 1e-6]
    except Part.OCCError:
        return None
    if not voids:
        return None
    return max(voids, key=lambda void: void.Volume).CenterOfMass


def _section_plane(document):
    """
    A placement that cuts through the part's real interior, not one of its
    faces — what a shell, bore or blind pocket needs to become visible.

    The plane sits at the centre of the largest actual void the modelled
    geometry has, not a guess at where a cavity usually sits.
    """
    import FreeCAD

    doc = document if document is not None else FreeCAD.ActiveDocument
    if doc is None:
        return None
    box = None
    material = None
    for obj in doc.Objects:
        bound = _measurable(obj)
        if bound is None:
            continue
        box = bound if box is None else box.united(bound)
        if not obj.InList:
            material = obj.Shape if material is None else material.fuse(obj.Shape)
    if box is None:
        return None
    center = _cavity_center(box, material) or box.Center
    return FreeCAD.Placement(center, FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90))


def capture_review_views(paths, document=None, size=(800, 600), dispatch=_direct):
    """
    The five views Review judges, one dispatched call each: two isometrics
    from opposite corners, plus top, front and a section, which makes a
    shell, bore or blind pocket visible that the others miss. A dispatched
    call holds the Qt main thread until it returns, and five renders in one
    is the longest block a run has, so the event loop runs between captures
    and each stands alone.
    """
    view = dispatch(lambda: _active_view(document))
    if view is None:
        return []

    return [path for name, path in zip(REVIEW_VIEWS, paths)
            if dispatch(lambda name=name, path=path: _capture_view(view, name, path,
                                                                  document, size))]


def _capture_view(view, name, path, document, size):
    """
    Write one of Review's views to *path*, or say there was none to write.

    Scaffolding past `_SCAFFOLDING` is hidden for the `fitAll()` this makes,
    the same as `fit_view`, so an Origin plane never frames the shot in the
    part's place. The camera, the clip plane and that visibility are all put
    back before this returns, whichever way it goes.
    """
    import FreeCAD

    camera = view.getCamera()
    sectioned = False
    hidden = _hide_scaffolding(document) if document is not None and hasattr(document, "Objects") else []
    try:
        if name == _OPPOSITE_ISOMETRIC:
            view.viewIsometric()
            view.setCameraOrientation(view.getCameraOrientation().multiply(
                FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 180)))
        elif name == _SECTION_VIEW:
            plane = _section_plane(document)
            if plane is None:
                return False
            view.toggleClippingPlane(1, False, True, plane)
            sectioned = True
        else:
            getattr(view, name)()
        view.fitAll()
        view.saveImage(path, size[0], size[1], "Current")
    finally:
        if sectioned:
            view.toggleClippingPlane(0)
        view.setCamera(camera)
        for view_object in hidden:
            view_object.Visibility = True
    return True


def _active_body(doc, dispatch=_direct):
    """
    The Body the user is working in, or the only one there is.

    `ActiveDocument.ActiveView` is the Gui's state and is read through
    *dispatch* for the reason `_selection` is.
    """
    try:
        import FreeCADGui

        body = dispatch(
            lambda: FreeCADGui.ActiveDocument.ActiveView.getActiveObject("pdbody"))
    except (ImportError, AttributeError):
        body = None
    if body is not None:
        return body.Name
    bodies = [o.Name for o in doc.Objects if o.isDerivedFrom("PartDesign::Body")]
    return bodies[0] if len(bodies) == 1 else None


def _errors(obj):
    return [s for s in (obj.State or []) if s in ("Invalid", "Error", "Touched")]


_SCAFFOLDING = 1e6


def _measurable(obj):
    """
    The object's bounding box where it is modelled geometry, else None.

    A datum plane is a real Shape with a real face and it is 1e100 across, so
    everything that measures the part has to refuse the same things. Anything
    bigger than a kilometre across is scaffolding rather than a part.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.Faces:
        return None
    bound = shape.BoundBox
    if not bound.isValid() or bound.DiagonalLength > _SCAFFOLDING:
        return None
    return bound


def export_step(document, path, only=None):
    """
    Write *document*'s modelled geometry to *path* as STEP, and hand the
    path back. Only the top-level objects `_measurable` calls real geometry go in --
    a Sketch or Pad an unhidden Body already fused into its own Shape, or
    a Body's own Origin scaffolding, would double the geometry or fail
    `Part.export` outright. *only* names the one object that goes out alone, as an edit's Body does,
    and a shape the mesh gate rejects is repaired first unless that moves the volume over 0.5%.
    """
    import warnings

    import Part

    from core.manifold import repair_fc, rewrite_step

    objects = [obj for obj in document.Objects
               if not obj.InList and _measurable(obj) is not None
               and (only is None or obj.Name == only)]
    shapes = []
    for obj in objects:
        fixed, note = repair_fc(obj.Shape)
        if note and fixed.isSame(obj.Shape):
            warnings.warn("%s: %s" % (obj.Name, note))
        shapes.append(fixed)
    if all(fixed is obj.Shape or fixed.isSame(obj.Shape) for fixed, obj in zip(shapes, objects)):
        Part.export(objects, path)
    else:
        Part.Compound(shapes).exportStep(path)
    note = rewrite_step(Part.Compound(shapes), path)
    if note:
        warnings.warn(note)
    return path


def has_solid(document):
    """Whether *document* holds modelled geometry with a solid, the only thing worth exporting."""
    return any(not obj.InList and _measurable(obj) is not None and obj.Shape.Solids
               for obj in document.Objects)


def _overall_size(doc):
    """
    How big the modelled part is, `[x, y, z]` in mm, or None if nothing is.

    A Body's Origin carries three planes and three axes, each 1e100 across,
    so a literal union of every bounding box makes any part 2e+100 mm wide;
    `_measurable` is what keeps this to the modelled geometry.
    """
    box = None
    for obj in doc.Objects:
        bound = _measurable(obj)
        if bound is None:
            continue
        box = bound if box is None else box.united(bound)
    if box is None:
        return None
    return [round(box.XLength, 6), round(box.YLength, 6), round(box.ZLength, 6)]


_ANGULAR = ("Angle", "AngleViaPoint")
_DIMENSIONAL = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter")


def description(doc):
    """
    What the Document now holds, in the two layers Review can judge by. The
    feature tree with its parameters is trustworthy because Lint forces it
    to be genuine PartDesign; the inventory read off `Shape` is what it came
    out as, the only one that reports a hole nobody thought to ask about,
    since the Acceptance list is written before the part exists.
    """
    return {"features": [_described(obj) for obj in doc.Objects],
            "geometry": _inventory(doc)}


def _described(obj):
    described = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    parameters = _parameters(obj)
    if parameters:
        described["parameters"] = parameters
    constraints = _constraints(obj)
    if constraints:
        described["constraints"] = constraints
    return described


def _parameters(obj):
    """
    The named values of one feature, as the property editor would show them.

    Hidden/ReadOnly is FreeCAD's own "is this editable here", not a
    relevance filter — an unthreaded Hole still reports its thread
    properties. A link, shape or placement is not a value to judge by.
    """
    parameters = {}
    for prop in getattr(obj, "PropertiesList", ()):
        if prop == "Label" or set(obj.getEditorMode(prop)) & {"Hidden", "ReadOnly"}:
            continue
        try:
            value = _named_value(getattr(obj, prop))
        except Exception:
            continue
        if value is not None:
            parameters[prop] = value
    return parameters


def _named_value(value):
    """
    The property's value where it is a number someone could name, else None.

    A Quantity is the pair of a Value and a Unit, and reads as "10.0 mm".
    """
    if isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "Value") and hasattr(value, "Unit"):
        return str(value)
    return None


def _constraints(obj):
    """
    A sketch's driving, named constraints — and deliberately not its geometry.

    Raw geometry is a defect formally present and practically invisible: a
    LineSegment's coordinates are the most tokens for the least judgement. A
    named constraint is the same dimension, said once, by someone who chose it.
    """
    return {constraint.Name: _dimension(constraint)
            for constraint in getattr(obj, "Constraints", ())
            if constraint.Name and constraint.Driving}


def _dimension(constraint):
    """
    One constraint as a person reads it.

    A constraint holds an angle in radians, and every place a person reads one
    it is degrees.
    """
    if constraint.Type in _ANGULAR:
        return "%.6g deg" % math.degrees(constraint.Value)
    if constraint.Type in _DIMENSIONAL:
        return "%.6g mm" % constraint.Value
    return constraint.Type


def _inventory(doc):
    """
    The geometry as it came out: material total, one entry per surface
    family. Grouping by kind/diameter/axis keeps identical holes one entry;
    only top-level objects count, so this is the part as it stands.

    A family counts distinct axis seats — a bore's two faces share one, flats share none — and volume tells apart a cut Pocket from one that missed.
    """
    solids, volume, seats = 0, 0.0, {}
    for obj in doc.Objects:
        if getattr(obj, "InList", None) or _measurable(obj) is None:
            continue
        shape = obj.Shape
        solids += len(shape.Solids)
        volume += shape.Volume
        for face in shape.Faces:
            family, seat = _family(face.Surface)
            seats.setdefault(family, []).append(seat)

    surfaces = []
    for family in sorted(seats, key=str):
        kind, diameter, axis = family
        seated = seats[family]
        entry = {"kind": kind}
        if diameter is not None:
            entry["diameter_mm"] = diameter
            entry["axis"] = list(axis)
        entry["count"] = len(set(seated)) if all(seated) else len(seated)
        surfaces.append(entry)
    return {"solids": solids, "volume_mm3": round(volume, 6), "surfaces": surfaces}


_INVENTORY_LINES = 40


def base_inventory(doc):
    """
    The part a Body starts from, in words: bounding box, then one line per face address, sections cut past `_INVENTORY_LINES`. None where no Body has a BaseFeature. Two ceilings: a B-spline address is approximate (family, bounding box and centroid rounded to 0.1 mm), and a plane address is the whole plane, so removing one of several faces on one plane leaves the address standing.
    """
    base = _base_feature(doc)
    if base is None:
        return None
    shape = base.Shape
    bound = shape.optimalBoundingBox(False)
    addresses = _addresses_of(shape)
    planes = {a: v for a, v in addresses.items() if a.startswith("plane ")}
    cylinders = {a: v for a, v in addresses.items() if a.startswith(("hole ", "boss "))}
    others = {a: v for a, v in addresses.items() if a not in planes and a not in cylinders}

    def line(address, area):
        return "%s (area %s mm2)" % (address, _g(area))

    lines = ["The part before the edit, in mm:",
             "bounding box: %s x %s x %s mm (x %s..%s, y %s..%s, z %s..%s)" % (
                 _g(bound.XLength), _g(bound.YLength), _g(bound.ZLength),
                 _g(bound.XMin), _g(bound.XMax), _g(bound.YMin), _g(bound.YMax),
                 _g(bound.ZMin), _g(bound.ZMax)),
             "volume: %s mm3, solids: %d" % (_g(shape.Volume), len(shape.Solids))]
    lines += _cut([line(a, v) for a, v in sorted(planes.items(), key=lambda item: -item[1])],
                  "planes")
    lines += _cut([line(a, v) for a, v in sorted(cylinders.items())], "cylinders")
    lines += _cut([line(a, v) for a, v in sorted(others.items())], "other surfaces")
    return "\n".join(lines)


def _base_feature(doc):
    base = next((obj.BaseFeature for obj in doc.Objects
                 if obj.TypeId == "PartDesign::Body" and obj.BaseFeature is not None), None)
    return base if base is not None and _measurable(base) is not None else None


def base_addresses(doc):
    """
    The face addresses of the base part with their areas, and of the Body as it stands, or None where no Body has a BaseFeature.

    A face is gone when its address is in `base` and not in `now`; an address that stays with another area is not gone.
    """
    base = _base_feature(doc)
    if base is None:
        return None
    body = next(obj for obj in doc.Objects
                if obj.TypeId == "PartDesign::Body" and obj.BaseFeature is not None)
    return {"base": {address: round(area, 3) for address, area in sorted(_addresses_of(base.Shape).items())},
            "now": sorted(_addresses_of(body.Shape))}


def _addresses_of(shape):
    """Every face address of *shape* with the area summed over the faces that share it."""
    found = {}
    for face in shape.Faces:
        surface = face.Surface
        kind = surface.__class__.__name__
        if kind == "Plane":
            axis = _along(surface.Axis)
            address = _plane_at(axis, round(sum(a * b for a, b in zip(axis, surface.Position)), 4))
            address = "plane " + address
        elif kind == "Cylinder":
            axis = _along(surface.Axis)
            centre = surface.Center
            reach = sum(a * b for a, b in zip(axis, centre))
            seat = tuple(round(c - reach * a + 0.0, 4) for c, a in zip(centre, axis))
            address = "%s diameter %s, axis %s, at %s" % (
                "hole" if _concave(face, surface) else "boss",
                _g(round(surface.Radius * 2, 4)), _vector(axis), _vector(seat))
        elif kind == "Cone":
            address = "cone axis %s, apex %s, angle %s" % (
                _vector(_along(surface.Axis)), _vector(_rounded(surface.Apex)),
                _g(round(abs(math.degrees(surface.SemiAngle)), 2)))
        elif kind == "Toroid":
            address = "torus axis %s, centre %s, radii %s and %s" % (
                _vector(_along(surface.Axis)), _vector(_rounded(surface.Center)),
                _g(round(surface.MajorRadius, 4)), _g(round(surface.MinorRadius, 4)))
        else:
            poles = getattr(surface, "getPoles", None)
            if poles:
                points = [point for row in surface.getPoles() for point in row]
                low = [min(point[i] for point in points) for i in range(3)]
                high = [max(point[i] for point in points) for i in range(3)]
            else:
                box = face.BoundBox
                low, high = [box.XMin, box.YMin, box.ZMin], [box.XMax, box.YMax, box.ZMax]
            address = "%s bbox x %s..%s, y %s..%s, z %s..%s" % (
                kind, *[_g(round(v, 1) + 0.0) for pair in zip(low, high) for v in pair])
        found[address] = found.get(address, 0.0) + face.Area
    return found


def _rounded(point, places=4):
    return tuple(round(value, places) for value in (point.x, point.y, point.z))


def _g(value):
    return "%.6g" % value


def _vector(values):
    return "(%s)" % ", ".join(_g(value + 0.0) for value in values)


def _plane_at(axis, offset):
    named = {(1.0, 0.0, 0.0): "x", (0.0, 1.0, 0.0): "y", (0.0, 0.0, 1.0): "z"}.get(axis)
    if named:
        return "%s = %s" % (named, _g(offset))
    return "normal %s at offset %s" % (_vector(axis), _g(offset))


def _concave(face, surface):
    """Whether the material lies outside this cylinder face, so it is a hole and not a boss."""
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    radial = face.valueAt(u, v) - surface.Center
    along = radial.dot(surface.Axis)
    radial = radial - surface.Axis * along
    return face.normalAt(u, v).dot(radial) < 0


def _cut(entries, title):
    shown = entries[:_INVENTORY_LINES]
    if len(entries) > len(shown):
        shown.append("... and %d more" % (len(entries) - len(shown)))
    return ["%s:" % title] + ["  " + entry for entry in shown] if entries else []


def _family(surface):
    """Which family of surfaces this face belongs to, and where along it it sits."""
    kind = surface.__class__.__name__
    radius = getattr(surface, "Radius", None)
    axis = getattr(surface, "Axis", None)
    centre = getattr(surface, "Center", None)
    if radius is None or axis is None:
        return (kind, None, None), None
    return ((kind, round(radius * 2, 6), _along(axis)),
            tuple(round(v, 6) for v in centre) if centre is not None else None)


def _along(axis):
    """
    An axis is a line: a bore's normal pointing in or out is the same axis.

    The zero is added so that a flipped zero reads as 0.0 rather than -0.0.
    """
    direction = [round(value, 6) for value in (axis.x, axis.y, axis.z)]
    leading = next((value for value in direction if value), 0.0)
    flip = -1 if leading < 0 else 1
    return tuple(value * flip + 0.0 for value in direction)


_SCRATCH = "DaedalusTypeProbe"


def _inventoried(obj):
    """
    Every property the object has, as the running FreeCAD reports it, shared
    by `describe_object` and `describe_type`. An enumeration is named where
    there is one -- `'Through all'` for `ThroughAll` is a Preflight failure
    no property list would catch. An empty-string value is dropped: a third
    of one listing was `"group": ""` and `"documentation": ""`.
    """
    inventory = []
    for prop in obj.PropertiesList:
        entry = {"name": prop, "type": obj.getTypeIdOfProperty(prop)}
        for key, said in (("group", obj.getGroupOfProperty(prop)),
                          ("documentation", obj.getDocumentationOfProperty(prop))):
            if said:
                entry[key] = said
        try:
            value = _readable(getattr(obj, prop))
        except Exception as exc:
            value = "<unreadable: %s>" % type(exc).__name__
        if not _opaque(value):
            entry["value"] = value
        spellings = _enumerations(obj, prop)
        if spellings:
            entry["enumeration"] = spellings
        inventory.append(entry)
    return inventory


def _opaque(value):
    """
    Whether this value tells the reader nothing it could act on.

    `<Shape object at 0x7f8247480>` has no textual value and its address
    changes every run — knowing the property is there is the whole of what a
    Shape-valued property has to say, and the entry already says that.
    """
    if value is None or value == "":
        return True
    return isinstance(value, str) and value.startswith("<") and " object at 0x" in value


def _enumerations(obj, prop):
    """The spellings an enumeration property accepts, or None where it is not one."""
    try:
        return obj.getEnumerationsOfProperty(prop) or None
    except Exception:
        return None


def _described_type(type_id):
    """
    What a type has, asked of a throwaway Document and never of the user's.
    """
    import FreeCAD

    _load_geometry_modules()
    was_active = FreeCAD.ActiveDocument
    scratch = FreeCAD.newDocument(_SCRATCH, hidden=True, temp=True)
    try:
        try:
            obj = scratch.addObject(type_id, "Probe")
        except Exception as exc:
            raise ToolError(
                "This build cannot create %r: %s. Call supported_types to see what it has."
                % (type_id, exc)
            )
        return {"type": obj.TypeId, "properties": _inventoried(obj)}
    finally:
        FreeCAD.closeDocument(scratch.Name)
        if was_active is not None:
            FreeCAD.setActiveDocument(was_active.Name)


def build_registry(document=None, pipeline=None, dispatch=_direct) -> ToolRegistry:
    """
    Build the registry against a Document.

    `document` may be a Document or a callable returning one; leaving it None
    resolves `FreeCAD.ActiveDocument` at call time, which is what the addon
    wants — the user may close and open files under us. `pipeline` is the
    apply pipeline, `None` for the read-only Tools Level 2 introspection
    tests want, and `dispatch` reads a Gui touch (the live selection, the
    active Body) -- identity headless, a live GUI's marshaller otherwise.
    """
    def active():
        import FreeCAD

        doc = document() if callable(document) else document
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            raise ToolError("There is no open Document. Ask the user to create or open one.")
        return doc

    registry = ToolRegistry()

    _register_document(registry, active, dispatch)
    _register_introspection(registry, active)
    _register_material(registry)

    if pipeline is not None:
        _register_apply(registry, pipeline, lambda: _selection(dispatch))

    return registry


def _register_document(registry, active, dispatch=_direct):
    @registry.tool(
        "snapshot",
        "The cheap summary of Document state: what objects exist, what is active, "
        "and what the user has selected. Not the whole tree — call describe_object "
        "for detail on one object.",
    )
    def snapshot():
        doc = active()
        objects = [
            {"name": o.Name, "label": o.Label, "type": o.TypeId, "errors": _errors(o)}
            for o in doc.Objects[:_SNAPSHOT_LIMIT]
        ]
        result = {
            "document": doc.Name,
            "objects": objects,
            "object_count": len(doc.Objects),
            "active_body": _active_body(doc, dispatch),
            "selection": _selection(dispatch),
        }
        if len(doc.Objects) > _SNAPSHOT_LIMIT:
            result["truncated"] = "showing %d of %d objects" % (_SNAPSHOT_LIMIT, len(doc.Objects))
        return result

    @registry.tool(
        "base_inventory",
        "The part an editing run starts from, in words, or none. Driven by the loop "
        "to brief Design, never by a phase.",
    )
    def base_inventory_tool():
        return {"inventory": base_inventory(active())}

    @registry.tool(
        "base_addresses",
        "The face addresses of the base part and of the Body as it stands. Driven by "
        "the loop to find what a Step removed, never by a phase.",
    )
    def base_addresses_tool():
        return base_addresses(active()) or {"base": {}, "now": []}

    @registry.tool(
        "overall_size",
        "How big the finished part is. Driven by the loop once before Review, "
        "never by a phase.",
    )
    def overall_size():
        return {"size_mm": _overall_size(active())}

    @registry.tool(
        "document_copy",
        "The live Document saved to a throwaway file, for a phase kernel to open. "
        "Driven by the loop, never by a phase; the caller deletes the file.",
    )
    def document_copy():
        doc = active()
        return {"path": dispatch(lambda: copy_of(doc))}

    @registry.tool(
        "dependencies",
        "What this object is built from and what would break if it changed. "
        "Check this before modifying or deleting anything.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The object's Name."}},
            "required": ["name"],
        },
    )
    def dependencies(name):
        doc = active()
        obj = doc.getObject(name)
        if obj is None:
            raise ToolError(
                "No object named %r. Existing: %s"
                % (name, ", ".join(o.Name for o in doc.Objects) or "(the Document is empty)")
            )
        return {
            "name": obj.Name,
            "depends_on": [o.Name for o in obj.OutList],
            "used_by": [o.Name for o in obj.InList],
        }


def _register_introspection(registry, active):
    @registry.tool(
        "describe_object",
        "Every property this object actually has in the running FreeCAD, with its "
        "type, group, documentation and current value. Use this instead of guessing "
        "a property name.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The object's Name, not its Label."}},
            "required": ["name"],
        },
    )
    def describe_object(name):
        doc = active()
        obj = doc.getObject(name)
        if obj is None:
            by_label = doc.getObjectsByLabel(name)
            if not by_label:
                raise ToolError(
                    "No object named %r. Existing: %s"
                    % (name, ", ".join(o.Name for o in doc.Objects) or "(the Document is empty)")
                )
            if len(by_label) > 1:
                raise ToolError(
                    "Label %r matches more than one object: %s. Use Name instead of Label."
                    % (name, ", ".join(o.Name for o in by_label))
                )
            obj = by_label[0]

        described = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "state": list(obj.State or []),
            "properties": _inventoried(obj),
        }

        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            box = shape.BoundBox
            described["shape"] = {
                "faces": len(shape.Faces),
                "edges": len(shape.Edges),
                "vertexes": len(shape.Vertexes),
                "volume": round(shape.Volume, 6),
                "bound_box": [round(v, 6) for v in
                              (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)],
            }
        return described

    @registry.tool(
        "describe_type",
        "Every property a type has, before any object of it exists — with the "
        "spellings this build accepts where a property is a choice between named "
        "values. Use this to check a type you are about to create; use "
        "describe_object for one that is already in the Document.",
        {
            "type": "object",
            "properties": {"type_id": {"type": "string",
                                       "description": "The full type, e.g. 'PartDesign::Pocket'."}},
            "required": ["type_id"],
        },
    )
    def describe_type(type_id):
        return _described_type(type_id)

    @registry.tool(
        "supported_types",
        "The object types this Document can actually create, straight from the "
        "running FreeCAD. Filter with a prefix such as 'Part::' or 'PartDesign::'.",
        {
            "type": "object",
            "properties": {"prefix": {"type": "string", "description": "Optional case-insensitive prefix filter."}},
        },
    )
    def supported_types(prefix=None):
        doc = active()
        _load_geometry_modules()
        types = sorted(doc.supportedTypes())
        if prefix:
            lowered = prefix.lower()
            types = [t for t in types if t.lower().startswith(lowered)]
        return {"types": types, "count": len(types)}


def _register_material(registry):
    @registry.tool(
        "materials",
        "The material cards this FreeCAD has, with the uuid ShapeMaterial needs. A "
        "uuid is not something to guess and a card's density is not something to set "
        "— pick the card that already has the density asked for.",
        {
            "type": "object",
            "properties": {"name": {"type": "string",
                                    "description": "Optional case-insensitive name filter, e.g. 'alumin'."}},
        },
    )
    def materials(name=None):
        """
        The material cards this build has, filtered by name where one is given.

        The 200 cards FreeCAD 1.1 ships are the same list every call, so an
        unfiltered answer is a page of it. A title block says "plain carbon
        steel" where cards say "Steel-Generic", so an unmatched name is
        matched on a word of itself, and failing that gets the whole shelf —
        an empty list read as "no material to assign" once cost a recorded
        run every revision it had.
        """
        try:
            import Materials
        except ImportError:
            raise ToolError("This FreeCAD build has no Materials module, so no material "
                            "can be assigned.")
        cards = Materials.MaterialManager().Materials

        def listed(match):
            return sorted(({"name": card.Name, "uuid": uuid,
                            "density": _physical(card, "Density")}
                           for uuid, card in cards.items() if match(card.Name.lower())),
                          key=lambda entry: entry["name"].lower())

        wanted = (name or "").lower()
        found = listed(lambda card: wanted in card)
        widened = bool(wanted) and not found
        if widened:
            words = [word for word in wanted.split() if len(word) > 2]
            found = (listed(lambda card: any(word in card for word in words))
                     or listed(lambda card: True))
        result = {"materials": found[:_MATERIALS_LIMIT], "count": len(found)}
        if widened:
            result["nothing_matched"] = (
                "no card's name contains %r, so a wider list is given instead — pick the "
                "nearest card rather than treating the material as unavailable." % name)
        if len(found) > _MATERIALS_LIMIT:
            result["truncated"] = ("showing %d of %d — filter by name"
                                   % (_MATERIALS_LIMIT, len(found)))
        return result


def _physical(card, quantity):
    """One physical value off a material card, or None where it has none."""
    try:
        value = card.getPhysicalValue(quantity)
    except Exception:
        return None
    return str(value) if value is not None else None


def _register_apply(registry, pipeline, selection):
    @registry.tool(
        "apply_step",
        "Run one Step against the Document, as one undoable unit. You get back "
        "either 'applied' or 'failed' with the traceback to correct against.",
        {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python for this Step. `doc`, `App`, `Part`, `Sketcher` "
                                   "and `PartDesign` are in scope. "
                                   "No Gui.*, no live selection, and no literal element "
                                   "names such as Edge12 — compute references instead.",
                },
                "label": {
                    "type": "string",
                    "description": "Short name for the undo entry, e.g. 'Pad the base sketch'.",
                },
                "op": {
                    "type": "string",
                    "description": "This Step's operation, one of the 36 in plan.md's closed "
                                   "list — carried straight from the Plan, not asked for here.",
                },
                "kernel_result": {
                    "type": "object",
                    "description": "What the kernel already found running this Step for real "
                                   "(DAE-A-53): created, deleted, modified, names, signature, "
                                   "objects. Carried by the loop, not asked for here.",
                },
                "design_model": {
                    "type": "boolean",
                    "description": "False where the caller has no Design model in scope, so the "
                                   "lint rules about reading one are left out. Carried by the "
                                   "loop, not asked for here.",
                },
                "design": {
                    "type": "object",
                    "description": "The Design model, read-only in the Step's own namespace "
                                   "(DAE-411). Carried by the loop, not asked for here.",
                },
                "plan_values": {
                    "type": "object",
                    "description": "The numbers Plan froze at declare time, read by name from "
                                   "plan_values in the Step's own namespace. Carried by the "
                                   "loop, not asked for here.",
                },
            },
            "required": ["code"],
        },
    )
    def apply_step(code, label="Daedalus Step", op=None, kernel_result=None, design=None,
                   plan_values=None, design_model=True):
        """
        There is no rehearsal behind this Tool any more: from here up, a
        Step was applied or it was not, and there is no third thing to know
        about.
        """
        return pipeline.apply(code, selection=selection(), label=label,
                              op=op, kernel_result=kernel_result,
                              design=design, plan_values=plan_values,
                              design_model=design_model).as_tool_result()

    @registry.tool(
        "undo_step",
        "Undo the last Step's transaction on the live Document -- the same undo entry "
        "apply_step committed. Driven by a Plan revision that drops that Step, never by "
        "a phase.",
    )
    def undo_step():
        return pipeline.undo_step()

    @registry.tool(
        "undo_depth",
        "How many Steps the live Document could still undo. Driven by a Plan "
        "revision before it drops any Step, never by a phase.",
    )
    def undo_depth():
        return {"depth": pipeline.undo_depth()}

    @registry.tool(
        "check_assertions",
        "Run read-only checks against a throwaway copy of the finished Document. "
        "Driven by the loop once per run, never by a phase.",
        {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    )
    def check_assertions(code):
        return pipeline.check_assertions(code)

    @registry.tool(
        "review_views",
        "Capture the four views Review judges. Driven by the loop at the end of a "
        "run, never by a phase.",
        {"type": "object", "properties": {}},
    )
    def review_views():
        return {"views": pipeline.review_views()}



def _readable(value):
    """Turn a FreeCAD property value into something JSON can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_readable(v) for v in value[:20]]
    if hasattr(value, "Name") and hasattr(value, "TypeId"):
        return value.Name
    return str(value)
