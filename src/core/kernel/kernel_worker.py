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
import copy
import difflib
import functools
import io
import json
import linecache
import os
import shutil
import sys
import tempfile
import time
import traceback

SENTINEL = "@@KERNEL@@"

_PROTOCOL_STREAM = None


def _isolate_protocol_stream():
    """
    Give the wire protocol a private fd before any native code can write
    into fd 1: `doc.recompute()`'s progress bar prints straight to the
    process's real stdout, with no newline after its last, unfinished
    fragment, so that text glues onto the front of the next tagged reply
    line -- the parent's reader drops the malformed line silently and both
    sides block on `read()` forever, one per Step. Native writes still land
    somewhere (stderr) rather than vanishing, and the wire keeps fd 1's
    original destination through the duplicated fd instead.
    """
    global _PROTOCOL_STREAM
    original = os.dup(1)
    os.dup2(2, 1)
    _PROTOCOL_STREAM = os.fdopen(original, "w", encoding="utf-8")


def respond(payload):
    stream = _PROTOCOL_STREAM or sys.stdout
    stream.write(SENTINEL + json.dumps(payload) + "\n")
    stream.flush()


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
    """
    The name, label and TypeId of every object. A PartDesign::Boolean also
    carries its `Type` (Fuse, Cut or Common) as `boolean_type`, because that
    property, not the TypeId, says which operation it performs.
    """
    return [
        {"name": o.Name, "label": o.Label, "type": o.TypeId,
         **({"boolean_type": o.Type} if o.TypeId == "PartDesign::Boolean" else {})}
        for o in doc.Objects
    ]


DERIVED = {"Shape"}

_EXCLUDED_FROM_DIFF = {"Name", "Label"} | DERIVED


def _property_value(obj, prop):
    """
    A sketch's geometry is read as its serialised `Content`, since every read
    of `Geometry` hands back fresh wrappers that never compare equal.
    """
    try:
        value = getattr(obj, prop)
    except Exception:
        return None
    kind = getattr(obj, "getTypeIdOfProperty", lambda _: None)(prop)
    if kind == "Part::PropertyGeometryList":
        return [geometry.Content for geometry in value]
    return value


def _comparable(obj, prop):
    """
    Whether *prop* is worth diffing by value.

    `ShapeMaterial` hands back a fresh wrapper on every read with no
    value equality, and a `Part::PropertyPartShape` is a computed shape that
    does the same, so both are excluded by FreeCAD property type, not name.
    """
    try:
        kind = obj.getTypeIdOfProperty(prop)
        return "Material" not in kind and kind != "Part::PropertyPartShape"
    except Exception:
        return True


def _native():
    """The one seam into nativeifc: what a Step does to the IFC a Document holds is read and written through it."""
    from core import nativeifc

    return nativeifc


def _properties_snapshot(doc):
    """
    Every object's own `PropertiesList`, by value, `Name`/`Label` and the
    derived properties in `DERIVED` left out -- the exclusion rule
    `fcc-research-doc-diff.md` demonstrated, so a rename or a recompute never
    reads as a modification.
    """
    snapshot = {}
    ifc = _native().fingerprints(doc)
    for obj in doc.Objects:
        properties = getattr(obj, "PropertiesList", None) or ()
        snapshot[obj.Name] = {
            prop: _property_value(obj, prop)
            for prop in properties
            if prop not in _EXCLUDED_FROM_DIFF and _comparable(obj, prop)
        }
        if obj.Name in ifc:
            snapshot[obj.Name]["IFC"] = ifc[obj.Name]
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


def built_ok(obj):
    """
    Whether *obj* recomputed clean -- the one check every `DocumentObject`
    answers the same way. `isError()` exists on some `PartDesign::Feature`
    subtypes and not on others (`PartDesign::AdditivePipe` has none in this
    build), so a Step reaches for `obj.State` instead, the same field the
    kernel's own failure report reads.
    """
    return not (obj.State and "Invalid" in obj.State)


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


