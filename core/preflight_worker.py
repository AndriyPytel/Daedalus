"""
The child half of Preflight. Runs inside a headless FreeCAD started by
`core/preflight.py`; never imported by the addon itself.

One JSON request per line on stdin, one tagged JSON response per line on
stdout. FreeCAD writes its own banner to stdout, so responses carry a
sentinel prefix and the parent ignores everything else.

Death is a normal outcome here. If the code under test takes the C++ core
down, this process goes with it and the parent restarts a new one — which is
the entire point of the arrangement (ADR-0002).
"""
import difflib
import json
import math
import os
import sys
import traceback

SENTINEL = "@@PREFLIGHT@@"

_LIGHT = (0.4, 0.5, 0.76)
_TESSELLATION = 0.2
_BACKGROUND = (250, 250, 252)
_EDGE = (40, 40, 45)
# Edges lie exactly on the faces they bound, so they lose a strict depth test to
# their own surface. The bias is a fraction of the scene's depth range, which
# makes it independent of the part's size.
_EDGE_BIAS = 0.002
# The viewport's default: 45° azimuth, 35.264° elevation — exact isometry.
_AZIMUTH, _ELEVATION = math.radians(45), math.radians(35.264)


def respond(payload):
    sys.stdout.write(SENTINEL + json.dumps(payload) + "\n")
    sys.stdout.flush()


def load_fresh(path):
    """
    Reload the Document copy from disk, every time.

    Mirroring state incrementally would be faster and would eventually
    diverge — at which point the only safety net starts approving code that
    crashes in the live Document (ADR-0002).
    """
    import FreeCAD

    for doc in list(FreeCAD.listDocuments().values()):
        FreeCAD.closeDocument(doc.Name)
    return FreeCAD.openDocument(path) if path else FreeCAD.newDocument("Preflight")


def _view_matrix():
    """The isometric view, as rows: right, up, towards the camera."""
    import numpy as np

    azimuth, elevation = _AZIMUTH, _ELEVATION
    return np.array([
        [math.cos(azimuth), -math.sin(azimuth), 0],
        [math.sin(azimuth) * math.sin(elevation), math.cos(azimuth) * math.sin(elevation),
         math.cos(elevation)],
        [-math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation),
         math.sin(elevation)],
    ])


def _is_modelled(shape):
    """
    True for a shape that is part of the part, rather than of the scaffolding.

    A Body carries an Origin: three `App::Line` axes and three `App::Plane`
    datums, each of them 1e100 across. They have no surface to shade and their
    edges are not edges of anything, but taken literally they would set the
    framing for the whole render and put the part in one pixel. The viewport
    does not draw them either.
    """
    if shape is None or shape.isNull() or not shape.Faces:
        return False
    box = shape.BoundBox
    return box.isValid() and box.DiagonalLength < 1e6


def _tessellated(doc):
    """Every modelled shape's triangles and its real edges, in model space."""
    import numpy as np

    triangles, polylines = [], []
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if not _is_modelled(shape):
            continue
        vertices, facets = shape.tessellate(_TESSELLATION)
        points = np.array([[v.x, v.y, v.z] for v in vertices], dtype=float)
        for facet in facets:
            triangles.append(points[list(facet[:3])])
        for edge in shape.Edges:
            try:
                # A straight edge needs two points; a curve is walked finely
                # enough that the polyline is not the thing you notice.
                along = edge.discretize(Deflection=_TESSELLATION)
            except Exception:
                continue
            if len(along) >= 2:
                polylines.append(np.array([[p.x, p.y, p.z] for p in along], dtype=float))
    return triangles, polylines


def _fill_triangle(colour, zbuffer, xy, z, rgb):
    """
    One flat-shaded triangle, per pixel, against the depth buffer.

    The depth test is what the mean-depth sort could not do: a triangle that
    interpenetrates another is in front over part of itself and behind over the
    rest, and only a per-pixel comparison can say which part is which (FCC-46).
    """
    import numpy as np

    height, width = zbuffer.shape
    left = max(int(math.floor(xy[:, 0].min())), 0)
    right = min(int(math.ceil(xy[:, 0].max())) + 1, width)
    top = max(int(math.floor(xy[:, 1].min())), 0)
    bottom = min(int(math.ceil(xy[:, 1].max())) + 1, height)
    if right <= left or bottom <= top:
        return

    (ax, ay), (bx, by), (cx, cy) = xy
    area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(area) < 1e-12:
        return                      # edge-on: it has no pixels of its own

    px, py = np.meshgrid(np.arange(left, right) + 0.5, np.arange(top, bottom) + 0.5)
    u = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / area
    v = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / area
    w = 1.0 - u - v
    inside = (u >= 0) & (v >= 0) & (w >= 0)
    if not inside.any():
        return

    depth = u * z[0] + v * z[1] + w * z[2]
    window = zbuffer[top:bottom, left:right]
    nearer = inside & (depth > window)
    window[nearer] = depth[nearer]
    colour[top:bottom, left:right][nearer] = rgb


