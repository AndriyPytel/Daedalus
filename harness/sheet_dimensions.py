"""
Dimensions of a solid read from its faces alone, in mm and world coordinates. The views are the
first-angle Front (looks along Y, shows X and Z), Top (looks along Z, shows X and Y) and Left
(looks along X, shows Y and Z). Planes and hole centres become chained levels per axis from the
bounding-box datum, equal features within 0.01 mm become one counted dimension, and B-spline
faces carry none.
"""
import math
from dataclasses import asdict, dataclass

import numpy as np

SAME_MM = 0.01
FACE_ON_VIEW = {0: "Left", 1: "Front", 2: "Top"}
LEVEL_VIEW = {0: "Front", 1: "Top", 2: "Front"}
VIEW_DIRECTION = {"Left": 0, "Front": 1, "Top": 2}
CHAMFER_ANGLE = 45.0
ANGLE_TOLERANCE = 0.5
CYLINDER, CONE, TORUS, SPHERE, PLANE, BSPLINE = "cylinder", "cone", "torus", "sphere", "plane", "bspline"


@dataclass
class Dim:
    feature: str
    kind: str
    value: float
    view: str
    p1: tuple
    p2: tuple
    count: int = 1
    axis: int = -1
    source: str = "step"

    def to_dict(self):
        return asdict(self)


def _axis_index(vector):
    for axis in range(3):
        if abs(vector[axis]) > 0.9999:
            return axis
    return None


def _unit(vector):
    vector = np.asarray(vector, dtype=float)
    return vector / np.linalg.norm(vector)


def _direction_key(vector):
    vector = _unit(vector)
    first = next(c for c in vector if abs(c) > 1e-6)
    return tuple(np.round(vector * (1 if first > 0 else -1), 3) + 0.0)


def bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    values = np.array(box.Get(), dtype=float)
    return values[:3], values[3:]


def _view_for_axis(vector):
    return FACE_ON_VIEW[int(np.argmax(np.abs(vector)))]


def _faces(shape):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    types = {GeomAbs_Plane: PLANE, GeomAbs_Cylinder: CYLINDER, GeomAbs_Cone: CONE, GeomAbs_Torus: TORUS, GeomAbs_Sphere: SPHERE}
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    for i in range(1, faces.Extent() + 1):
        face = TopoDS.Face_s(faces.FindKey(i))
        surface = BRepAdaptor_Surface(face)
        yield types.get(surface.GetType(), BSPLINE), surface, face.Orientation() == TopAbs_REVERSED


def _xyz(p):
    return np.array([p.X(), p.Y(), p.Z()])


def _facts(shape):
    """Every non-B-spline face as a plain record: type, direction, and the numbers its dimension needs."""
    records, bspline = [], 0
    total = 0
    for kind, surface, _ in _faces(shape):
        total += 1
        u0, u1, v0, v1 = surface.FirstUParameter(), surface.LastUParameter(), surface.FirstVParameter(), surface.LastVParameter()
        if kind == BSPLINE:
            bspline += 1
            continue
        if kind == PLANE:
            plane = surface.Plane()
            normal = _xyz(plane.Axis().Direction())
            records.append({"type": PLANE, "normal": normal, "offset": float(normal @ _xyz(plane.Location()))})
        elif kind == CYLINDER:
            cylinder = surface.Cylinder()
            records.append({
                "type": CYLINDER, "axis": _xyz(cylinder.Axis().Direction()), "origin": _xyz(cylinder.Location()),
                "radius": cylinder.Radius(), "span": u1 - u0, "point": _xyz(surface.Value((u0 + u1) / 2, (v0 + v1) / 2)),
            })
        elif kind == CONE:
            cone = surface.Cone()
            semi = abs(cone.SemiAngle())
            r0 = cone.RefRadius() + v0 * math.sin(cone.SemiAngle())
            r1 = cone.RefRadius() + v1 * math.sin(cone.SemiAngle())
            records.append({
                "type": CONE, "axis": _xyz(cone.Axis().Direction()), "origin": _xyz(cone.Location()),
                "half_angle": math.degrees(semi), "height": abs(v1 - v0) * math.cos(semi),
                "radius": max(abs(r0), abs(r1)), "point": _xyz(surface.Value((u0 + u1) / 2, (v0 + v1) / 2)),
            })
        elif kind == TORUS:
            torus = surface.Torus()
            records.append({
                "type": TORUS, "axis": _xyz(torus.Axis().Direction()), "origin": _xyz(torus.Location()),
                "radius": torus.MinorRadius(), "point": _xyz(surface.Value((u0 + u1) / 2, (v0 + v1) / 2)),
            })
        else:
            sphere = surface.Sphere()
            records.append({
                "type": SPHERE, "axis": np.array([0.0, 0.0, 1.0]), "origin": _xyz(sphere.Location()),
                "radius": sphere.Radius(), "point": _xyz(surface.Value((u0 + u1) / 2, (v0 + v1) / 2)),
            })
    return records, bspline, total