def pick_edges(shape, predicate, expect):
    """
    The edge names of *shape* where *predicate* -- a function from one
    `Part.Edge` to True/False -- answers True, or a `RuntimeError` naming
    the edges it found when their count is not *expect*. The lint treats
    a name assigned from this call as counted, the same as a Step's own
    `if len(...) != N: raise` guard.
    """
    if not callable(predicate):
        raise TypeError(
            "pick_edges wants a predicate over Shape.Edges, a function from one "
            "Edge to True/False -- got %r" % (predicate,))
    names = ["Edge%d" % at for at, edge in enumerate(shape.Edges, start=1) if predicate(edge)]
    if len(names) != expect:
        raise RuntimeError(
            "pick_edges wanted %d edges, found %d: %s" % (expect, len(names), names))
    return names


def pick_faces(shape, predicate, expect):
    """
    The face names of *shape* where *predicate* -- a function from one
    `Part.Face` to True/False -- answers True, or a `RuntimeError` naming
    the faces it found when their count is not *expect*. The lint treats
    a name assigned from this call as counted, the same as `pick_edges`.
    """
    if not callable(predicate):
        raise TypeError(
            "pick_faces wants a predicate over Shape.Faces, a function from one "
            "Face to True/False -- got %r" % (predicate,))
    names = ["Face%d" % at for at, face in enumerate(shape.Faces, start=1) if predicate(face)]
    if len(names) != expect:
        raise RuntimeError(
            "pick_faces wanted %d faces, found %d: %s" % (expect, len(names), names))
    return names


def _along(axis):
    return {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]


def plane_face(axis, at, tol=0.01):
    """
    A predicate for `pick_faces`: a planar face whose normal runs along global
    *axis* ('x', 'y' or 'z') and which lies at coordinate *at* on it.
    """
    direction = _along(axis)

    def predicate(face):
        surface = face.Surface
        if surface.__class__.__name__ != "Plane":
            return False
        normal = surface.Axis
        parallel = abs(abs(sum(a * b for a, b in zip(direction, normal))) - 1) < 1e-6
        return parallel and abs(sum(a * b for a, b in zip(direction, surface.Position)) - at) <= tol

    return predicate


def cylinder_face(diameter, axis=None, tol=0.01):
    """
    A predicate for `pick_faces`: a cylindrical face of this *diameter*, with
    its axis along global *axis* ('x', 'y' or 'z') where one is given.
    """
    def predicate(face):
        surface = face.Surface
        if surface.__class__.__name__ != "Cylinder" or abs(surface.Radius * 2 - diameter) > tol:
            return False
        if axis is None:
            return True
        return abs(abs(sum(a * b for a, b in zip(_along(axis), surface.Axis))) - 1) < 1e-6

    return predicate


def no_collateral(doc, volume_change=None, moved=None, measures=None):
    """
    Raises an AssertionError naming every way the Body's shape is not its BaseFeature edited as asked: more than one solid, invalid, a volume change other than *volume_change* mm3, a bounding box side outside *moved* that moved, checked only when *moved* is given.
    Does nothing where no Body has a BaseFeature, so a run that edits nothing is unaffected.
    """
    from core.edit_guard import Measures, collateral, measured

    body = next((obj for obj in doc.Objects
                 if obj.TypeId == "PartDesign::Body" and obj.BaseFeature is not None), None)
    if body is None:
        return
    measures = measures or Measures()
    reasons = collateral(measured(body.BaseFeature.Shape, measures), measured(body.Shape, measures),
                         volume_change, moved)
    assert not reasons, "these did not hold: no collateral change. " + "; ".join(reasons)


def bbox(shape):
    """
    *shape*'s bounding box computed from its exact geometry, not from
    `Shape.BoundBox`'s tessellation padding. `optimalBoundingBox()`'s
    default reads a cached triangulation when the shape carries one, so
    this always passes `useTriangulation=False` to read the surfaces
    themselves.
    """
    return shape.optimalBoundingBox(False)