def _draw_edge(colour, zbuffer, points, depths, bias):
    """
    One projected polyline, depth-tested so hidden edges stay hidden.

    Edges are the larger half of why the render did not look like the viewport:
    two adjacent faces of a similar shade merge into one blob without them.
    """
    import numpy as np

    height, width = zbuffer.shape
    longest = 2 * (width + height)
    for start in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[start], points[start + 1]
        z0, z1 = depths[start], depths[start + 1]
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1, z0, z1)):
            continue
        # Capped: a segment that runs far off-screen is sampled for the part
        # that can land somewhere, not along its whole length.
        steps = min(int(max(abs(x1 - x0), abs(y1 - y0))) + 1, longest)
        along = np.linspace(0.0, 1.0, steps * 2 + 2)
        xs = np.rint(x0 + (x1 - x0) * along).astype(int)
        ys = np.rint(y0 + (y1 - y0) * along).astype(int)
        zs = z0 + (z1 - z0) * along

        on_screen = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
        xs, ys, zs = xs[on_screen], ys[on_screen], zs[on_screen]
        if not len(xs):
            continue
        visible = zs + bias >= zbuffer[ys, xs]
        colour[ys[visible], xs[visible]] = _EDGE


def render(doc, path, size=(800, 600)):
    """
    Isometric render of every shape in the Document.

    Software-rasterised on purpose: a headless FreeCAD has no GL context, so
    Coin's offscreen renderer cannot produce a pixel here.

    A per-pixel depth buffer and the shapes' own edges, because Review judges
    by this file and the painter's-algorithm version did not resemble the
    viewport it is compared against (FCC-46). Anti-aliasing, per-object colour
    and perspective are deliberately still absent: Review does not judge by
    those, and the render is not trying to be a screenshot.
    """
    import numpy as np
    from PIL import Image

    triangles, polylines = _tessellated(doc)
    if not triangles:
        return None

    tris = np.array(triangles)
    view = _view_matrix()
    viewed = tris @ view.T
    flat = viewed[:, :, :2]

    width, height = size
    low, high = flat.reshape(-1, 2).min(0), flat.reshape(-1, 2).max(0)
    scale = min(width, height) * 0.82 / max((high - low).max(), 1e-9)
    offset = np.array([width, height]) / 2 - (low + high) / 2 * scale

    def to_pixels(points_2d):
        placed = points_2d * scale + offset
        placed[..., 1] = height - placed[..., 1]
        return placed

    pixels = to_pixels(flat)
    depth = viewed[:, :, 2]

    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1
    shade = np.clip(abs(normals / lengths[:, None] @ np.array(_LIGHT)), 0, 1)

    colour = np.zeros((height, width, 3), dtype=np.uint8)
    colour[:, :] = _BACKGROUND
    zbuffer = np.full((height, width), -np.inf)

    for i in range(len(tris)):
        grey = int(60 + 175 * shade[i])
        _fill_triangle(colour, zbuffer, pixels[i], depth[i],
                       (grey, grey, min(255, int(grey * 1.05))))

    span = float(depth.max() - depth.min()) or 1.0
    bias = span * _EDGE_BIAS
    for polyline in polylines:
        seen = polyline @ view.T
        _draw_edge(colour, zbuffer, to_pixels(seen[:, :2]), seen[:, 2], bias)

    Image.fromarray(colour, "RGB").save(path)
    return path


def summarise(doc):
    return [
        {"name": o.Name, "label": o.Label, "type": o.TypeId}
        for o in doc.Objects
    ]


# Derived by recompute rather than assigned by the Step. An object whose only
# changed property is one of these was recalculated, not modified — otherwise
# every dependent object downstream of a legitimate change would look touched.
# ponytail: one property; widen if a cascade still trips the Owned Object rule.
DERIVED = {"Shape"}


