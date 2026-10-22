"""
Dimensions of a solid read from its faces alone, in mm and world coordinates. The views are the
first-angle Front (looks along Y, shows X and Z), Top (looks along Z, shows X and Y) and Left
(looks along X, shows Y and Z). Planes and hole centres become chained levels per axis from the
bounding-box datum, equal features within 0.01 mm become one counted dimension, and B-spline
faces carry none. Features that repeat or mirror are covered by constraints instead of measurements: a
symmetry about the envelope's centre plane (one side plus the pair distance), equal spacing on a line or
a bolt circle, concentric holes sharing one location, and a note for fillets and chamfers below
max(0.5 mm, 1% of the envelope diagonal). Each constraint is a Dim of its own kind whose `covers` lists
the faces it accounts for, and missing() accepts them as coverage.
"""
import math
from dataclasses import asdict, dataclass, field

import numpy as np

SAME_MM = 0.01
FACE_ON_VIEW = {0: "Left", 1: "Front", 2: "Top"}
LEVEL_VIEW = {0: "Front", 1: "Top", 2: "Front"}
VIEW_DIRECTION = {"Left": 0, "Front": 1, "Top": 2}
SMALL_MM = 0.5
SMALL_SHARE = 0.01
EQUAL_ANGLE = 0.1
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
    label: str = ""
    covers: list = field(default_factory=list)

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