def by_label(doc, label):
    """
    The one `DocumentObject` in *doc* whose Label is *label*, or a
    `LookupError` listing every Label the Document holds when there is
    none or more than one -- a Step's own Label guess never silently
    resolves to the wrong object.
    """
    matches = [obj for obj in doc.Objects if obj.Label == label]
    if len(matches) != 1:
        raise LookupError(
            "by_label wanted one object labelled %r, found %d: %s"
            % (label, len(matches), [obj.Label for obj in doc.Objects]))
    return matches[0]


def sketch_at(body, label, normal, at):
    """
    A new Sketch in *body* on the plane perpendicular to global axis *normal*
    ('x', 'y' or 'z'), lying at global coordinate *at* along it. Local (u, v)
    are global (y, z) for 'x', (x, z) for 'y' and (x, y) for 'z'; the sketch's
    own normal points along +X, -Y and +Z respectively, so pick a Pad or
    Pocket direction with `Reversed`.
    """
    roles = {"x": "YZ_Plane", "y": "XZ_Plane", "z": "XY_Plane"}
    axes = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
    if normal not in roles:
        raise ValueError("sketch_at's normal must be 'x', 'y' or 'z', got %r" % (normal,))
    plane = next(o for o in body.Origin.OriginFeatures if o.Role == roles[normal])
    import FreeCAD
    facing = plane.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
    sign = 1 if facing.dot(FreeCAD.Vector(*axes[normal])) > 0 else -1
    sketch = body.newObject("Sketcher::SketchObject", label)
    sketch.AttachmentSupport = [(plane, "")]
    sketch.MapMode = "FlatFace"
    sketch.AttachmentOffset = FreeCAD.Placement(
        FreeCAD.Vector(0, 0, sign * at), FreeCAD.Rotation())
    sketch.Label = label
    sketch.Document.recompute()
    return sketch


def step_namespace(doc, base_faces=None, intent=None, outcome=None):
    """
    What a Step's code runs with, built identically by the kernel and the GUI's
    replay or they run different programs. Sketch geometry lives outside
    Sketcher in every FreeCAD version, so leaving Part out turns correct code
    into a NameError answered by an invented Sketcher.Circle, and FreeCAD's own
    Mod/Mesh/App directory shares the name `App` on sys.path, so a Step's bare
    `import App` finds an empty namespace package unless sys.modules holds the
    real module first.
    """
    import FreeCAD

    from core.edit_guard import RememberedMeasures

    sys.modules["App"] = FreeCAD

    measure = RememberedMeasures()
    namespace = {"measure": measure, "App": FreeCAD, "FreeCAD": FreeCAD, "doc": doc, "__name__": "__step__",
                 "edges_between": edges_between, "edges_by_concavity": edges_by_concavity,
                 "built_ok": built_ok, "pick_edges": pick_edges, "bbox": bbox,
                 "pick_faces": pick_faces, "plane_face": plane_face,
                 "cylinder_face": cylinder_face,
                 "no_collateral": functools.partial(no_collateral, measures=measure),
                 "by_label": by_label, "sketch_at": sketch_at}
    if base_faces is not None:
        namespace["base_faces"] = dict(base_faces)
    if intent is not None:
        namespace["intent"] = copy.deepcopy(intent)
    if outcome is not None:
        namespace["outcome"] = copy.deepcopy(outcome)
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


def _bodies_with(doc, defect):
    """
    The lines *defect* reports, one per PartDesign Body whose Shape it rejects.

    Only Bodies -- a Part::Compound is many solids on purpose.
    """
    found = []
    for obj in doc.Objects:
        if obj.TypeId != "PartDesign::Body":
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        said = defect(shape)
        if said:
            found.append("%s: %s" % (obj.Name, said))
    return found


def _in_pieces(shape):
    if len(shape.Solids) > 1:
        return "%d separate solids" % len(shape.Solids)


def _invalid(shape):
    if not shape.isValid():
        return "invalid shape"


def _inverted(shape):
    if shape.Volume < 0:
        return "inverted solid (volume %s)" % shape.Volume


def _pinched(shape):
    from core.manifold import check_fc

    return check_fc(shape)