class _Touched:
    """What the Step did, straight from FreeCAD rather than from reading the code."""

    def __init__(self):
        self.created = set()
        self.deleted = set()
        self.changed = set()     # (name, property)

    def slotCreatedObject(self, obj):
        self.created.add(obj.Name)

    def slotDeletedObject(self, obj):
        self.deleted.add(obj.Name)

    def slotChangedObject(self, obj, prop):
        self.changed.add((obj.Name, prop))

    def modified(self):
        names = set()
        for name in {name for name, _ in self.changed}:
            if name in self.created or name in self.deleted:
                continue
            properties = {prop for changed, prop in self.changed if changed == name}
            if properties - DERIVED:
                names.add(name)
        return names


_HINT_LIMIT = 60

# Sketch geometry is spelled Part.Circle / Part.LineSegment in every version of
# FreeCAD; the Sketcher module holds none of it. Leaving these out of the
# namespace turned correct code into a NameError, which the assistant read as
# "Part does not exist here" and answered by inventing Sketcher.Circle.
#
# Materials is in for the same reason: ShapeMaterial accepts only a
# Materials.Material, and the one source of those is Materials.MaterialManager
# (FCC-38). Left out, the correct code is a NameError and the assistant answers
# by inventing an App.Material that has no Name or Density.
STEP_MODULES = ("Part", "Sketcher", "PartDesign", "Materials")


def step_namespace(doc):
    """
    What a Step's code runs with.

    Preflight and Apply must build this identically or they are running
    different programs and Preflight stops meaning anything (ADR-0002), so both
    call this rather than each spelling it out.
    """
    import FreeCAD

    namespace = {"App": FreeCAD, "FreeCAD": FreeCAD, "doc": doc, "__name__": "__step__"}
    for module in STEP_MODULES:
        try:
            namespace[module] = __import__(module)
        except ImportError:
            pass
    return namespace


def _name_of(obj):
    """
    What to call the thing in the hint.

    TypeId is the useful name for a DocumentObject, but on a class it is an
    unbound descriptor whose repr — <attribute 'TypeId' of 'Base.BaseClass'
    objects> — named nothing the assistant could act on.
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
    here as everywhere (ADR-0006) — this just applies it at the moment the
    guess fails, rather than hoping the assistant asks first (ADR-0008).
    """
    obj, name = getattr(exc, "obj", None), getattr(exc, "name", None)
    if obj is None or not name:
        return ""
    try:
        public = sorted(a for a in dir(obj) if not a.startswith("_"))
        # PropertiesList is the sharp answer for a DocumentObject; dir() is 80
        # names of mostly plumbing. On a module or a class it is the unbound
        # descriptor, which is neither — hence the isinstance, not a truth test.
        declared = getattr(obj, "PropertiesList", None)
        properties = sorted(declared) if isinstance(declared, (list, tuple)) else public
        hint = "\n%s has no %r." % (_name_of(obj), name)
        close = difflib.get_close_matches(name, public, n=3)
        if close:
            hint += " Closest existing: %s." % ", ".join(close)
        return hint + "\nIt has: %s" % ", ".join(properties[:_HINT_LIMIT])
    except BaseException:
        # A hint is never worth losing the traceback it was meant to explain.
        return ""


