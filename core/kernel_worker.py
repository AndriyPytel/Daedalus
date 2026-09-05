"""
The child half of the kernel. Runs inside a headless FreeCAD started by
`core/kernel.py`, as a bare script -- so it may import the standard library
and FreeCAD, and nothing of the addon's own.

Two things are imported *out* of it, by name: `SENTINEL` by `core/kernel.py`,
and `step_namespace` by `core/apply.py`, so that the kernel and the GUI's
own transaction replay cannot build different namespaces for the same Step.
`reading` imports `core.sketch_check`; `run_step` imports `render` off
`core.preflight_render`. Both cost a `sys.path` entry and buy the one thing
this process exists for -- the solver runs where its death is survivable
(ADR-0002).

One JSON request per line on stdin, one tagged JSON response per line on
stdout. FreeCAD writes its own banner to stdout, so responses carry a
sentinel prefix and the parent ignores everything else.

The kernel owns one live Document across many Steps. A Step whose code (and
whose checks, below) succeed gets a checkpoint written to disk and the live
Document keeps running -- no reopen. A Step that fails is undone by closing
the live Document and reopening the last checkpoint, so the Document a
following Step sees is exactly what it would have been had the failed Step
never run. A request that hands a `document` path is a fresh handoff: the
session (Document, checkpoint and name map) starts over from that path, or
from an empty Document where no path is given.

Death is a normal outcome here. If the code under test takes the C++ core
down, this process goes with it and the parent restarts a new one -- which is
the entire point of the arrangement (ADR-0002). That is also why there is no
shutdown message: the parent kills the process rather than asking it to go.

`freecadcmd` runs a script with `__name__` set to its basename rather than
"__main__", and importing this module as `core.kernel_worker` for its
`SENTINEL` must not start serving, so both names are named at the foot.
"""
import difflib
import json
import linecache
import os
import sys
import tempfile
import traceback

SENTINEL = "@@KERNEL@@"


def respond(payload):
    sys.stdout.write(SENTINEL + json.dumps(payload) + "\n")
    sys.stdout.flush()


