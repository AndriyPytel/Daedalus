"""
What a Step's code sees and what Apply replays: the helper functions, the namespace that holds them, and the signature a replay must reproduce. The kernel's worker and the GUI's `run_in_transaction` both import it, so a Step runs the same program in either. `HELPERS` is the one list the namespace is built from.
"""
import linecache
import sys
import traceback

from core import nativeifc


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


def group_edges_by_sign(shape, names):
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
    by_sign = group_edges_by_sign(shape, edges)
    return [names for _, names in sorted(by_sign.items(), reverse=True)]


HELPERS = (built_ok, edges_between, edges_by_concavity, pick_edges, pick_faces, plane_face,
           cylinder_face, bbox, by_label, sketch_at)


def step_namespace(doc):
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

    sys.modules["App"] = FreeCAD

    namespace = {"App": FreeCAD, "FreeCAD": FreeCAD, "doc": doc, "__name__": "__step__",
                 **{helper.__name__: helper for helper in HELPERS}}
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


def volume_of(obj):
    """
    A feature's own material, zero for anything that is not a closed solid.

    A datum plane's `Shape` has no `Solids` — OCC's volume on an unbounded
    Face is numerically unstable rather than a clean zero.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.Solids:
        return 0.0
    return shape.Volume


def volume_moved(doc, created):
    """
    How much material each PartDesign feature this Step created actually moved.
    Every Domain's Step result carries it, and the Part rules' volume-neutral check reads the same numbers.
    """
    moved = {}
    for name in sorted(created):
        obj = doc.getObject(name)
        if obj is None or not obj.isDerivedFrom("PartDesign::Feature"):
            continue
        base = getattr(obj, "BaseFeature", None)
        before = volume_of(base) if base is not None else 0.0
        now = volume_of(obj)
        moved[name] = {"before": round(before, 6), "after": round(now, 6),
                       "moved": round(now - before, 6)}
    return moved


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
    signature = {
        "count": len(doc.Objects),
        "names": sorted(o.Name for o in doc.Objects),
        "volume": round(sum(volume_of(o) for o in doc.Objects if not nativeifc.is_native(o)), 6),
        "links": links,
    }
    ifc = nativeifc.outlines(doc)
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