def orient(shape):
    """The shape turned so its longest bounding-box axis is X and its second longest Z, which puts the long side horizontal in the Front view."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    low, high = bounds(shape)
    longest, middle, shortest = np.argsort(-(high - low), kind="stable")
    matrix = np.zeros((3, 3))
    matrix[0, longest] = matrix[2, middle] = matrix[1, shortest] = 1.0
    matrix[1] *= np.linalg.det(matrix)
    turn = gp_Trsf()
    turn.SetValues(*matrix[0], 0.0, *matrix[1], 0.0, *matrix[2], 0.0)
    return BRepBuilderAPI_Transform(shape, turn, True).Shape()


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
            records.append({
                "type": PLANE, "normal": normal, "offset": float(normal @ _xyz(plane.Location())),
                "point": _xyz(surface.Value((u0 + u1) / 2, (v0 + v1) / 2)),
            })
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


def face_counts(shape):
    """Faces per surface type; non-analytic surfaces count as bspline."""
    counts = {name: 0 for name in (PLANE, CYLINDER, CONE, TORUS, SPHERE, BSPLINE)}
    for kind, _, _ in _faces(shape):
        counts[kind] += 1
    return counts


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


def _concentric(records):
    """One record per axis line that carries holes of several diameters: they share the location of the first."""
    lines = {}
    for c in _merge_cylinders(records):
        axis = _axis_index(c["axis"])
        if axis is not None and c["span"] > 2 * math.pi - 1e-3:
            lines.setdefault((axis, tuple(np.round(c["origin"][[i for i in range(3) if i != axis]], 1))), []).append(c)
    dims = []
    for (axis, _), group in lines.items():
        radii = sorted({round(c["radius"], 2) for c in group})
        if len(radii) > 1:
            point = tuple(group[0]["origin"])
            dims.append(Dim("concentric", "concentric", 0.0, FACE_ON_VIEW[axis], point, point, len(radii), axis, covers=[["diameter", 2 * r] for r in radii[1:]]))
    return dims


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


def _equal_steps(values, tolerance):
    steps = np.diff(values)
    return len(values) >= 3 and bool(np.all(np.abs(steps - steps[0]) < tolerance))


def _spot(axis, radius, c):
    o1, o2 = [i for i in range(3) if i != axis]
    return axis, radius, round(float(c["origin"][o1]), 2), round(float(c["origin"][o2]), 2)


def _patterns(records):
    """Equal-pitch rows and bolt circles of identical axis-aligned through holes, each hole in at most one."""
    holes = {}
    for index, c in enumerate(_merge_cylinders(records)):
        axis = _axis_index(c["axis"])
        if axis is not None and c["span"] > 2 * math.pi - 1e-3:
            holes.setdefault((axis, round(c["radius"], 2)), []).append(c)
    found = []
    for (axis, radius), group in holes.items():
        free = list(range(len(group)))
        o1, o2 = [i for i in range(3) if i != axis]
        for along, across in ((o1, o2), (o2, o1)):
            lines = {}
            for i in free:
                lines.setdefault(round(float(group[i]["origin"][across]), 2), []).append(i)
            for level, members in sorted(lines.items()):
                members.sort(key=lambda i: group[i]["origin"][along])
                values = np.array([group[i]["origin"][along] for i in members])
                if _equal_steps(values, 2 * SAME_MM) and all(i in free for i in members):
                    found.append({"kind": "row", "members": {_spot(axis, radius, group[i]) for i in members}, "axis": axis, "radius": radius, "along": along, "across": across, "level": level, "values": values})
                    free = [i for i in free if i not in members]
        if len(free) >= 3:
            points = np.array([[group[i]["origin"][o1], group[i]["origin"][o2]] for i in free])
            centre = points.mean(axis=0)
            offsets = points - centre
            distance = np.linalg.norm(offsets, axis=1)
            angles = np.sort(np.degrees(np.arctan2(offsets[:, 1], offsets[:, 0])))
            gaps = np.diff(np.append(angles, angles[0] + 360))
            if np.all(np.abs(distance - distance[0]) < 2 * SAME_MM) and np.all(np.abs(gaps - gaps[0]) < EQUAL_ANGLE):
                found.append({"kind": "circle", "members": {_spot(axis, radius, group[i]) for i in free}, "axis": axis, "radius": radius, "centre": centre, "pcd": float(2 * distance.mean()), "points": points, "o": (o1, o2)})
    return found


def _pattern_dim(pattern, low):
    axis = pattern["axis"]
    view = FACE_ON_VIEW[axis]
    if pattern["kind"] == "row":
        along, across = pattern["along"], pattern["across"]
        values = pattern["values"]
        p1, p2 = low.copy(), low.copy()
        p1[along], p2[along] = values[0], values[-1]
        p1[across] = p2[across] = pattern["level"]
        pitch = float(values[1] - values[0])
        covers = [[along, round(float(v), 2)] for v in values] + [[across, pattern["level"]]]
        return Dim("eq_spacing row", "eq_spacing", pitch, view, tuple(p1), tuple(p2), len(values) - 1, along, label=f"{len(values) - 1}\u00d7{pitch:.2f}".rstrip("0").rstrip(".") + " EQ SP", covers=covers)
    o1, o2 = pattern["o"]
    first = low.copy()
    first[o1], first[o2] = pattern["points"][0]
    n = len(pattern["points"])
    pcd = f"{pattern['pcd']:.2f}".rstrip("0").rstrip(".")
    diameter = f"{2 * pattern['radius']:.2f}".rstrip("0").rstrip(".")
    covers = [[o, round(float(p[k]), 2)] for p in pattern["points"] for k, o in enumerate((o1, o2))] + [["diameter", 2 * pattern["radius"]]]
    return Dim("eq_spacing circle", "eq_spacing", pattern["pcd"], view, tuple(first), tuple(first), n, -1, label=f"{n}\u00d7\u00d8{diameter} EQ SP on PCD \u00d8{pcd}", covers=covers)


def _hole_centres(records, patterns=()):
    centres = {0: set(), 1: set(), 2: set()}
    views = {0: [], 1: [], 2: []}
    taken = set().union(*(p["members"] for p in patterns)) if patterns else set()
    for c in _merge_cylinders(records):
        axis = _axis_index(c["axis"])
        if axis is None or c["span"] <= 2 * math.pi - 1e-3:
            continue
        for other in range(3):
            if other != axis:
                views[other].append(FACE_ON_VIEW[axis])
                if _spot(axis, round(c["radius"], 2), c) not in taken:
                    centres[other].add(round(float(c["origin"][other]), 2))
    for p in patterns:
        if p["kind"] == "row":
            centres[p["along"]] |= {round(float(p["values"][0]), 2), round(float(p["values"][-1]), 2)}
            centres[p["across"]].add(p["level"])
        else:
            for k, o in enumerate(p["o"]):
                centres[o].add(round(float(p["centre"][k]), 2))
    chosen = {a: max(set(v), key=v.count) for a, v in views.items() if v}
    return centres, chosen


def _level_dims(levels, centres, view_choice, low, high, load, spans):
    """Chained level dims per axis, each chain on the allowed view that carries the fewest dims so far.
    Levels that mirror about the envelope's centre plane keep one side plus the pair distance and a symmetry
    constraint; a chain link that is an equal-spacing span is left to that dim."""
    chains, constraints = {}, {}
    for axis in range(3):
        wanted = {round(float(low[axis]), 2), round(float(high[axis]), 2)}
        wanted |= {v for v in levels[axis] | centres[axis] if low[axis] - SAME_MM <= v <= high[axis] + SAME_MM}
        total = round(float(low[axis] + high[axis]), 2)
        inner = sorted(wanted - {round(float(low[axis]), 2), round(float(high[axis]), 2)})
        pairs = [(a, b) for a in inner for b in inner if a < b and abs(a + b - total) < 1.5 * SAME_MM]
        if pairs:
            wanted -= {b for _, b in pairs}
            constraints[axis] = pairs
        points = sorted(wanted)
        chains[axis] = [(a, b) for a, b in zip(points[:-1], points[1:-1]) if b - a >= SAME_MM and (axis, a, b) not in spans]
    dims = []
    for axis in sorted(chains, key=lambda a: -len(chains[a]) - (axis in constraints)):
        allowed = [v for v in VIEW_DIRECTION if VIEW_DIRECTION[v] != axis]
        view = min(allowed, key=lambda v: (load[v], v != view_choice.get(axis, LEVEL_VIEW[axis])))
        load[view] += len(chains[axis]) + (axis in constraints)
        for a, b in chains[axis]:
            p1, p2 = low.copy(), low.copy()
            p1[axis], p2[axis] = a, b
            dims.append(Dim(f"level {axis} {b:g}", "linear", float(b - a), view, tuple(p1), tuple(p2), 1, axis))
        if axis in constraints:
            a, b = max(constraints[axis])
            p1, p2 = low.copy(), low.copy()
            p1[axis], p2[axis] = a, b
            dims.append(Dim(f"pair {axis}", "linear", float(b - a), view, tuple(p1), tuple(p2), 1, axis))
            centre = low.copy()
            centre[axis] = (low[axis] + high[axis]) / 2
            dims.append(Dim(f"symmetry {axis}", "symmetry", 0.0, view, tuple(centre), tuple(centre), 1, axis, covers=[[axis, b] for _, b in constraints[axis]]))
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
    load = {view: 0 for view in VIEW_DIRECTION}
    patterns = _patterns(records)
    circles = {}
    for p in patterns:
        if p["kind"] == "circle":
            circles[round(2 * p["radius"], 2)] = circles.get(round(2 * p["radius"], 2), 0) + len(p["points"])
    small = max(SMALL_MM, SMALL_SHARE * float(np.linalg.norm(high - low)))
    notes = {"radius": [], "chamfer": []}
    for kind, value, count, record, mid in _round_features(records, low, high):
        if kind == "diameter" and circles.get(round(value, 2)) == count:
            continue
        if kind == "chamfer" or (kind == "radius" and record["type"] in (CYLINDER, TORUS)):
            if value < small:
                notes[kind].append(value)
                continue
        dims.append(_round_dim(kind, value, count, record, mid))
        load[dims[-1].view] += 1
    for kind, title, letter in (("radius", "UNSPECIFIED FILLETS", "R"), ("chamfer", "UNSPECIFIED CHAMFERS", "C")):
        if notes[kind]:
            values = sorted(notes[kind])
            text = ", ".join(f"{letter}{v:.2f}".rstrip("0").rstrip(".") for v in values)
            dims.append(Dim(title, "note", 0.0, "Front", (0, 0, 0), (0, 0, 0), len(values), label=f"{title} {text}", covers=[[kind, v] for v in values]))
    dims += [_pattern_dim(p, low) for p in patterns]
    dims += _concentric(records)
    for group in _oblique_groups(_plane_levels(records)[1]):
        view, record = group[0][1]
        point = tuple(record["point"])
        dims.append(Dim(f"angle {group[0][0]:g}", "angle", group[0][0], view, point, point, len(group)))
    levels, _ = _plane_levels(records)
    centres, view_choice = _hole_centres(records, patterns)
    spans = {(d.axis, round(d.p1[d.axis], 2), round(d.p2[d.axis], 2)) for d in dims if d.kind == "eq_spacing" and d.axis >= 0}
    return dims + _level_dims(levels, centres, view_choice, low, high, load, spans)


def _covered(dims, kind, value):
    return any(abs(c[1] - value) < 2 * SAME_MM for d in dims if d.kind in ("eq_spacing", "symmetry", "note") for c in d.covers if c[0] == kind)


def _has(dims, kind, value):
    return any(d.kind == kind and abs(d.value - value) < 2 * SAME_MM for d in dims) or _covered(dims, kind, value)


def _tied_directly(dims, axis, level, low, high):
    if min(abs(level - low[axis]), abs(level - high[axis])) < 2 * SAME_MM:
        return True
    return any(d.kind in ("linear", "eq_spacing") and d.axis == axis and min(abs(d.p1[axis] - level), abs(d.p2[axis] - level)) < 2 * SAME_MM for d in dims)


def _tied(dims, axis, level, low, high):
    if _tied_directly(dims, axis, level, low, high):
        return True
    mirror = float(low[axis] + high[axis]) - level
    symmetric = any(d.kind == "symmetry" and d.axis == axis and any(abs(c[1] - level) < 2 * SAME_MM for c in d.covers) for d in dims)
    return (symmetric and _tied_directly(dims, axis, mirror, low, high)) or any(
        d.kind == "eq_spacing" and any(c[0] == axis and abs(c[1] - level) < 2 * SAME_MM for c in d.covers) for d in dims
    )


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
