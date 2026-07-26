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
import json
import math
import os
import sys
import traceback

SENTINEL = "@@PREFLIGHT@@"

_LIGHT = (0.4, 0.5, 0.76)
_TESSELLATION = 0.2


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


def render(doc, path, size=(800, 600)):
    """
    Isometric render of every shape in the Document.

    Software-rasterised on purpose: a headless FreeCAD has no GL context, so
    Coin's offscreen renderer cannot produce a pixel here. Painter's algorithm
    over flat-shaded tessellated triangles is enough for Review to judge by.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    triangles = []
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        vertices, facets = shape.tessellate(_TESSELLATION)
        points = np.array([[v.x, v.y, v.z] for v in vertices], dtype=float)
        for facet in facets:
            triangles.append(points[list(facet[:3])])

    if not triangles:
        return None

    tris = np.array(triangles)

    azimuth, elevation = math.radians(45), math.radians(35.264)
    view = np.array([
        [math.cos(azimuth), -math.sin(azimuth), 0],
        [math.sin(azimuth) * math.sin(elevation), math.cos(azimuth) * math.sin(elevation), math.cos(elevation)],
        [-math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation), math.sin(elevation)],
    ])
    viewed = tris @ view.T
    flat, depth = viewed[:, :, :2], viewed[:, :, 2].mean(axis=1)

    width, height = size
    low, high = flat.reshape(-1, 2).min(0), flat.reshape(-1, 2).max(0)
    scale = min(width, height) * 0.82 / max((high - low).max(), 1e-9)
    pixels = flat * scale + (np.array([width, height]) / 2 - (low + high) / 2 * scale)
    pixels[:, :, 1] = height - pixels[:, :, 1]

    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1
    shade = np.clip(abs(normals / lengths[:, None] @ np.array(_LIGHT)), 0, 1)

    image = Image.new("RGB", size, (250, 250, 252))
    draw = ImageDraw.Draw(image)
    for i in np.argsort(depth):
        grey = int(60 + 175 * shade[i])
        draw.polygon([tuple(p) for p in pixels[i]], fill=(grey, grey, min(255, int(grey * 1.05))))
    image.save(path)
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


def preflight(request):
    import FreeCAD

    doc = load_fresh(request.get("document"))
    namespace = {"App": FreeCAD, "FreeCAD": FreeCAD, "doc": doc, "__name__": "__preflight__"}

    touched = _Touched()
    FreeCAD.addDocumentObserver(touched)
    try:
        exec(compile(request["code"], "<step>", "exec"), namespace)  # noqa: S102
    finally:
        FreeCAD.removeDocumentObserver(touched)
    doc.recompute()

    failures = [o.Name for o in doc.Objects if o.State and "Invalid" in o.State]
    if failures:
        raise RuntimeError("recompute left these objects in error: " + ", ".join(failures))

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
    if request.get("save_to"):
        doc.saveAs(request["save_to"])
        result["saved"] = request["save_to"]
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
        except BaseException:
            # Anything Python can still catch comes back as a correction.
            # Anything it cannot takes this process down instead of FreeCAD.
            respond({"ok": False, "traceback": traceback.format_exc()})


# freecadcmd runs a script with __name__ set to its basename, not "__main__".
# Importing this module as core.preflight_worker (for SENTINEL) must not serve.
if __name__ in ("__main__", "preflight_worker"):
    serve()