def _not_round_trip_safe(shape):
    from core.manifold import step_round_trip_fc

    return step_round_trip_fc(shape)


_BODY_CHECKS = (
    (_in_pieces, "this Step left a Body in more than one piece:",
     "A PartDesign Body is one solid. Disjoint profiles in one sketch pad into "
     "separate lumps rather than one part -- the profiles that make up the outline "
     "have to meet, and anything that is genuinely a second part belongs in a "
     "second Body."),
    (_invalid, "this Step left a Body with an invalid Shape:",
     "OCC's own isValid() says the geometry is broken -- a self-touching wire or "
     "a face that folds back on itself, not a Body it will let through unnoticed."),
    (_inverted, "this Step left a Body with an inverted solid:",
     "A negative signed volume is a solid that came out inside-out -- a boss cut "
     "instead of added, a profile wound the wrong way."),
    (_pinched, "this Step left a Body that is not manifold:",
     "A cylinder face exactly tangent to another face leaves a wall of zero "
     "thickness there. OCC calls it valid, but the exported STEP fails the mesh check "
     "every consumer runs -- move the cylinder or change its radius so the faces "
     "overlap or leave a gap."),
    (_not_round_trip_safe, "this Step left a Body that does not survive a STEP round trip:",
     "OCC calls the Body valid, but the STEP written from it reads back with broken faces and the "
     "exported file is rejected as invalid. Coplanar faces left unmerged (Refine off), or a feature "
     "leaving a sliver or a ledge face that coincides with an earlier feature's face, make it -- "
     "rebuild the Step so the faces merge or overlap clearly."),
)


def _body_defects(doc):
    """
    The refusal for the first of `_BODY_CHECKS` some Body fails, or None; later checks are not run.

    Each check catches what recomputes Up-to-date and is still wrong: disjoint lumps, a
    self-touching wire, a solid inside-out, a wall of zero thickness OCC calls valid.
    """
    for defect, heading, advice in _BODY_CHECKS:
        found = _bodies_with(doc, defect)
        if found:
            return "%s\n%s\n%s" % (heading, "\n".join(found), advice)
    return None


_TANGENT_COS = 0.999


