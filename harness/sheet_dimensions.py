"""
Dimensions of one Fusion 360 Gallery design, read from its timeline JSON and
filled in from its STEP. Everything here is in mm and in world coordinates; a
sketch point (x, y) is origin + x * x_axis + y * y_axis, and the gallery's cm
values are scaled by 10. The views are the first-angle Front (looks along Y,
shows X and Z), Top (looks along Z, shows X and Y) and Left (looks along X,
shows Y and Z).
"""
from dataclasses import asdict, dataclass

import numpy as np

CM_TO_MM = 10.0
FACE_ON_VIEW = {0: "Left", 1: "Front", 2: "Top"}
DEPTH_VIEW = {0: "Front", 1: "Top", 2: "Front"}
SAME_POSITION_MM = 0.01
DIMENSION_TYPES = ("SketchLinearDimension", "SketchDiameterDimension", "SketchRadialDimension")


@dataclass
class Dim:
    feature: str
    source: str
    kind: str
    value_mm: float
    view: str
    p1: tuple
    p2: tuple

    def to_dict(self):
        return asdict(self)


def _vec(v):
    return np.array([v["x"], v["y"], v["z"]], dtype=float)


def _world_axis(vector):
    """Index of the world axis `vector` is parallel to, or None."""
    for axis in range(3):
        if abs(vector[axis]) > 0.999:
            return axis
    return None


def sketch_axis(sketch):
    return _world_axis(_vec(sketch["transform"]["z_axis"]))


def _frame(sketch):
    t = sketch["transform"]
    return _vec(t["origin"]), _vec(t["x_axis"]), _vec(t["y_axis"])


def _to_world(sketch, x, y):
    origin, ux, uy = _frame(sketch)
    return (origin + x * ux + y * uy) * CM_TO_MM


def _point(sketch, point_id):
    p = sketch["points"][point_id]
    return _to_world(sketch, p["x"], p["y"])


def _tuple(v):
    return tuple(float(c) for c in v)


def _sketch_dims(sketch):
    view = FACE_ON_VIEW[sketch_axis(sketch)]
    origin, ux, uy = _frame(sketch)
    for dim in sketch.get("dimensions", {}).values():
        kind = dim["type"]
        if kind not in DIMENSION_TYPES or not dim.get("is_driving", True):
            continue
        value = dim["parameter"]["value"] * CM_TO_MM
        if kind == "SketchLinearDimension":
            a = _point(sketch, dim["entity_one"])
            b = _point(sketch, dim["entity_two"])
            orientation = dim["orientation"]
            if orientation.startswith("Horizontal"):
                b = a + _vec_unit(b - a, ux) * value
            elif orientation.startswith("Vertical"):
                b = a + _vec_unit(b - a, uy) * value
            yield "linear", value, view, a, b
            continue
        curve = sketch["curves"][dim["curve"]]
        center = _point(sketch, curve["center_point"])
        radius = curve["radius"] * CM_TO_MM
        text = dim["text_position"]
        toward = text["x"] * ux + text["y"] * uy
        toward = toward / np.linalg.norm(toward) if np.linalg.norm(toward) > 1e-9 else ux
        if kind == "SketchDiameterDimension":
            yield "diameter", value, view, center - toward * radius, center + toward * radius
        else:
            yield "radius", value, view, center, center + toward * radius


def _vec_unit(delta, axis):
    return axis * np.sign(np.dot(delta, axis) or 1.0)


def _depth_dim(sketch, extrude):
    axis = sketch_axis(sketch)
    normal = _vec(sketch["transform"]["z_axis"])
    origin = _vec(sketch["transform"]["origin"]) * CM_TO_MM
    extent = extrude["extent_one"]
    value = extent["distance"]["value"] * CM_TO_MM
    start = origin
    if extrude["extent_type"] == "SymmetricFeatureExtentType":
        start = origin - normal * value / 2
    points = np.array([_to_world(sketch, p["x"], p["y"]) for p in sketch["points"].values()])
    beside = [a for a in range(3) if a != axis and a != _view_axis(DEPTH_VIEW[axis])]
    offset = np.zeros(3)
    for a in beside:
        offset[a] = points[:, a].max() - start[a]
    return "depth", value, DEPTH_VIEW[axis], start + offset, start + offset + normal * value


