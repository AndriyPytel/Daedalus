"""
The child half of Preflight. Runs inside a headless FreeCAD started by
`core/preflight.py`, as a bare script — so it may import the standard library
and FreeCAD, and nothing of the addon's own.

Two things are imported *out* of it, by name, and both deliberately: `SENTINEL`
by `core/preflight.py`, and `step_namespace` by `core/apply.py`, so that
Preflight and Apply cannot build different namespaces for the same Step.

One thing is imported *in*, by `reading` alone and for the same reason in the
other direction: `core.sketch_check`, so that the check and its tests cannot
build different sketches out of the same reading. It costs a `sys.path` entry
and it buys the one thing this process exists for — the solver runs where its
death is survivable (FCC-126).

One JSON request per line on stdin, one tagged JSON response per line on
stdout. FreeCAD writes its own banner to stdout, so responses carry a
sentinel prefix and the parent ignores everything else. What a reply means is
read in exactly one place per kind of request: `PreflightResult.from_response`
for a Step, `sketch_check.from_response` for a reading.

Death is a normal outcome here. If the code under test takes the C++ core
down, this process goes with it and the parent restarts a new one — which is
the entire point of the arrangement (ADR-0002). That is also why there is no
shutdown message: the parent kills the process rather than asking it to go.

`freecadcmd` runs a script with `__name__` set to its basename rather than
"__main__", and importing this module as `core.preflight_worker` for its
`SENTINEL` must not start serving, so both names are named at the foot.
"""
import difflib
import json
import linecache
import math
import os
import sys
import traceback

SENTINEL = "@@PREFLIGHT@@"

_LIGHT = (0.4, 0.5, 0.76)
_TESSELLATION = 0.2
_BACKGROUND = (250, 250, 252)
_EDGE = (40, 40, 45)
_EDGE_BIAS = 0.002
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
    """
    The isometric view, as rows: right, up, towards the camera.

    45° azimuth and 35.264° elevation, which is the viewport's default and an
    exact isometry.
    """
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
    """
    Every modelled shape's triangles and its real edges, in model space.

    A straight edge needs two points; a curve is walked finely enough that the
    polyline is not the thing you notice.
    """
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

    A triangle of no area is edge-on and has no pixels of its own.
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
        return

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

    An edge lies exactly on the faces it bounds, so it loses a strict depth
    test to its own surface; the bias against that is a fraction of the scene's
    depth range, which makes it independent of the part's size. The sampling is
    capped, so that a segment running far off-screen is sampled for the part
    that can land somewhere rather than along its whole length.
    """
    import numpy as np

    height, width = zbuffer.shape
    longest = 2 * (width + height)
    for start in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[start], points[start + 1]
        z0, z1 = depths[start], depths[start + 1]
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1, z0, z1)):
            continue
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


DERIVED = {"Shape"}


class _Touched:
    """
    What the Step did, straight from FreeCAD rather than from reading the code.

    `changed` holds one (name, property) pair per notification. The properties
    in `DERIVED` are derived by recompute rather than assigned by the Step, so
    an object whose only changed property is one of those was recalculated
    rather than modified — counting them would make every dependent object
    downstream of a legitimate change look touched.
    """

    def __init__(self):
        self.created = set()
        self.deleted = set()
        self.changed = set()

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

STEP_MODULES = ("Part", "Sketcher", "PartDesign", "Materials")


def step_namespace(doc):
    """
    What a Step's code runs with.

    Preflight and Apply must build this identically or they are running
    different programs and Preflight stops meaning anything (ADR-0002), so both
    call this rather than each spelling it out.

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


_A_DRESSUP = {"PartDesign::Fillet": ("Radius", "makeFillet"),
              "PartDesign::Chamfer": ("Size", "makeChamfer")}

_SMALLER = (0.75, 0.5, 0.25)


def _why_it_failed(obj):
    """What the Document says about a feature in error, and what it will not."""
    return ". ".join(said for said in (obj.getStatusString(),
                                       _why_a_dressup_failed(obj)) if said)