def _edge_concavity_sign(shape, edge):
    """
    Convex/concave sign of one Base edge: `normal.dot(other.CenterOfMass -
    point)` at its midpoint, negative for convex. Beats the Solution Notes'
    `cross(n1, n2).dot(tangent)` -- OCC's own per-edge curve direction is
    not consistent around one rim, so that one splits a uniform corner set.
    `None` without exactly two adjacent faces, an edge-on normal, or a
    dihedral angle near zero -- a fillet's own boundary is C0-tangent to
    its neighbour there, the dot product stays far from zero on a small
    face, and a fillet asked to round it again is silently skipped by OCC
    rather than refused.
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
    u2, v2 = other.Surface.parameter(point)
    other_normal = other.normalAt(u2, v2)
    if normal.dot(other_normal) > _TANGENT_COS:
        return None
    sign = normal.dot(other.CenterOfMass - point)
    if abs(sign) < 1e-9:
        return None
    return -1 if sign < 0 else 1


def _group_edges_by_sign(shape, names):
    """
    `{sign: [edge names]}` for *names*, each looked up on *shape* and signed
    by `_edge_concavity_sign`. An edge with no resolvable sign -- fewer than
    two adjacent faces, an edge-on normal, or a dihedral angle near zero --
    is left out of every group.
    """
    by_sign = {}
    for name in names:
        edge = shape.getElement(name)
        sign = _edge_concavity_sign(shape, edge)
        if sign is None:
            continue
        by_sign.setdefault(sign, []).append(name)
    return by_sign


def edges_by_concavity(shape, edges):
    """
    *edges* -- typically `edges_between`'s own return value -- split into
    one group per concavity sign, concave group first, so a Step can fillet
    one group at a time and never hand `doc.recompute()` a mixed Base. A
    selection that is already single-concavity comes back as one group
    holding the same edges.
    """
    by_sign = _group_edges_by_sign(shape, edges)
    return [names for _, names in sorted(by_sign.items(), reverse=True)]


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
        by_sign = _group_edges_by_sign(shape, names)
        if len(by_sign) > 1:
            sides = ", ".join("%s: %s" % ("concave" if sign > 0 else "convex", ", ".join(edges))
                              for sign, edges in sorted(by_sign.items(), reverse=True))
            mixed.append("%s: Base mixes concave and convex edges (%s)" % (obj.Name, sides))
    return mixed


def _raise_if_mixed_concavity(doc):
    """
    Refuse a mixed-concavity `Base` with its own sentence before OCC's
    `command not done` or `FrozenShape` can bury it under a generic
    recompute-error message.

    Called once before the recompute -- against Base edges resolved from a
    base feature the Step's own code already recomputed, so the check needs
    no recompute of its own -- and once after, since a base feature this
    same Step just created has no Shape yet at the first call and only
    becomes checkable once the recompute below gives it one.
    """
    mixed = _fillets_with_mixed_concavity(doc)
    if mixed:
        raise RuntimeError(
            "this Step built a fillet whose Base mixes concavities:\n" + "\n".join(mixed)
            + "\nOne radius on a mixed selection pulls the concave edges inward and the "
              "convex edges outward -- split Base by concavity (a face's own normal against "
              "the other face's CenterOfMass at the edge's midpoint) and fillet one side "
              "at a time.")


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
    native = _native()
    signature = {
        "count": len(doc.Objects),
        "names": sorted(o.Name for o in doc.Objects),
        "volume": round(sum(_volume_of(o) for o in doc.Objects if not native.is_native(o)), 6),
        "links": links,
    }
    ifc = native.outlines(doc)
    if ifc:
        signature["ifc"] = ifc
    return signature


_VOLUME_RELATIVE_TOLERANCE = 1e-6


def signatures_match(landed, expected):
    """
    Whether a replay's *landed* signature reproduces *expected* closely
    enough to count as the same Step. Count, names, links and the IFC of a nativeifc project must match
    exactly; volume is compared with a relative tolerance, since the same
    geometry computed in two OCC processes does not round to the same
    sixth decimal.
    """
    if (landed["count"], landed["names"], landed["links"], landed.get("ifc")) != (
            expected["count"], expected["names"], expected["links"], expected.get("ifc")):
        return False
    scale = max(abs(landed["volume"]), abs(expected["volume"]), 1.0)
    return abs(landed["volume"] - expected["volume"]) <= _VOLUME_RELATIVE_TOLERANCE * scale


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

    Returns the cause and, for `"direction"`, the `Reversed` value opposite
    the one already on the object -- the value that would fix the cut.
    """
    if not hasattr(obj, "Reversed"):
        return None, None
    original = obj.Reversed
    try:
        obj.Reversed = not original
        obj.Document.recompute([obj])
        flipped = _volume_of(obj)
    finally:
        obj.Reversed = original
        obj.Document.recompute([obj])
    return _idle_feature_cause(before, flipped), not original