def _ensure_on_path():
    """
    Put the repo root on `sys.path` if it is not there already, so this
    child process can import `core.*` the way `run_step` and `reading` both
    need to, on demand rather than at module load.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)


def summarise(doc):
    return [
        {"name": o.Name, "label": o.Label, "type": o.TypeId}
        for o in doc.Objects
    ]


DERIVED = {"Shape"}

_EXCLUDED_FROM_DIFF = {"Name", "Label"} | DERIVED


def _property_value(obj, prop):
    try:
        return getattr(obj, prop)
    except Exception:
        return None


def _comparable(obj, prop):
    """
    Whether *prop* is worth diffing by value.

    `ShapeMaterial` (`Materials::PropertyMaterial`) hands back a freshly
    built wrapper object on every read, with no value equality of its own --
    two reads of the same untouched property compare unequal, which would
    read as a modification on every single Step of a Document that has one.
    It is excluded by its FreeCAD property type rather than by name, so the
    same trap does not reopen under a different Tool's own material-typed
    property.
    """
    try:
        return "Material" not in obj.getTypeIdOfProperty(prop)
    except Exception:
        return True


def _properties_snapshot(doc):
    """
    Every object's own `PropertiesList`, by value, `Name`/`Label` and the
    derived properties in `DERIVED` left out -- the exclusion rule
    `fcc-research-doc-diff.md` demonstrated, so a rename or a recompute never
    reads as a modification.
    """
    snapshot = {}
    for obj in doc.Objects:
        properties = getattr(obj, "PropertiesList", None) or ()
        snapshot[obj.Name] = {
            prop: _property_value(obj, prop)
            for prop in properties
            if prop not in _EXCLUDED_FROM_DIFF and _comparable(obj, prop)
        }
    return snapshot


def _changed_properties(before, after):
    changed = set()
    for prop in set(before) | set(after):
        if prop not in before or prop not in after:
            changed.add(prop)
            continue
        try:
            if before[prop] != after[prop]:
                changed.add(prop)
        except Exception:
            changed.add(prop)
    return changed


class _Touched:
    """
    What the Step did, observed off the Document rather than recorded through
    a call this process hooked (FCC-300) -- the open kernel namespace (FCC-299)
    means a Step's code no longer has to route through anything here at all.

    A `PropertiesList` snapshot is taken at construction, before the Step's
    code runs; `diff()` takes a second one and compares by value. It runs
    before recompute, not after: a computed property like `Shape` changes for
    every object downstream of a legitimate edit the moment recompute runs,
    which is exactly the false positive `DERIVED` already existed to guard
    against under the signal-based mechanism this replaces.
    """

    def __init__(self, doc):
        self._before = _properties_snapshot(doc)
        self.created = set()
        self.deleted = set()
        self._changed = {}

    def diff(self, doc):
        after = _properties_snapshot(doc)
        self.created = set(after) - set(self._before)
        self.deleted = set(self._before) - set(after)
        self._changed = {
            name: _changed_properties(self._before[name], after[name])
            for name in set(self._before) & set(after)
        }

    def modified(self):
        return {name for name, properties in self._changed.items() if properties}


_HINT_LIMIT = 60

STEP_MODULES = ("Part", "Sketcher", "PartDesign", "Materials")


def step_namespace(doc):
    """
    What a Step's code runs with.

    The kernel and the GUI's own replay (`core/apply.py::run_in_transaction`)
    must build this identically or they are running different programs
    (ADR-0002), so both call this rather than each spelling it out.

    Sketch geometry is spelled Part.Circle and Part.LineSegment in every
    version of FreeCAD and the Sketcher module holds none of it, so leaving
    Part out turns correct code into a NameError, which the assistant reads as
    "Part does not exist here" and answers by inventing Sketcher.Circle.
    Materials is here for the same reason: ShapeMaterial accepts only a
    Materials.Material and the one source of those is
    Materials.MaterialManager, so left out the correct code is a NameError and
    the assistant answers by inventing an App.Material that has no Name or
    Density.
    """
    import FreeCAD

    namespace = {"App": FreeCAD, "FreeCAD": FreeCAD, "doc": doc, "__name__": "__step__"}
    for module in STEP_MODULES:
        try:
            namespace[module] = __import__(module)
        except ImportError:
            pass
    return namespace


class _NamedDoc:
    """
    The live Document, wrapped so `doc.addObject(type, name)` records the name
    FreeCAD actually allocated under the name the Step asked for.

    The two differ exactly when an earlier Step, or a hand-edit already in the
    Document, holds that name first -- FreeCAD renames the new object rather
    than raising, and a later Step addressing it by the literal string it
    asked for reaches the wrong object, silently. `names` is what a later
    Step reaches through instead: `doc.getObject(names['Box'])`.

    Every other attribute passes straight through: this is not a new
    Document API, only the one seam a Step's code creates objects through.
    """

    def __init__(self, doc, names):
        self._doc = doc
        self._names = names

    def addObject(self, type_id, name=None, *args, **kwargs):
        if name is None:
            obj = self._doc.addObject(type_id, *args, **kwargs)
        else:
            obj = self._doc.addObject(type_id, name, *args, **kwargs)
            self._names[name] = obj.Name
        return obj

    def __getattr__(self, item):
        return getattr(self._doc, item)


def _name_of(obj):
    """
    What to call the thing in the hint.

    TypeId is the useful name for a DocumentObject, but on a class it is an
    unbound descriptor whose repr -- <attribute 'TypeId' of 'Base.BaseClass'
    objects> -- named nothing the assistant could act on.
    """
    type_id = getattr(obj, "TypeId", None)
    if isinstance(type_id, str):
        return type_id
    return getattr(obj, "__name__", None) or type(obj).__name__


def introspect(exc):
    """
    Answer an AttributeError with what the object actually has.

    FreeCAD names the attribute that is missing and nothing else, so the
    assistant guesses a second spelling of the same wrong idea and the Step
    budget goes on one dead end. Introspection is the answer to API knowledge
    here as everywhere (ADR-0006) -- this just applies it at the moment the
    guess fails, rather than hoping the assistant asks first (ADR-0008).

    PropertiesList is the sharp answer for a DocumentObject, where `dir()` is
    eighty names of mostly plumbing; on a module or a class it is the unbound
    descriptor, which is neither, hence the isinstance rather than a truth
    test. A hint is never worth losing the traceback it was meant to explain,
    so anything that goes wrong here gives back nothing.
    """
    obj, name = getattr(exc, "obj", None), getattr(exc, "name", None)
    if obj is None or not name:
        return ""
    try:
        public = sorted(a for a in dir(obj) if not a.startswith("_"))
        declared = getattr(obj, "PropertiesList", None)
        properties = sorted(declared) if isinstance(declared, (list, tuple)) else public
        hint = "\n%s has no %r." % (_name_of(obj), name)
        close = difflib.get_close_matches(name, public, n=3)
        if close:
            hint += " Closest existing: %s." % ", ".join(close)
        return hint + "\nIt has: %s" % ", ".join(properties[:_HINT_LIMIT])
    except BaseException:
        return ""


def _bodies_in_pieces(doc):
    """
    Every PartDesign Body whose result is more than one solid (FCC-27).

    A sketch of disjoint profiles pads into separate lumps, and nothing raises:
    the feature reports itself up-to-date and valid, so a check that only
    watched status would pass it unnoticed.

    Only Bodies. A Part::Compound or a Document holding several unrelated
    objects is many solids on purpose; a Body is single-solid by design, which
    is what makes the count mean something here.
    """
    scattered = []
    for obj in doc.Objects:
        if obj.TypeId != "PartDesign::Body":
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        solids = shape.Solids
        if len(solids) > 1:
            scattered.append("%s: %d separate solids" % (obj.Name, len(solids)))
    return scattered


def _bodies_with_invalid_shape(doc):
    """
    Every PartDesign Body whose Shape fails OCC's own `isValid()` (FCC-255).

    A self-touching wire -- two lobes pinched at one vertex -- pads into a
    single solid that recomputes Up-to-date, and nothing about that state
    says the geometry is broken: `isValid()` is the one question worth asking
    on every Step, cheap enough to afford (FCC-253 Q4).
    """
    invalid = []
    for obj in doc.Objects:
        if obj.TypeId != "PartDesign::Body":
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        if not shape.isValid():
            invalid.append("%s: invalid shape" % obj.Name)
    return invalid


def _bodies_with_inverted_solid(doc):
    """
    Every PartDesign Body whose Shape carries a negative signed volume
    (FCC-255).

    A boss cut instead of added, a profile wound the wrong way -- the
    Solution Notes' own examples of a feature that comes out inverted. The
    Body still recomputes Up-to-date; only the sign of `Shape.Volume` says
    it came out inside-out.
    """
    inverted = []
    for obj in doc.Objects:
        if obj.TypeId != "PartDesign::Body":
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        if shape.Volume < 0:
            inverted.append("%s: inverted solid (volume %s)" % (obj.Name, shape.Volume))
    return inverted


_A_DRESSUP = {"PartDesign::Fillet": ("Radius", "makeFillet"),
              "PartDesign::Chamfer": ("Size", "makeChamfer")}

_SMALLER = (0.75, 0.5, 0.25)


def _why_it_failed(obj):
    """What the Document says about a feature in error, and what it will not."""
    return ". ".join(said for said in (obj.getStatusString(),
                                       _why_a_dressup_failed(obj)) if said)


def _objects_left_in_error(doc):
    """
    Every object a recompute left `Invalid` (FCC-11), one line each.

    Named like its siblings (`_bodies_in_pieces`, `_bodies_with_invalid_shape`,
    `_bodies_with_inverted_solid`) so the post-Step checklist is a set of
    functions a completeness test can enumerate by name, this one included --
    it used to be the one check still written inline.
    """
    return ["%s (%s): %s" % (o.Name, o.TypeId, _why_it_failed(o))
            for o in doc.Objects if o.State and "Invalid" in o.State]


def _why_a_dressup_failed(obj):
    """
    What a fillet or a chamfer will not say for itself.

    A PartDesign dressup OCC refuses says either nothing at all --
    `getStatusString()` is the empty string, and `Fillet_R2
    (PartDesign::Fillet): ` was the whole of the failure Execute was handed
    (`logs/sessions/069d21dbac10416392533fe7c06b62d4.jsonl`) -- or `BRep_API:
    command not done`, which names no edge and no size either. Both leave one
    thing to change, how the selection is written, and it is rarely the thing
    that is wrong: Execute rewrote the selection twice and then wrote nothing.

    The exception survives one level down, so the same selection is built again
    on the base feature's own shape, where `Standard_ConstructionError` is what
    comes back. Two questions follow it, and both are answered by building it
    again rather than by reasoning about it: which of the selected edges is the
    one the set cannot carry, and whether the size is what will not fit. An
    edge named and a radius that works are what tell Plan whether to change the
    selection or the number.

    `_SMALLER` is what to try instead: three quarters of the size asked for, a
    half and a quarter. Three builds are what a Step that has already failed can
    afford, and a size that fits nowhere in that range is a selection to change
    rather than a number.
    """
    how = _A_DRESSUP.get(obj.TypeId)
    base = getattr(obj, "Base", None)
    if how is None or not base:
        return ""
    of_it, made = how
    size = getattr(obj, of_it, None)
    size = getattr(size, "Value", size)
    shape = getattr(base[0], "Shape", None)
    if not size or shape is None or shape.isNull():
        return ""

    chosen = _the_edges_of(shape, base[1])
    refused = _refusal(shape, made, size, [edge for _, edge in chosen]) if chosen else ""
    if not refused:
        return ""

    said = ("the same selection built on %s is refused with %s"
            % (base[0].Name, refused))
    for how_many in range(1, len(chosen) + 1):
        if _refusal(shape, made, size, [edge for _, edge in chosen[:how_many]]):
            said += (". %s is the first of the %d selected that will not build with the "
                     "edges before it" % (chosen[how_many - 1][0], len(chosen)))
            break
    for share in _SMALLER:
        if not _refusal(shape, made, size * share, [edge for _, edge in chosen]):
            said += ". The whole selection builds at %s mm" % round(size * share, 3)
            break
    return said


def _the_edges_of(shape, chosen):
    """
    The edges a dressup's sub-element names stand for, each under its own name.

    A dressup is selected by edge or by face, and a face stands for every edge
    of its outline. Named against the base shape rather than against the face,
    so what comes back is what a Step can be written with.
    """
    edges = []
    for name in chosen:
        try:
            element = shape.getElement(name)
        except Exception:
            continue
        for edge in getattr(element, "Edges", [element]):
            named = _the_name_of(shape, edge)
            if named not in [was for was, _ in edges]:
                edges.append((named, edge))
    return edges


def _the_name_of(shape, edge):
    for at, one in enumerate(shape.Edges, start=1):
        if one.isSame(edge):
            return "Edge%d" % at
    return "an edge"


def _refusal(shape, made, size, edges):
    """What the same dressup raises at Part level, or "" where it builds."""
    try:
        getattr(shape, made)(size, edges)
    except Exception as exc:
        return str(exc).strip() or exc.__class__.__name__
    return ""


_MOVES_MATERIAL = "PartDesign::Feature"

_VOLUME_TOLERANCE = 1e-9


def _geometry_of(obj):
    """
    Volume plus the BoundBox corners for one feature (FCC-260).

    Not `Shape.hashCode()`: measured directly, it is stable for one live
    Shape but changes across a checkpoint save/reload on identical geometry --
    `check_assertions` reopens a copy of the Document -- so a hash that moves
    on reload would report every feature as regressed on every single
    Assertion pass. Volume and BoundBox were measured the same way and are
    exactly reproduced after a save/reload/recompute of unchanged geometry.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    box = shape.BoundBox
    return (shape.Volume, box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)


