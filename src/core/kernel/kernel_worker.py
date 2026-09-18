"""
The child half of the kernel: a headless FreeCAD child process started by
`core/kernel/kernel.py`, one JSON request per stdin line and one tagged
response per stdout line. `SENTINEL` and `step_namespace` must import
without starting the server, so `freecadcmd`'s renamed `__name__` is
checked before serving starts, and a crash here is a normal outcome the
parent restarts from, with no shutdown message, because the parent kills
rather than asks; the kernel holds one live Document across many Steps, a
failed Step reopens the last checkpoint rather than continuing on a
Document a following Step should never have seen. Measured: 45 of 152
kernel calls failed across one review's sessions -- 15 on the Step's own
volume assert, 10 on a None deref -- so a failing Step is handed `volume`,
the idle-feature diagnosis and the Document's own objects, not only the
traceback.
"""
import contextlib
import difflib
import io
import json
import linecache
import os
import sys
import tempfile
import traceback
import types

SENTINEL = "@@KERNEL@@"


def respond(payload):
    sys.stdout.write(SENTINEL + json.dumps(payload) + "\n")
    sys.stdout.flush()


def _ensure_on_path():
    """
    The installed addon leaves `sys.path` and this checkout's `src/` goes on
    it: `core` is a namespace package, so a second directory holding one
    merges in rather than losing, and what is bound from it answers imports.
    """
    src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path[:] = [entry for entry in sys.path
                   if entry != src and not os.path.isdir(os.path.join(entry, "core"))]
    sys.path.insert(0, src)
    for name, module in list(sys.modules.items()):
        if name != "core" and not name.startswith("core."):
            continue
        where = [getattr(module, "__file__", None) or ""] + list(getattr(module, "__path__", []))
        if any(w and not os.path.abspath(w).startswith(src + os.sep) for w in where):
            del sys.modules[name]


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

    `ShapeMaterial` hands back a fresh wrapper on every read with no
    value equality, so two untouched reads compare unequal -- excluded
    by FreeCAD property type, not name, so no other Tool reopens the trap.
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
    a call this process hooked.

    Diff runs before recompute, not after: a computed property like `Shape`
    changes for every object downstream the moment recompute runs.
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

    def touched(self):
        return bool(self.created or self.deleted or self.modified())


_HINT_LIMIT = 60

STEP_MODULES = ("Part", "Sketcher", "PartDesign", "Materials")


def edges_between(shape, a, b):
    """
    The edges of *shape* where a face matching predicate *a* meets a face
    matching predicate *b*, each a function from one `Part.Face` to
    True/False, matched by `Edge.isSame` rather than by a literal `Edge12`/
    `Face3` name. An edge counts only where its two adjacent faces split one
    to *a* and the other to *b* -- a face answering both never pairs with
    itself.
    """
    import Part

    if not callable(a) or not callable(b):
        raise TypeError(
            "edges_between wants two predicates over Shape.Faces, each a function "
            "from one Face to True/False -- got %r and %r" % (a, b))
    a_faces = [face for face in shape.Faces if a(face)]
    b_faces = [face for face in shape.Faces if b(face)]
    if not a_faces:
        raise ValueError("edges_between's first predicate matched no face of the shape")
    if not b_faces:
        raise ValueError("edges_between's second predicate matched no face of the shape")

    names = []
    for at, edge in enumerate(shape.Edges, start=1):
        adjacent = shape.ancestorsOfType(edge, Part.Face)
        if len(adjacent) != 2:
            continue
        one, other = adjacent
        a_then_b = (any(face.isSame(one) for face in a_faces)
                    and any(face.isSame(other) for face in b_faces))
        b_then_a = (any(face.isSame(other) for face in a_faces)
                    and any(face.isSame(one) for face in b_faces))
        if a_then_b or b_then_a:
            names.append("Edge%d" % at)
    return names