def bspline_share(shape):
    records, bspline, total = _facts(shape)
    return bspline / total if total else 0.0


def _merge_cylinders(records):
    """Cylinder faces of one axis, radius and centre line add up to one circle or arc."""
    merged = {}
    for r in records:
        if r["type"] != CYLINDER:
            continue
        axis = r["axis"]
        along = r["origin"] - axis * (r["origin"] @ axis)
        key = (_direction_key(axis), round(r["radius"], 2), tuple(np.round(along, 1)))
        entry = merged.setdefault(key, dict(r, span=0.0))
        entry["span"] += r["span"]
    return list(merged.values())


def _groups(items):
    """Cluster (value, payload) pairs whose values lie within SAME_MM of the previous one."""
    groups = []
    for value, payload in sorted(items, key=lambda i: i[0]):
        if groups and value - groups[-1][-1][0] < SAME_MM + 1e-9:
            groups[-1].append((value, payload))
        else:
            groups.append([(value, payload)])
    return groups


def _round_features(records, low, high):
    """Counted groups per (kind, value): name, kind, value, view, payload list."""
    mid = (low + high) / 2
    items = {"diameter": [], "radius": [], "chamfer": [], "angle": []}
    for c in _merge_cylinders(records):
        full = c["span"] > 2 * math.pi - 1e-3
        items["diameter" if full else "radius"].append((2 * c["radius"] if full else c["radius"], c))
    for r in records:
        if r["type"] in (TORUS, SPHERE):
            items["radius"].append((r["radius"], r))
        elif r["type"] == CONE:
            if abs(r["half_angle"] - CHAMFER_ANGLE) < ANGLE_TOLERANCE:
                items["chamfer"].append((r["height"], r))
            else:
                items["angle"].append((r["half_angle"], r))
                items["diameter"].append((2 * r["radius"], r))
    dims = []
    for kind, pairs in items.items():
        for group in _groups(pairs):
            value = float(np.mean([v for v, _ in group]))
            first = group[0][1]
            dims.append((kind, value, len(group), first, mid))
    return dims


def _centre(record, mid):
    axis = record["axis"]
    centre = record["origin"] - axis * ((record["origin"] - mid) @ axis)
    return centre


def _round_dim(kind, value, count, record, mid):
    axis = record["axis"]
    view = _view_for_axis(axis)
    perpendicular = _unit(np.cross(axis, [0, 0, 1] if abs(axis[2]) < 0.9 else [1, 0, 0]))
    if kind == "diameter":
        centre = _centre(record, mid)
        radius = value / 2
        name = f"diameter {value:g}"
        return Dim(name, kind, value, view, tuple(centre - perpendicular * radius), tuple(centre + perpendicular * radius), count)
    point = tuple(record["point"])
    name = f"{kind} {value:g}"
    return Dim(name, kind, value, view, point, point, count)


def _plane_levels(records):
    levels = {0: set(), 1: set(), 2: set()}
    oblique = []
    for r in records:
        if r["type"] != PLANE:
            continue
        axis = _axis_index(r["normal"])
        if axis is None:
            oblique.append(r)
        else:
            levels[axis].add(round(float(r["offset"] * r["normal"][axis]), 2))
    return levels, oblique


def _oblique_groups(oblique):
    items = []
    for r in oblique:
        normal = _unit(r["normal"])
        view = min(VIEW_DIRECTION, key=lambda v: abs(normal[VIEW_DIRECTION[v]]))
        a, b = [i for i in range(3) if i != VIEW_DIRECTION[view]]
        phi = math.degrees(math.atan2(abs(normal[a]), abs(normal[b])))
        items.append((round(min(phi, 90 - phi), 2), (view, r)))
    return _groups(items)


def _hole_centres(records):
    centres = {0: set(), 1: set(), 2: set()}
    views = {0: [], 1: [], 2: []}
    for c in _merge_cylinders(records):
        axis = _axis_index(c["axis"])
        if axis is None or c["span"] <= 2 * math.pi - 1e-3:
            continue
        for other in range(3):
            if other != axis:
                centres[other].add(round(float(c["origin"][other]), 2))
                views[other].append(FACE_ON_VIEW[axis])
    chosen = {a: max(set(v), key=v.count) for a, v in views.items() if v}
    return centres, chosen