_IDLE_CAUSE_MESSAGE = {
    "direction": (
        "flipping Reversed changed its volume: the profile sits on the right "
        "face but the cut travels away from the solid instead of into it. Set "
        "Reversed=%s (or use Type='ThroughAll' or SideType='Symmetric' for a "
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
    never worth losing the traceback it explains, but the diagnostic's own
    exception is not thrown away either: it is reported in its place.
    """
    try:
        idle = _features_that_moved_no_material(doc, created)
        if not idle:
            return ""
        def _cause_message(obj, before, now):
            cause, fix = _diagnose_idle_feature(obj, before, now)
            message = _IDLE_CAUSE_MESSAGE[cause]
            return message % fix if cause == "direction" else message

        lines = [
            "%s (%s): %s mm3 before, %s mm3 after -- %s"
            % (obj.Name, obj.TypeId, before, now, _cause_message(obj, before, now))
            for obj, before, now in idle
        ]
        return "\n%s\n%s" % ("\n".join(lines), _IDLE_EXPLANATION)
    except BaseException as exc:
        return "\n(the volume-neutral feature diagnostic itself raised: %s)" % exc


def _past_the_tip(doc, created):
    """
    The features this call added to a Body after its Tip, which build nothing
    into `body.Shape` whatever their own Shape shows. `Body.addObject` moves
    the Tip onto a Pad but leaves it where it was for a PolarPattern.
    """
    lines = []
    for body in doc.Objects:
        if body.TypeId != "PartDesign::Body" or body.Tip is None:
            continue
        group = body.Group
        tip = group.index(body.Tip) if body.Tip in group else len(group)
        past = [obj for obj in group[tip + 1:]
                if obj.Name in created and obj.isDerivedFrom("PartDesign::Feature")]
        if past:
            lines.append("%s is past %s's Tip (%s), so %s.Shape does not have it: "
                         "set body.Tip = doc.getObject('%s') and recompute."
                         % (", ".join(obj.Name for obj in past), body.Name, body.Tip.Name,
                            body.Name, past[-1].Name))
    return "\n" + "\n".join(lines) if lines else ""


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


def _held(namespace):
    """A script's own `held` list, read from the namespace it ran in; `None` for a name never defined, so absent reads differently from empty."""
    held = namespace.get("held")
    return list(held) if isinstance(held, (list, tuple)) else None


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


SCRATCH = "KERNEL_SCRATCH"

_SESSION = {"doc": None, "checkpoint": None, "names": {}, "scratch": None}


def _scratch():
    """
    The directory this process keeps its checkpoint in: the one its parent made and will remove,
    or one of its own that it removes on the way out.
    """
    if _SESSION.get("scratch") is None:
        _SESSION["scratch"] = os.environ.get(SCRATCH) or tempfile.mkdtemp(prefix="kernel_checkpoint_%d_" % os.getpid())
    return _SESSION["scratch"]


def _checkpoint_path():
    if _SESSION["checkpoint"] is None:
        _SESSION["checkpoint"] = os.path.join(_scratch(), "checkpoint.FCStd")
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


def _checkpoint_exists():
    return _SESSION["checkpoint"] is not None and os.path.exists(_SESSION["checkpoint"])


def _save_checkpoint(doc):
    """
    `saveCopy` is a silent no-op when the destination equals `doc.FileName`
    -- which is exactly what it becomes after `_reload_checkpoint` opens the
    checkpoint itself. Saving to a sibling temp path first sidesteps that,
    and `os.replace` is atomic, so a reader never sees a half-written file.
    """
    path = _checkpoint_path()
    tmp_path = os.path.join(os.path.dirname(path), "saving.FCStd")
    _native().save_projects(doc, _scratch())
    doc.saveCopy(tmp_path)
    os.replace(tmp_path, path)


def _raise_if_broken(doc, touched):
    """
    Raise on whatever a Step left wrong in *doc*: objects in error, idle features, unwired features, a Body in pieces, invalid, inverted or not manifold.
    A Step that changed nothing in a Document it did not open cannot have left any of it, and these scans cost seconds on a big part.
    """
    failures = _objects_left_in_error(doc)
    if failures:
        raise RuntimeError("recompute left these objects in error:\n" + "\n".join(failures))

    _raise_if_mixed_concavity(doc)

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

    refusal = _body_defects(doc)
    if refusal:
        raise RuntimeError(refusal)


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

    _raise_if_mixed_concavity(doc)

    if touched.touched():
        scope = _recompute_scope(doc, touched)
        if scope is None:
            doc.recompute()
        else:
            doc.recompute(scope)

    if touched.touched() or "document" in request:
        _raise_if_broken(doc, touched)

    changed = touched.touched() or "document" in request
    result = {
        "ok": True,
        "objects": summarise(doc),
        "created": sorted(touched.created),
        "deleted": sorted(touched.deleted),
        "modified": sorted(touched.modified()),
        "geometry": _geometry_snapshot(doc) if changed or request.get("geometry") else {},
        "volume": _volume_moved(doc, touched.created),
        "signature": signature_of(doc) if changed else {},
    }
    held = _held(namespace)
    if held is not None:
        result["held"] = held
    render_path = request.get("render_path")
    if render_path:
        from core.kernel.preflight_render import render

        result["render"] = render(doc, render_path, view=request.get("render_view") or "iso")
    return result


def run_step(request):
    """
    Run one Step against the kernel's live Document. A `document` in
    *request* is a fresh handoff: the session starts over from that path,
    with a name map seeded from *request*'s own `names`, and
    `ephemeral=True` skips checkpoint writes. Pass writes the checkpoint;
    fail reopens the last checkpoint only when `_Touched.touched()` says
    the Step actually changed the Document, since a read-only probe that
    raises leaves it exactly as it was; `exploring=True` marks a call no
    Step declared, so a pass that touched the Document is reopened the
    same as a failed one, and the response names what was rolled back.
    """
    ephemeral = request.get("ephemeral", False)
    exploring = request.get("exploring", False)
    if "document" in request or _SESSION["doc"] is None:
        _SESSION["doc"] = _open_document(request.get("document"))
        _SESSION["names"] = dict(request.get("names") or {})
        if not ephemeral:
            _save_checkpoint(_SESSION["doc"])

    doc = _SESSION["doc"]
    names = dict(_SESSION["names"])
    namespace = step_namespace(_NamedDoc(doc, names),
                               base_faces=request.get("base_faces"),
                               intent=request.get("intent"), outcome=request.get("outcome"))
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
        held = _held(namespace)
        if held is not None:
            response["held"] = held
    except BaseException as exc:
        touched.diff(doc)
        response = {"ok": False,
                    "traceback": (trimmed_traceback(exc) + introspect(exc, doc)
                                  + _idle_feature_report(doc, touched.created)),
                    "volume": _volume_moved(doc, touched.created)}
    printed = _printed(said)
    if printed:
        response["stdout"] = printed

    past = _past_the_tip(doc, touched.created)
    if past:
        response["ok"] = False
        response["traceback"] = (response.get("traceback") or "") + past
    if request.get("builds") and response.get("ok") and not touched.touched():
        response = {"ok": False, "traceback": (
            "This Step changed nothing in the Document, so it is not a Step. Code "
            "that only looks goes without \"step\"; send the code that builds it.")}
    if response.get("ok"):
        if exploring and touched.touched() and not ephemeral:
            _reload_checkpoint()
            response["rolled_back"] = sorted(touched.created | touched.deleted | touched.modified())
        else:
            if not ephemeral and (touched.touched() or not _checkpoint_exists()):
                _save_checkpoint(doc)
            _SESSION["names"] = names
    else:
        if not ephemeral and touched.touched():
            _reload_checkpoint()
    response["checkpoint"] = _SESSION["checkpoint"]
    response["names"] = dict(_SESSION["names"])
    return response


_ASKED_FOR = {"step": run_step}


def _parent_is_gone(parent):
    """
    Whether *parent* has exited, given a second to be reparented away from.
    A parent that only closed this process's stdin to restart it still owns, and resumes from, the checkpoint.
    """
    deadline = time.monotonic() + 1.0
    while os.getppid() == parent and time.monotonic() < deadline:
        time.sleep(0.05)
    return os.getppid() != parent


def serve(stdin=None, respond=respond):
    """
    Read requests off stdin until it closes, and answer each one.

    What the process will do is the `do` of the request; a request that does
    not say is a Step. Anything Python can still catch comes back as a
    correction, and anything it cannot takes this process down instead of
    FreeCAD.
    """
    _ensure_on_path()
    _isolate_protocol_stream()
    parent = os.getppid()
    _SESSION["scratch"] = os.environ.get(SCRATCH)
    stdin = stdin or sys.stdin
    respond({"ready": True, "pid": os.getpid()})

    try:
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
    finally:
        if _SESSION.get("scratch") and (SCRATCH not in os.environ or _parent_is_gone(parent)):
            shutil.rmtree(_SESSION["scratch"], ignore_errors=True)


if __name__ in ("__main__", "kernel_worker"):
    serve()