def _read_only(value):
    """
    *value* with every nested `dict`/`list` frozen -- `types.MappingProxyType`
    for a mapping, a `tuple` of frozen elements for a list -- so a Step that
    tries to write through it (`design['x'] = ...`, `design['x'].append(...)`)
    gets a `TypeError` instead of quietly changing what Design established.
    """
    if isinstance(value, dict):
        return types.MappingProxyType({key: _read_only(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_read_only(item) for item in value)
    return value


def step_namespace(doc, design=None):
    """
    What a Step's code runs with, built identically by the kernel and the GUI's
    replay or they run different programs. Sketch geometry lives outside
    Sketcher in every FreeCAD version, so leaving Part out turns correct code
    into a NameError answered by an invented Sketcher.Circle, and FreeCAD's own
    Mod/Mesh/App directory shares the name `App` on sys.path, so a Step's bare
    `import App` finds an empty namespace package unless sys.modules holds the
    real module first. *design* is carried in read-only alongside the
    Document rather than through a working directory that does not survive
    Apply or replay -- absent where no run has one.
    """
    import FreeCAD

    sys.modules["App"] = FreeCAD

    namespace = {"App": FreeCAD, "FreeCAD": FreeCAD, "doc": doc, "__name__": "__step__",
                 "edges_between": edges_between}
    if design is not None:
        namespace["design"] = _read_only(design)
    for module in STEP_MODULES:
        try:
            namespace[module] = __import__(module)
        except ImportError:
            pass  # except-pass-allowed: an optional Step module not installed in this build
    return namespace


class _NamedDoc:
    """
    The live Document, wrapped so `doc.addObject` records the name
    FreeCAD actually allocated.

    FreeCAD renames rather than raises when a name is taken; a Step
    reaching for its own string gets the wrong object silently.
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


def _what_the_document_holds(doc, name):
    """
    Answer a dereference of `None` with the objects that do exist.

    `'NoneType' object has no attribute 'Shape'` names neither the lookup that
    failed nor what it could have found, and FreeCAD renames an object whose
    Name is taken, so asked-for and allocated names routinely differ.
    """
    if doc is None:
        return ""
    try:
        held = ", ".join("%s (%s, %s)" % (o["name"], o["label"], o["type"])
                         for o in summarise(doc))
    except BaseException:
        return ""
    return ("\nThe lookup before %r returned None -- nothing in the Document answered it."
            "\nThe Document holds: %s" % (name, held))


def introspect(exc, doc=None):
    """
    Answer an AttributeError with what the object actually has. FreeCAD
    names only the missing attribute, so a second guess burns Step
    budget; introspection answers it here rather than hoping the
    assistant asks first. PropertiesList suits a DocumentObject (`dir()`
    is eighty names of plumbing); a module/class gets the unbound
    descriptor instead.
    """
    obj, name = getattr(exc, "obj", None), getattr(exc, "name", None)
    if not name:
        return ""
    if obj is None:
        return _what_the_document_holds(doc, name)
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


def _features_not_wired_to_a_body(doc):
    """
    Every PartDesign feature the Document holds that no Body's Group claims.

    `doc.addObject('PartDesign::Pad', ...)` computes the feature's own
    Shape from its Profile regardless of who owns it, so it looks correct
    and Up-to-date; only the Body it was meant to extend is left with
    `Tip` unset and its own `Shape.Volume` raising "shape is invalid".
    """
    wired = {member.Name for obj in doc.Objects if obj.TypeId == "PartDesign::Body"
             for member in obj.Group}
    return [obj.Name for obj in doc.Objects
            if obj.isDerivedFrom("PartDesign::Feature") and obj.Name not in wired]


def _bodies_in_pieces(doc):
    """
    Every PartDesign Body whose result is more than one solid.

    A sketch of disjoint profiles pads into separate lumps, and nothing
    raises. Only Bodies — a Part::Compound is many solids on purpose.
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
    Every PartDesign Body whose Shape fails OCC's own `isValid()`.

    A self-touching wire pads into a solid that recomputes Up-to-date, and
    nothing about that state says the geometry is broken.
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
    Every PartDesign Body whose Shape carries a negative signed volume.

    A boss cut instead of added, a profile wound the wrong way. The Body
    recomputes Up-to-date; only the sign says it came out inside-out.
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


def _edge_concavity_sign(shape, edge):
    """
    Convex/concave sign of one Base edge: `normal.dot(other.CenterOfMass -
    point)` at its midpoint, negative for convex. Beats the Solution Notes'
    `cross(n1, n2).dot(tangent)` -- OCC's own per-edge curve direction is
    not consistent around one rim, so that one splits a uniform corner set.
    `None` without exactly two adjacent faces, or an edge-on normal.
    """
    import Part

    faces = shape.ancestorsOfType(edge, Part.Face)
    if len(faces) != 2:
        return None
    mid = 0.5 * (edge.FirstParameter + edge.LastParameter)
    point = edge.valueAt(mid)
    face, other = faces
    u, v = face.Surface.parameter(point)
    normal = face.normalAt(u, v)
    sign = normal.dot(other.CenterOfMass - point)
    if abs(sign) < 1e-9:
        return None
    return -1 if sign < 0 else 1


def _fillets_with_mixed_concavity(doc):
    """
    Every `PartDesign::Fillet` whose `Base` edges are not all one concavity.

    A single radius on a Base that mixes concave and convex edges pulls half
    a rim outward and half inward -- the report still says the Step
    succeeded, because a mixed selection recomputes clean.
    """
    mixed = []
    for obj in doc.Objects:
        if obj.TypeId != "PartDesign::Fillet":
            continue
        base = getattr(obj, "Base", None)
        if not base or not base[0]:
            continue
        feature, names = base
        shape = getattr(feature, "Shape", None)
        if shape is None or shape.isNull():
            continue
        by_sign = {}
        for name in names:
            edge = shape.getElement(name)
            sign = _edge_concavity_sign(shape, edge)
            if sign is None:
                continue
            by_sign.setdefault(sign, []).append(name)
        if len(by_sign) > 1:
            sides = ", ".join("%s: %s" % ("concave" if sign > 0 else "convex", ", ".join(edges))
                              for sign, edges in sorted(by_sign.items(), reverse=True))
            mixed.append("%s: Base mixes concave and convex edges (%s)" % (obj.Name, sides))
    return mixed


_A_DRESSUP = {"PartDesign::Fillet": ("Radius", "makeFillet"),
              "PartDesign::Chamfer": ("Size", "makeChamfer")}

_SMALLER = (0.75, 0.5, 0.25)


def _why_it_failed(obj):
    """What the Document says about a feature in error, and what it will not."""
    return ". ".join(said for said in (obj.getStatusString(),
                                       _why_a_dressup_failed(obj)) if said)


def _objects_left_in_error(doc):
    """Every object a recompute left `Invalid`, one line each."""
    return ["%s (%s): %s" % (o.Name, o.TypeId, _why_it_failed(o))
            for o in doc.Objects if o.State and "Invalid" in o.State]


def _why_a_dressup_failed(obj):
    """
    What a fillet or a chamfer will not say for itself.

    Constraint: a refused dressup names no edge and no size, so the
    selection is rebuilt on the base feature to find which and whether the
    size fits, tried at `_SMALLER` -- three quarters, a half, a quarter.
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
    Volume plus the BoundBox corners for one feature.

    Not `Shape.hashCode()`: it is stable for one live Shape but changes
    across a checkpoint save/reload on identical geometry. Volume and
    BoundBox are exactly reproduced after a save/reload/recompute.
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
    What *obj* actually points at: an `App::Link`'s `LinkedObject` or a
    Sketch's `ExternalGeometry` — the reference `copyObject` cannot be
    trusted to reproduce and `addExternal` re-resolves by name.
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
    A landed Step reduced to what a replay must reproduce exactly.

    Object count, names and total volume are not enough: a Step whose code
    runs over the wrong, same-named object matches all three. `links`
    closes that gap with the resolved target of every `App::Link` and Sketch
    `ExternalGeometry` reference.
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

    A datum plane's `Shape` has no `Solids` — OCC's volume on an unbounded
    Face is numerically unstable rather than a clean zero.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.Solids:
        return 0.0
    return shape.Volume


def _volume_moved(doc, created):
    """
    How much material each feature this Step created actually moved.

    Two volume reads the volume-neutral check below makes anyway, handed back
    rather than left for the Step's own code to derive.
    """
    moved = {}
    for name in sorted(created):
        obj = doc.getObject(name)
        if obj is None or not obj.isDerivedFrom(_MOVES_MATERIAL):
            continue
        base = getattr(obj, "BaseFeature", None)
        before = _volume_of(base) if base is not None else 0.0
        now = _volume_of(obj)
        moved[name] = {"before": round(before, 6), "after": round(now, 6),
                       "moved": round(now - before, 6)}
    return moved


def _features_that_moved_no_material(doc, created):
    """
    Features this Step created that came out the same volume they went in.

    A Pocket sketched on the face at YMin with its normal pointing into the
    part cuts along -Y, away from the solid, and hands back its base shape
    untouched. FreeCAD calls it 'Up-to-date' and 'Valid'.
    """
    idle = []
    for name, volume in _volume_moved(doc, created).items():
        before, now = volume["before"], volume["after"]
        if abs(now - before) <= _VOLUME_TOLERANCE * max(1.0, abs(before)):
            idle.append((doc.getObject(name), before, now))
    return idle


def _idle_feature_cause(before, after_reversed_flip):
    """
    3 of 12 recorded volume-neutral failures moved volume when `Reversed`
    was flipped (profile on the right face pointing the wrong way), the
    other 9 did not (profile that overlaps the solid at neither sign).
    """
    if abs(after_reversed_flip - before) > _VOLUME_TOLERANCE * max(1.0, abs(before)):
        return "direction"
    return "no-overlap"


def _diagnose_idle_feature(obj, before, now):
    """
    Tell a wrong-direction Pocket from one whose profile never touches the
    solid, by flipping `Reversed` and recomputing.
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

_IDLE_HEADLINE = "this Step added a feature that neither removed nor added any material"

_IDLE_EXPLANATION = (
    "A Pad, Pocket, Hole or Fillet that leaves the Body's volume exactly as it found it "
    "did not happen: a Pocket whose profile is extruded away from the solid cuts nothing."
)


def _idle_feature_report(doc, created):
    """
    What this Step's volume-neutral features did, as the hint a failure carries.

    A Step's own volume assert raises before any check in `_run_step_body`
    runs, so every failure carries this and not only the clean runs. A hint is
    never worth losing the traceback it explains: anything raised here is lost.
    """
    try:
        idle = _features_that_moved_no_material(doc, created)
        if not idle:
            return ""
        lines = [
            "%s (%s): %s mm3 before, %s mm3 after -- %s"
            % (obj.Name, obj.TypeId, before, now,
               _IDLE_CAUSE_MESSAGE[_diagnose_idle_feature(obj, before, now)])
            for obj, before, now in idle
        ]
        return "\n%s\n%s" % ("\n".join(lines), _IDLE_EXPLANATION)
    except BaseException:
        return ""


STDOUT_LIMIT = 4000


def _capturing():
    """
    Somewhere for a Step's own output to go that is still a real text stream:
    `sys.stdout.buffer.write` is what a Step is free to call, and a bare
    `StringIO` has no `buffer` at all.
    """
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace",
                            write_through=True)


def _printed(said):
    """
    What a Step's code wrote to stdout, capped and marked where it was cut.

    A Step's print is the one channel an agent has for looking at the Document
    -- its answer says what changed, not what is there -- and the one field of
    that answer the model can grow without limit.
    """
    said.flush()
    text = said.buffer.getvalue().decode("utf-8", "replace")
    if len(text) <= STDOUT_LIMIT:
        return text
    return text[:STDOUT_LIMIT] + "\n...cut here, %d characters in all" % len(text)


def _measured(namespace):
    """
    An assertion script's own `held`/`failed` lists, read from the
    namespace it ran in, not parsed from a traceback.

    `None`, not `[]`, for a name never defined -- absent must read
    differently from "measured, and empty"; only a list or tuple counts.
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
    What a Step's own recompute should touch: its writes, resolved to
    live objects, plus everything downstream via `InList` -- unlike
    `doc.recompute(objs)` alone, which skips it and would leave an edited
    feature stale (not `Invalid`) past the failure scan. `None` asks for
    the full recompute instead.
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


_SESSION = {"doc": None, "checkpoint": None, "names": {}, "design": None}


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

    if touched.touched():
        scope = _recompute_scope(doc, touched)
        if scope is None:
            doc.recompute()
        else:
            doc.recompute(scope)

    failures = _objects_left_in_error(doc)
    if failures:
        raise RuntimeError("recompute left these objects in error:\n" + "\n".join(failures))

    mixed = _fillets_with_mixed_concavity(doc)
    if mixed:
        raise RuntimeError(
            "this Step built a fillet whose Base mixes concavities:\n" + "\n".join(mixed)
            + "\nOne radius on a mixed selection pulls the concave edges inward and the "
              "convex edges outward -- split Base by concavity (a face's own normal against "
              "the other face's CenterOfMass at the edge's midpoint) and fillet one side "
              "at a time.")

    if _features_that_moved_no_material(doc, touched.created):
        raise RuntimeError(_IDLE_HEADLINE)

    orphaned = _features_not_wired_to_a_body(doc)
    if orphaned:
        raise RuntimeError(
            "this Step created a feature no Body claims:\n" + "\n".join(orphaned)
            + "\nA feature added with doc.addObject computes its own Shape from its "
              "Profile and looks correct on its own, but the Body it was meant to extend "
              "still has none -- its own Shape.Volume raises 'shape is invalid' until the "
              "feature is wired in with body.addObject(feature) and body.Tip = feature, "
              "the same way the Body's other features were added.")

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
        "volume": _volume_moved(doc, touched.created),
        "signature": signature_of(doc),
    }
    held, failed = _measured(namespace)
    if held is not None:
        result["held"] = held
    if failed is not None:
        result["failed"] = failed
    render_path = request.get("render_path")
    if render_path:
        from core.kernel.preflight_render import render

        result["render"] = render(doc, render_path)
    return result


def run_step(request):
    """
    Run one Step against the kernel's live Document. A `document` in
    *request* is a fresh handoff: the session starts over from that path,
    with a name map seeded from *request*'s own `names`, and
    `ephemeral=True` skips checkpoint writes. Pass writes the checkpoint;
    fail reopens the last checkpoint only when `_Touched.touched()` says
    the Step actually changed the Document, since a read-only probe that
    raises leaves it exactly as it was.
    """
    ephemeral = request.get("ephemeral", False)
    if "document" in request or _SESSION["doc"] is None:
        _SESSION["doc"] = _open_document(request.get("document"))
        _SESSION["names"] = dict(request.get("names") or {})
        if not ephemeral:
            _save_checkpoint(_SESSION["doc"])
    if "design" in request:
        _SESSION["design"] = request["design"]

    doc = _SESSION["doc"]
    names = dict(_SESSION["names"])
    namespace = step_namespace(_NamedDoc(doc, names), design=_SESSION["design"])
    namespace["names"] = names

    touched = _Touched(doc)
    said = _capturing()
    try:
        with contextlib.redirect_stdout(said):
            response = _run_step_body(doc, namespace, touched, request)
    except AssertionError as exc:
        touched.diff(doc)
        response = {"ok": False,
                    "traceback": trimmed_traceback(exc) + _idle_feature_report(doc, touched.created),
                    "volume": _volume_moved(doc, touched.created)}
        held, failed = _measured(namespace)
        if held is not None:
            response["held"] = held
        if failed is not None:
            response["failed"] = failed
    except BaseException as exc:
        touched.diff(doc)
        response = {"ok": False,
                    "traceback": (trimmed_traceback(exc) + introspect(exc, doc)
                                  + _idle_feature_report(doc, touched.created)),
                    "volume": _volume_moved(doc, touched.created)}
    printed = _printed(said)
    if printed:
        response["stdout"] = printed

    if response.get("ok"):
        if not ephemeral:
            _save_checkpoint(doc)
        _SESSION["names"] = names
    else:
        if not ephemeral and touched.touched():
            _reload_checkpoint()
    response["checkpoint"] = _SESSION["checkpoint"]
    response["names"] = dict(_SESSION["names"])
    return response


def reading(request):
    """
    One outline off a drawing, built as a sketch and put to the solver.

    Here rather than in the addon's process because this is the process whose
    death is a normal outcome: `Sketcher.Constraint` is the call that segfaults.
    """
    from core.sheets import sketch_check

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
    _ensure_on_path()
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