def _level_dims(levels, centres, view_choice, low, high):
    dims = []
    for axis in range(3):
        wanted = {round(float(low[axis]), 2), round(float(high[axis]), 2)}
        wanted |= {v for v in levels[axis] | centres[axis] if low[axis] - SAME_MM <= v <= high[axis] + SAME_MM}
        points = sorted(wanted)
        view = view_choice.get(axis, LEVEL_VIEW[axis])
        for a, b in zip(points[:-1], points[1:-1]):
            if b - a < SAME_MM:
                continue
            p1, p2 = low.copy(), low.copy()
            p1[axis], p2[axis] = a, b
            dims.append(Dim(f"level {axis} {b:g}", "linear", float(b - a), view, tuple(p1), tuple(p2), 1, axis))
    return dims


def _envelope(low, high):
    dims = []
    for axis, view in ((0, "Front"), (2, "Front"), (1, "Top")):
        p2 = low.copy()
        p2[axis] = high[axis]
        dims.append(Dim("envelope", "envelope", float(high[axis] - low[axis]), view, tuple(low), tuple(p2), 1, axis))
    return dims


def dimensions(shape):
    records, _, _ = _facts(shape)
    low, high = bounds(shape)
    dims = _envelope(low, high)
    for kind, value, count, record, mid in _round_features(records, low, high):
        dims.append(_round_dim(kind, value, count, record, mid))
    for group in _oblique_groups(_plane_levels(records)[1]):
        view, record = group[0][1]
        point = tuple(record["normal"] * record["offset"])
        dims.append(Dim(f"angle {group[0][0]:g}", "angle", group[0][0], view, point, point, len(group)))
    levels, _ = _plane_levels(records)
    centres, view_choice = _hole_centres(records)
    return dims + _level_dims(levels, centres, view_choice, low, high)


def _has(dims, kind, value):
    return any(d.kind == kind and abs(d.value - value) < 2 * SAME_MM for d in dims)


def _tied(dims, axis, level, low, high):
    if min(abs(level - low[axis]), abs(level - high[axis])) < SAME_MM:
        return True
    return any(d.kind == "linear" and d.axis == axis and min(abs(d.p1[axis] - level), abs(d.p2[axis] - level)) < SAME_MM for d in dims)


def missing(shape, dims):
    """Rules the dimension list leaves unmet for this shape: an uncovered face type and value, an untied level, an unlocated hole or a missing envelope."""
    records, _, _ = _facts(shape)
    low, high = bounds(shape)
    out = []
    if {d.axis for d in dims if d.kind == "envelope"} != {0, 1, 2}:
        out.append("envelope")
    for c in _merge_cylinders(records):
        full = c["span"] > 2 * math.pi - 1e-3
        kind, value = ("diameter", 2 * c["radius"]) if full else ("radius", c["radius"])
        if not _has(dims, kind, value):
            out.append(f"cylinder {kind} {value:g}")
        axis = _axis_index(c["axis"])
        if full and axis is not None:
            for other in range(3):
                if other != axis and not _tied(dims, other, round(float(c["origin"][other]), 2), low, high):
                    out.append(f"hole {value:g} not located on axis {other}")
    for r in records:
        if r["type"] in (TORUS, SPHERE) and not _has(dims, "radius", r["radius"]):
            out.append(f"{r['type']} radius {r['radius']:g}")
        elif r["type"] == CONE:
            chamfer = abs(r["half_angle"] - CHAMFER_ANGLE) < ANGLE_TOLERANCE
            if chamfer and not _has(dims, "chamfer", r["height"]):
                out.append(f"cone chamfer {r['height']:g}")
            if not chamfer and not (_has(dims, "angle", r["half_angle"]) and _has(dims, "diameter", 2 * r["radius"])):
                out.append(f"cone angle {r['half_angle']:g}")
    levels, oblique = _plane_levels(records)
    for axis in range(3):
        for level in sorted(levels[axis]):
            if not _tied(dims, axis, level, low, high):
                out.append(f"plane level {'xyz'[axis]}={level:g}")
    for angle, _ in (g[0] for g in _oblique_groups(oblique)):
        if not _has(dims, "angle", angle):
            out.append(f"oblique plane angle {angle:g}")
    return sorted(set(out))


def complexity(shape):
    """Distinct dimension groups and distinct feature directions of a shape."""
    records, _, _ = _facts(shape)
    low, high = bounds(shape)
    levels, oblique = _plane_levels(records)
    features = len(_round_features(records, low, high)) + sum(len(v) for v in levels.values()) + len(_oblique_groups(oblique))
    key = lambda r: r["normal"] if r["type"] == PLANE else r["axis"]
    directions = {_direction_key(key(r)) for r in records if r["type"] != SPHERE}
    return {"features": features, "directions": len(directions)}