def _geometry_snapshot(doc):
    return {o.Name: _geometry_of(o) for o in doc.Objects}


def _resolved_targets(obj):
    """
    What *obj* actually points at, for the one property this checks: an
    `App::Link`/`App::LinkGroup`'s `LinkedObject`/`ElementList`, or a
    Sketch's `ExternalGeometry` -- the reference `copyObject` cannot be
    trusted to reproduce and `addExternal` re-resolves by name (FCC-302).

    Read by `TypeId` rather than `getattr` alone: `LinkedObject` is `None`
    on an unset `App::Link`, which is a legitimate signature (nothing
    resolved yet) rather than "this object has no such property".
    """
    type_id = getattr(obj, "TypeId", "") or ""
    if type_id == "App::Link":
        linked = getattr(obj, "LinkedObject", None)
        return getattr(linked, "Name", None)
    if type_id == "App::LinkGroup":
        return sorted(getattr(element, "Name", None)
                      for element in (getattr(obj, "ElementList", None) or ()))
    external = getattr(obj, "ExternalGeometry", None)
    if external:
        return sorted(getattr(ref, "Name", None) for ref, _subs in external)
    return None


def signature_of(doc):
    """
    A landed Step reduced to what a replay must reproduce exactly (FCC-302).

    Object count, names and total volume are not enough on their own: a
    Step whose code runs over the wrong, same-named object matches all
    three while addressing something the kernel never verified
    (`fcc-openq-replay.md`). `links` closes that gap with the resolved
    target of every `App::Link`, `App::LinkGroup` and Sketch
    `ExternalGeometry` reference, keyed by the referring object's own Name
    and left out entirely where an object has none of those to resolve.
    """
    links = {}
    for obj in doc.Objects:
        target = _resolved_targets(obj)
        if target is not None:
            links[obj.Name] = target
    return {
        "count": len(doc.Objects),
        "names": sorted(o.Name for o in doc.Objects),
        "volume": round(sum(_volume_of(o) for o in doc.Objects), 6),
        "links": links,
    }