def _view_axis(view):
    return {"Left": 0, "Front": 1, "Top": 2}[view]


def timeline_dimensions(source):
    entities = source["entities"]
    dims = []
    for step in source["timeline"]:
        entity = entities[step["entity"]]
        if entity["type"] == "Sketch" and sketch_axis(entity) is not None:
            for kind, value, view, p1, p2 in _sketch_dims(entity):
                dims.append(Dim(entity["name"], "timeline", kind, value, view, _tuple(p1), _tuple(p2)))
        elif entity["type"] == "ExtrudeFeature":
            sketch = entities[entity["profiles"][0]["sketch"]]
            kind, value, view, p1, p2 = _depth_dim(sketch, entity)
            dims.append(Dim(entity["name"], "timeline", kind, value, view, _tuple(p1), _tuple(p2)))
    return dims


def envelope_axis(dim):
    return int(np.argmax(np.abs(np.array(dim.p2) - np.array(dim.p1))))


def missing_features(source, dims):
    have = {d.feature for d in dims if d.source == "timeline"}
    missing = [
        source["entities"][step["entity"]]["name"]
        for step in source["timeline"]
        if source["entities"][step["entity"]]["name"] not in have
    ]
    if {envelope_axis(d) for d in dims if d.kind == "envelope"} != {0, 1, 2}:
        missing.append("envelope")
    return missing


def _bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return np.array(box.Get(), dtype=float)


def _holes(shape):
    """Full-circle cylindrical holes parallel to a world axis: (axis, point, radius)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    spans = {}
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        explorer.Next()
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        direction = cylinder.Axis().Direction()
        axis = _world_axis(np.array([direction.X(), direction.Y(), direction.Z()]))
        if axis is None:
            continue
        inner = face.Orientation() == TopAbs_REVERSED
        if not inner:
            continue
        location = cylinder.Location()
        point = np.array([location.X(), location.Y(), location.Z()])
        point[axis] = 0.0
        key = (axis, round(cylinder.Radius(), 3), tuple(np.round(point, 2)))
        spans[key] = spans.get(key, 0.0) + surface.LastUParameter() - surface.FirstUParameter()
    return [(k[0], np.array(k[2]), k[1]) for k, span in spans.items() if span > 2 * np.pi - 1e-3]


def step_dimensions(shape, have):
    x0, y0, z0, x1, y1, z1 = _bounds(shape)
    low, high = np.array([x0, y0, z0]), np.array([x1, y1, z1])
    dims = []
    for axis, view in ((0, "Front"), (2, "Front"), (1, "Top")):
        p2 = low.copy()
        p2[axis] = high[axis]
        dims.append(Dim("envelope", "step", "envelope", float(high[axis] - low[axis]), view, _tuple(low), _tuple(p2)))
    covered = [
        (d.value_mm, (np.array(d.p1) + np.array(d.p2)) / 2)
        for d in have
        if d.kind == "diameter"
    ]
    for axis, point, radius in _holes(shape):
        keep = [a for a in range(3) if a != axis]
        if any(
            abs(value - 2 * radius) < SAME_POSITION_MM
            and np.linalg.norm((center - point)[keep]) < SAME_POSITION_MM
            for value, center in covered
        ):
            continue
        view = FACE_ON_VIEW[axis]
        center = point.copy()
        center[axis] = (low[axis] + high[axis]) / 2
        name = f"hole {2 * radius:g} at {point[keep[0]]:g},{point[keep[1]]:g}"
        first = keep[0]
        edge = center.copy()
        edge[first] += radius
        dims.append(Dim(name, "step", "diameter", 2 * radius, view, _tuple(2 * center - edge), _tuple(edge)))
        for a in keep:
            start = center.copy()
            start[a] = low[a]
            dims.append(Dim(name, "step", "linear", float(center[a] - low[a]), view, _tuple(start), _tuple(center)))
    return dims