def _bodies_in_pieces(doc):
    """
    Every PartDesign Body whose result is more than one solid (FCC-27).

    A sketch of disjoint profiles pads into separate lumps, and nothing raises:
    the feature reports itself up-to-date and valid, so Preflight passed it and
    the only phase that objected was Review, at the end, from a render.

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


def _as_written(value):
    """A property value in the spelling Plan would have used for it."""
    return getattr(value, "Value", value)    # a Quantity: 10.0 mm -> 10.0


# A written number is a rounding of the one Execute typed: 13/√3 is
# 7.505553499… in the code and 7.505553 in the Plan, and those are the same
# number. Relative, because the decimals Plan writes scale with the size.
_TOLERANCE = 1e-6

# Plan means the same thing by either, and `Name` is not a property at all, so
# a Plan that wrote it was refused as if it had invented one (FCC-71).
NAMING = ("Name", "Label")


def _agrees(got, want):
    """
    Whether what the Step built agrees with what the Plan wrote for it.

    Silence is the default, and that is the decision. Plan owns the operation,
    the names and the declared numbers; the binding — a link, a support list, a
    placement — is worked out where the code is written (ADR-0005), and those
    arrive here as objects and lists of tuples whose `str()` matches nothing
    Plan could have written. Comparing them refused every Step that attached a
    sketch, for building exactly what it was told to (FCC-71).
    """
    got = _as_written(got)
    if isinstance(got, bool) or isinstance(want, bool):
        return bool(got) is bool(want)
    if isinstance(got, (int, float)):
        return (isinstance(want, (int, float))
                and abs(got - want) <= max(abs(want) * _TOLERANCE, _TOLERANCE))
    if isinstance(got, str):
        return got == str(want)
    return True


def wrong_parameters(parameters, doc, created, modified=()):
    """
    Where what the Step built disagrees with the operation the Plan named.

    Plan writes the operation and its declared numbers for every Step; Execute
    writes the code. Checking one against the other holds on an involute flank
    as much as on a cylinder — which measuring the result never did, because
    nobody works out the volume of a tooth in advance.

    A `type` without `::` is not a document object at all — the geometry inside
    a sketch is spelled `Part.Circle` and creates nothing to look at. Such a
    Step is left to the floor ADR-0010 and FCC-27 already provide.
    """
    named = str(parameters.get("type") or "")
    if "::" not in named:
        return []

    # Created first, because most Steps create. A Step that edits an existing
    # feature — a Pad whose Length changes — touches one instead.
    touched = [doc.getObject(name) for name in list(created) + list(modified)]
    touched = [obj for obj in touched if obj is not None]
    matching = [obj for obj in touched if obj.TypeId == named]
    if not matching:
        did = ", ".join(sorted({obj.TypeId for obj in touched})) or "nothing"
        return ["type: the Plan named %s, the Step built %s" % (named, did)]

    wrong = []
    for obj in matching:
        for prop, want in (parameters.get("properties") or {}).items():
            if prop in NAMING:
                if str(want) not in (obj.Name, obj.Label):
                    wrong.append("%s: the Plan named %s, the Step built %s"
                                 % (prop, want, obj.Label))
            elif prop not in obj.PropertiesList:
                wrong.append("%s: the Plan named it, and %s has no such property"
                             % (prop, named))
            elif not _agrees(got := getattr(obj, prop), want):
                wrong.append("%s: the Plan named %s, the Step built %s"
                             % (prop, want, _as_written(got)))
    return wrong


def preflight(request):
    import FreeCAD

    doc = load_fresh(request.get("document"))
    namespace = step_namespace(doc)
    parameters = request.get("parameters") or {}

    touched = _Touched()
    FreeCAD.addDocumentObserver(touched)
    try:
        exec(compile(request["code"], "<step>", "exec"), namespace)  # noqa: S102
    finally:
        FreeCAD.removeDocumentObserver(touched)
    doc.recompute()

    failures = ["%s (%s): %s" % (o.Name, o.TypeId, o.getStatusString())
                for o in doc.Objects if o.State and "Invalid" in o.State]
    if failures:
        raise RuntimeError("recompute left these objects in error:\n" + "\n".join(failures))

    scattered = _bodies_in_pieces(doc)
    if scattered:
        raise RuntimeError(
            "this Step left a Body in more than one piece:\n" + "\n".join(scattered)
            + "\nA PartDesign Body is one solid. Disjoint profiles in one sketch pad into "
              "separate lumps rather than one part — the profiles that make up the outline "
              "have to meet, and anything that is genuinely a second part belongs in a "
              "second Body.")

    wrong = wrong_parameters(parameters, doc, touched.created, touched.modified())
    if wrong:
        raise RuntimeError(
            "this Step did not build the operation the Plan named:\n" + "\n".join(wrong)
            + "\nThe Plan names the operation and its numbers, and this code built "
              "something else. Correct the code against the Plan's numbers rather than "
              "sending it again.")

    result = {
        "ok": True,
        "objects": summarise(doc),
        "created": sorted(touched.created),
        "deleted": sorted(touched.deleted),
        "modified": sorted(touched.modified()),
    }
    render_path = request.get("render_path")
    if render_path:
        result["render"] = render(doc, render_path)
    return result


def serve(stdin=None, respond=respond):
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
        if request.get("shutdown"):
            return
        try:
            respond(preflight(request))
        except BaseException as exc:
            # Anything Python can still catch comes back as a correction.
            # Anything it cannot takes this process down instead of FreeCAD.
            respond({"ok": False, "traceback": traceback.format_exc() + introspect(exc)})


# freecadcmd runs a script with __name__ set to its basename, not "__main__".
# Importing this module as core.preflight_worker (for SENTINEL) must not serve.
if __name__ in ("__main__", "preflight_worker"):
    serve()