def _why_a_dressup_failed(obj):
    """
    What a fillet or a chamfer will not say for itself.

    A PartDesign dressup OCC refuses says either nothing at all —
    `getStatusString()` is the empty string, and `Fillet_R2
    (PartDesign::Fillet): ` was the whole of the failure Execute was handed
    (`logs/sessions/069d21dbac10416392533fe7c06b62d4.jsonl`) — or `BRep_API:
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


def _volume_of(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return 0.0
    return shape.Volume


def _features_that_moved_no_material(doc, created):
    """
    Features this Step created that came out the same volume they went in
    (FCC-96).

    The silent failure this exists for: a Pocket sketched on the face at YMin
    with its normal pointing into the part cuts along -Y, away from the solid,
    intersects nothing, and hands back its base shape untouched. FreeCAD calls
    it 'Up-to-date' and 'Valid', Preflight passed it on that, and five bores in
    a row went missing from a part every phase reported as applied.

    Read off `BaseFeature` rather than off the Body, because that is the one
    comparison that names the feature at fault: a feature's base is exactly the
    solid it was handed, so the difference is exactly what it did, whatever
    else the same Step built around it.

    A feature is a step the user would recognise as cutting or adding metal; a
    Sketch, a datum or a Body itself is not, and neither is anything outside
    PartDesign.

    Returns the idle features themselves, not formatted text (FCC-246): the
    caller diagnoses each one before it has anything to print, and this
    function's own comparison — the one line FCC-246's AC 4 leaves alone —
    stays exactly what it was.
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
    The assertion.md script's own `held` and `failed` lists, read back out of
    the namespace it ran in rather than parsed from a traceback string.

    `None`, not `[]`, for a name the script never defined — a Step's code has
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
    top, so only the Step's frames — from `"<step>"` down — and the standard
    call into `preflight()` or `run_in_transaction()` above it reach the reader.

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
    What `preflight()`'s own recompute should touch: this Step's own writes,
    resolved back to live objects, plus everything downstream of them.

    `doc.recompute(objs)` recomputes exactly *objs* and their own
    dependencies -- it does not walk `InList` the way the argument-free form
    does, so a Step that edits an existing feature rather than only
    appending a new one at the tip would leave every feature built on it
    `Touched` with a stale `Shape`, not `Invalid`: the failure scan right
    after (`"Invalid" in o.State`) would not see it, and Preflight would pass
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


def preflight(request):
    import FreeCAD

    doc = load_fresh(request.get("document"))
    namespace = step_namespace(doc)

    touched = _Touched()
    FreeCAD.addDocumentObserver(touched)
    try:
        try:
            register_step_source(request["code"])
            exec(compile(request["code"], "<step>", "exec"), namespace)  # noqa: S102
        except AssertionError as exc:
            response = {"ok": False, "traceback": trimmed_traceback(exc)}
            held, failed = _measured(namespace)
            if held is not None:
                response["held"] = held
            if failed is not None:
                response["failed"] = failed
            return response
    finally:
        FreeCAD.removeDocumentObserver(touched)
    scope = _recompute_scope(doc, touched)
    if scope is None:
        doc.recompute()
    else:
        doc.recompute(scope)

    failures = ["%s (%s): %s" % (o.Name, o.TypeId, _why_it_failed(o))
                for o in doc.Objects if o.State and "Invalid" in o.State]
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
              "found it did not happen, and FreeCAD reports it up-to-date and valid all the "
              "same: a Pocket whose profile is extruded away from the solid cuts nothing."
        )

    scattered = _bodies_in_pieces(doc)
    if scattered:
        raise RuntimeError(
            "this Step left a Body in more than one piece:\n" + "\n".join(scattered)
            + "\nA PartDesign Body is one solid. Disjoint profiles in one sketch pad into "
              "separate lumps rather than one part — the profiles that make up the outline "
              "have to meet, and anything that is genuinely a second part belongs in a "
              "second Body.")

    result = {
        "ok": True,
        "objects": summarise(doc),
        "created": sorted(touched.created),
        "deleted": sorted(touched.deleted),
        "modified": sorted(touched.modified()),
    }
    held, failed = _measured(namespace)
    if held is not None:
        result["held"] = held
    if failed is not None:
        result["failed"] = failed
    render_path = request.get("render_path")
    if render_path:
        result["render"] = render(doc, render_path)
    return result


def reading(request):
    """
    One outline off a drawing, built as a sketch and put to the solver.

    Here rather than in the addon's process because this is the process whose
    death is a normal outcome: the phases that read a sheet run on the loop's
    worker thread, and `Sketcher.Constraint` is the call that segfaults
    (FCC-19, FCC-126).
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from core import sketch_check

    outline, view, between = sketch_check.from_request(request)
    answer = {"ok": True}
    answer.update(sketch_check.as_response(sketch_check.checked(outline, view, between)))
    return answer


_ASKED_FOR = {"step": preflight, "reading": reading}


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


if __name__ in ("__main__", "preflight_worker"):
    serve()