def _volume_of(obj):
    """
    A feature's own material, zero for anything that is not a closed solid.

    A datum plane's `Shape` is not null but has no `Solids` -- OCC's own
    volume calculation on an unbounded planar Face is numerically unstable
    rather than a clean zero, and it differs between two separate processes
    computing the same Document (FCC-302's own signature check caught this:
    count, names and every other feature's volume matched exactly).
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.Solids:
        return 0.0
    return shape.Volume


def _features_that_moved_no_material(doc, created):
    """
    Features this Step created that came out the same volume they went in
    (FCC-96).

    The silent failure this exists for: a Pocket sketched on the face at YMin
    with its normal pointing into the part cuts along -Y, away from the solid,
    intersects nothing, and hands back its base shape untouched. FreeCAD calls
    it 'Up-to-date' and 'Valid', and a check that only watched status would
    pass it on that -- and five bores in a row went missing from a part every
    phase reported as applied.

    Read off `BaseFeature` rather than off the Body, because that is the one
    comparison that names the feature at fault: a feature's base is exactly the
    solid it was handed, so the difference is exactly what it did, whatever
    else the same Step built around it.

    A feature is a step the user would recognise as cutting or adding metal; a
    Sketch, a datum or a Body itself is not, and neither is anything outside
    PartDesign.

    Returns the idle features themselves, not formatted text (FCC-246): the
    caller diagnoses each one before it has anything to print, and this
    function's own comparison stays exactly what it was.
    """
    idle = []
    for name in sorted(created):
        obj = doc.getObject(name)
        if obj is None or not obj.isDerivedFrom(_MOVES_MATERIAL):
            continue
        base = getattr(obj, "BaseFeature", None)
        before = _volume_of(base) if base is not None else 0.0
        now = _volume_of(obj)
        if abs(now - before) <= _VOLUME_TOLERANCE * max(1.0, abs(before)):
            idle.append((obj, before, now))
    return idle


def _idle_feature_cause(before, after_reversed_flip):
    """
    FCC-245's own replay, as a decision rule: 3 of the 12 recorded
    volume-neutral failures moved volume when `Reversed` was flipped (a
    profile on the right face pointing the wrong way), the other 9 did not
    (a profile that overlaps the solid at neither sign of `Reversed`). Scored
    against `tests/recorded/preflight_volume_neutral_failures.json`, this
    rule reproduces the recorded cause in all 12 -- it is the same comparison
    FCC-245 made, against the same tolerance `_features_that_moved_no_material`
    already uses to call a feature idle in the first place.
    """
    if abs(after_reversed_flip - before) > _VOLUME_TOLERANCE * max(1.0, abs(before)):
        return "direction"
    return "no-overlap"


def _diagnose_idle_feature(obj, before, now):
    """
    Tell a wrong-direction Pocket from one whose profile never touches the
    solid, by flipping `Reversed` and recomputing -- FCC-245's own method,
    replayed live at the moment the check fires instead of offline afterward.

    Only features that carry `Reversed` (Pad, Pocket, Groove and their
    Additive/Subtractive siblings) are candidates: a Fillet or a Hole has no
    direction to flip, and `None` tells the caller to say it cannot tell
    rather than naming a cause it never measured.
    """
    if not hasattr(obj, "Reversed"):
        return None
    original = obj.Reversed
    try:
        obj.Reversed = not original
        obj.Document.recompute([obj])
        flipped = _volume_of(obj)
    finally:
        obj.Reversed = original
        obj.Document.recompute([obj])
    return _idle_feature_cause(before, flipped)


_IDLE_CAUSE_MESSAGE = {
    "direction": (
        "flipping Reversed changed its volume: the profile sits on the right "
        "face but the cut travels away from the solid instead of into it. Set "
        "Reversed=True (or use Type='ThroughAll' or SideType='Symmetric' for a "
        "through cut rather than a length that may run the wrong way)."
    ),
    "no-overlap": (
        "flipping Reversed left its volume unchanged either way: the profile "
        "does not overlap the solid at all, at either sign of Reversed. Check "
        "the sketch's placement against the Body's BoundBox -- Reversed will "
        "not fix this."
    ),
    None: (
        "flipping Reversed is not possible on this feature type, so the check "
        "cannot tell a wrong direction from a placement that misses the solid. "
        "Compare the feature's placement against the Body's BoundBox by hand."
    ),
}


def _measured(namespace):
    """
    An assertion script's own `held` and `failed` lists, read back out of
    the namespace it ran in rather than parsed from a traceback string.

    `None`, not `[]`, for a name the script never defined -- a Step's code has
    no reason to define either, and an older or deviant check script may not
    either, so absent has to read differently from "measured, and empty". Only
    a list or a tuple counts: a Step happens to be free to call a local
    variable `held` or `failed` for its own purposes, and whatever that is
    must not be handed to the caller as an acceptance-entry list.
    """
    held = namespace.get("held")
    failed = namespace.get("failed")
    return (list(held) if isinstance(held, (list, tuple)) else None,
            list(failed) if isinstance(failed, (list, tuple)) else None)


def register_step_source(code):
    """
    Put a Step's own source into `linecache` under the name `exec(compile(...))`
    gives it, `"<step>"`, so a frame that lands there prints the line that
    failed instead of nothing. `"<step>"` is not a file on disk; `linecache`
    otherwise has nowhere to read it from.
    """
    linecache.cache["<step>"] = (len(code), None, code.splitlines(True), "<step>")


def trimmed_traceback(exc):
    """
    Format *exc* with the worker's own `exec(compile(...))` frame cut off the
    top, so only the Step's frames -- from `"<step>"` down -- and the standard
    call into `run_step()` or `run_in_transaction()` above it reach the reader.

    An exception that never passes through a `"<step>"` frame (an error raised
    by the worker itself, not the Step it ran) keeps its traceback whole
    rather than losing it to a search that never matches.
    """
    tb = exc.__traceback__
    trimmed = tb
    while trimmed is not None and trimmed.tb_frame.f_code.co_filename != "<step>":
        trimmed = trimmed.tb_next
    return "".join(traceback.format_exception(type(exc), exc, trimmed or tb))


def _recompute_scope(doc, touched):
    """
    What a Step's own recompute should touch: this Step's own writes,
    resolved back to live objects, plus everything downstream of them.

    `doc.recompute(objs)` recomputes exactly *objs* and their own
    dependencies -- it does not walk `InList` the way the argument-free form
    does, so a Step that edits an existing feature rather than only
    appending a new one at the tip would leave every feature built on it
    `Touched` with a stale `Shape`, not `Invalid`: the failure scan right
    after (`"Invalid" in o.State`) would not see it, and the check would pass
    a Step silently on the wrong shape. Following `InList` out to its whole
    closure is what keeps this scoped recompute as correct as the bare
    `doc.recompute()` it replaces, while still bounded to what this Step
    could actually have affected rather than the whole Document.

    `None` asks the caller for the ordinary full recompute -- a Step that
    only deleted something leaves nothing of its own to target, and the
    Document's dependency graph is what has to catch the consequences of a
    deletion, not a list of names.
    """
    names = touched.created | touched.modified()
    roots = [o for o in (doc.getObject(name) for name in names) if o is not None]
    if not roots:
        return None
    seen = set()
    scope = []
    stack = list(roots)
    while stack:
        obj = stack.pop()
        if obj.Name in seen:
            continue
        seen.add(obj.Name)
        scope.append(obj)
        stack.extend(obj.InList)
    return scope


_SESSION = {"doc": None, "checkpoint": None, "names": {}}


def _checkpoint_path():
    if _SESSION["checkpoint"] is None:
        handle, path = tempfile.mkstemp(prefix="kernel_checkpoint_", suffix=".FCStd")
        os.close(handle)
        _SESSION["checkpoint"] = path
    return _SESSION["checkpoint"]


def _close_every_document():
    import FreeCAD

    for doc in list(FreeCAD.listDocuments().values()):
        FreeCAD.closeDocument(doc.Name)


def _open_document(path):
    import FreeCAD

    _close_every_document()
    return FreeCAD.openDocument(path) if path else FreeCAD.newDocument("Kernel")


def _reload_checkpoint():
    """
    Undo a failed Step by closing the live Document and reopening the last
    checkpoint -- the Document a following Step sees is exactly what it would
    have been had the failed Step never run.
    """
    import FreeCAD

    path = _SESSION["checkpoint"]
    _close_every_document()
    _SESSION["doc"] = (FreeCAD.openDocument(path) if path and os.path.exists(path)
                       else FreeCAD.newDocument("Kernel"))


def _save_checkpoint(doc):
    doc.saveCopy(_checkpoint_path())


def _run_step_body(doc, namespace, touched, request):
    """
    Execute a Step's code against *doc* and the checks that make it wrong.

    Raises on any failure -- an `AssertionError` from the code itself, or a
    `RuntimeError` from a check below -- for `run_step` to catch and
    translate into a checkpoint reload. Returns the success payload.
    """
    register_step_source(request["code"])
    exec(compile(request["code"], "<step>", "exec"), namespace)  # noqa: S102
    touched.diff(doc)

    scope = _recompute_scope(doc, touched)
    if scope is None:
        doc.recompute()
    else:
        doc.recompute(scope)

    failures = _objects_left_in_error(doc)
    if failures:
        raise RuntimeError("recompute left these objects in error:\n" + "\n".join(failures))

    idle = _features_that_moved_no_material(doc, touched.created)
    if idle:
        lines = []
        for obj, before, now in idle:
            cause = _diagnose_idle_feature(obj, before, now)
            lines.append(
                "%s (%s): %s mm3 before, %s mm3 after -- %s"
                % (obj.Name, obj.TypeId, round(before, 6), round(now, 6),
                   _IDLE_CAUSE_MESSAGE[cause])
            )
        raise RuntimeError(
            "this Step added a feature that neither removed nor added any material:\n"
            + "\n".join(lines)
            + "\nA Pad, Pocket, Hole or Fillet that leaves the Body's volume exactly as it "
              "found it did not happen: a Pocket whose profile is extruded away from the solid "
              "cuts nothing."
        )

    scattered = _bodies_in_pieces(doc)
    if scattered:
        raise RuntimeError(
            "this Step left a Body in more than one piece:\n" + "\n".join(scattered)
            + "\nA PartDesign Body is one solid. Disjoint profiles in one sketch pad into "
              "separate lumps rather than one part -- the profiles that make up the outline "
              "have to meet, and anything that is genuinely a second part belongs in a "
              "second Body.")

    invalid = _bodies_with_invalid_shape(doc)
    if invalid:
        raise RuntimeError(
            "this Step left a Body with an invalid Shape:\n" + "\n".join(invalid)
            + "\nOCC's own isValid() says the geometry is broken -- a self-touching wire or "
              "a face that folds back on itself, not a Body it will let through unnoticed.")

    inverted = _bodies_with_inverted_solid(doc)
    if inverted:
        raise RuntimeError(
            "this Step left a Body with an inverted solid:\n" + "\n".join(inverted)
            + "\nA negative signed volume is a solid that came out inside-out -- a boss cut "
              "instead of added, a profile wound the wrong way.")

    result = {
        "ok": True,
        "objects": summarise(doc),
        "created": sorted(touched.created),
        "deleted": sorted(touched.deleted),
        "modified": sorted(touched.modified()),
        "geometry": _geometry_snapshot(doc),
        "signature": signature_of(doc),
    }
    held, failed = _measured(namespace)
    if held is not None:
        result["held"] = held
    if failed is not None:
        result["failed"] = failed
    render_path = request.get("render_path")
    if render_path:
        _ensure_on_path()
        from core.preflight_render import render

        result["render"] = render(doc, render_path)
    return result


def run_step(request):
    """
    Run one Step against the kernel's live Document.

    A `document` in *request* is a fresh handoff: the session starts over
    from that path (or an empty Document, for no path at all), with a
    baseline checkpoint and a name map seeded from *request*'s own `names`
    (empty where not given) rather than always reset -- a caller replaying
    against the Document's true current state (FCC-302) still needs what an
    earlier Step allocated, even though every apply() call is its own fresh
    handoff. Anything else continues the running session.

    Pass: the live Document keeps running and a checkpoint is written -- one
    `saveCopy`, not a reopen. Fail: the live Document is thrown away and the
    last checkpoint reopened, so the Step never happened, and the name map
    reverts with it.
    """
    if "document" in request or _SESSION["doc"] is None:
        _SESSION["doc"] = _open_document(request.get("document"))
        _SESSION["names"] = dict(request.get("names") or {})
        _save_checkpoint(_SESSION["doc"])

    doc = _SESSION["doc"]
    names = dict(_SESSION["names"])
    namespace = step_namespace(_NamedDoc(doc, names))
    namespace["names"] = names

    touched = _Touched(doc)
    try:
        response = _run_step_body(doc, namespace, touched, request)
    except AssertionError as exc:
        response = {"ok": False, "traceback": trimmed_traceback(exc)}
        held, failed = _measured(namespace)
        if held is not None:
            response["held"] = held
        if failed is not None:
            response["failed"] = failed
    except BaseException as exc:
        response = {"ok": False, "traceback": trimmed_traceback(exc) + introspect(exc)}

    if response.get("ok"):
        _save_checkpoint(doc)
        _SESSION["names"] = names
    else:
        _reload_checkpoint()
    response["checkpoint"] = _SESSION["checkpoint"]
    response["names"] = dict(_SESSION["names"])
    return response


def reading(request):
    """
    One outline off a drawing, built as a sketch and put to the solver.

    Here rather than in the addon's process because this is the process whose
    death is a normal outcome: the phases that read a sheet run on the loop's
    worker thread, and `Sketcher.Constraint` is the call that segfaults
    (FCC-19, FCC-126).
    """
    _ensure_on_path()
    from core import sketch_check

    outline, view, between = sketch_check.from_request(request)
    answer = {"ok": True}
    answer.update(sketch_check.as_response(sketch_check.checked(outline, view, between)))
    return answer


_ASKED_FOR = {"step": run_step, "reading": reading}


def serve(stdin=None, respond=respond):
    """
    Read requests off stdin until it closes, and answer each one.

    What the process will do is the `do` of the request; a request that does
    not say is a Step. Anything Python can still catch comes back as a
    correction, and anything it cannot takes this process down instead of
    FreeCAD.
    """
    stdin = stdin or sys.stdin
    respond({"ready": True, "pid": os.getpid()})

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            respond({"ok": False, "traceback": "malformed request: " + str(exc)})
            continue
        try:
            respond(_ASKED_FOR[request.get("do") or "step"](request))
        except BaseException as exc:
            respond({"ok": False, "traceback": trimmed_traceback(exc) + introspect(exc)})


if __name__ in ("__main__", "kernel_worker"):
    serve()
