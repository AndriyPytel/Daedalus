"""
Runs inside freecadcmd, never the harness venv: DAE_JOB names a JSON file with "build" (a list of
[spec, FCStd path] pairs, each written as an Arch document plus an IFC4 export beside it) and
"measure" (a list of IFC files); the measurements go to DAE_RESULT. Walls, doors, windows and spaces
are read from world-coordinate meshes, hosting from IfcRelFillsElement or else from the
IfcRelVoidsElement opening that lies under the door or window.
"""
import json
import math
import os

import FreeCAD as App
import numpy
import Part

MM = 1000.0
SLAB = 0.2
ADJACENCY = 0.35
OPENING_MATCH = 0.3
SILL_FRAME = 50


def corners(wall):
    ux, uy = math.cos(wall["ang"]), math.sin(wall["ang"])
    half_l, half_t = wall["len"] / 2, wall["thk"] / 2
    return [
        (wall["c"][0] + a * ux - b * uy, wall["c"][1] + a * uy + b * ux)
        for a, b in ((-half_l, -half_t), (half_l, -half_t), (half_l, half_t), (-half_l, half_t))
    ]


def hull(points):
    points = sorted(set(points))

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(p)
        return out

    lower, upper = half(points), half(reversed(points))
    return lower[:-1] + upper[:-1]


def make_wall(Arch, Draft, wall, z=0):
    centre = App.Vector(wall["c"][0] * MM, wall["c"][1] * MM, z * MM)
    direction = App.Vector(math.cos(wall["ang"]), math.sin(wall["ang"]), 0)
    line = Draft.makeLine(centre - direction * wall["len"] * MM / 2, centre + direction * wall["len"] * MM / 2)
    obj = Arch.makeWall(line, width=wall["thk"] * MM, height=wall["h"] * MM, align="Center")
    obj.Label = f"Wall_{wall['id']}"
    return obj


def make_opening(Arch, kind, item, z=0):
    ux, uy = math.cos(item["ang"]), math.sin(item["ang"])
    direction, normal = App.Vector(ux, uy, 0), App.Vector(uy, -ux, 0)
    position = App.Vector(item["c"][0] * MM, item["c"][1] * MM, (z + item["sill"]) * MM) - direction * (item["w"] * MM / 2) + normal * SILL_FRAME / 2
    preset = "Simple door" if kind == "Door" else "Open 1-pane"
    obj = Arch.makeWindowPreset(
        preset, item["w"] * MM, item["h"] * MM, 50, 100, 100, 50, 50, 0, 0,
        placement=App.Placement(position, App.Rotation(direction, App.Vector(0, 0, 1), normal, "ZXY")),
    )
    obj.IfcType = kind
    obj.Label = f"{kind}_{item['id']}"
    return obj


def make_storey(doc, Arch, Draft, spec, z):
    """The walls, openings, spaces and slab of the flat in *spec* with its floor at *z* metres."""
    walls = {w["id"]: make_wall(Arch, Draft, w, z) for w in spec["walls"]}
    doc.recompute()
    objects = list(walls.values())
    for kind, key in (("Door", "doors"), ("Window", "windows")):
        for item in spec[key]:
            obj = make_opening(Arch, kind, item, z)
            if item["host"]:
                obj.Hosts = [walls[item["host"]]]
            objects.append(obj)
    for space in spec["spaces"]:
        face = Part.Face(Part.makePolygon([App.Vector(x * MM, y * MM, z * MM) for x, y in space["poly"]] + [App.Vector(space["poly"][0][0] * MM, space["poly"][0][1] * MM, z * MM)]))
        base = doc.addObject("Part::Feature", f"Outline_{space['id']}")
        base.Shape = face.extrude(App.Vector(0, 0, spec["storey_height"] * MM))
        obj = Arch.makeSpace(base)
        obj.Label = f"Space_{space['id']}"
        obj.LongName = space["name"]
        objects.append(obj)
    outline = hull([p for w in spec["walls"] for p in corners(w)])
    slab_base = doc.addObject("Part::Feature", "Outline_Slab")
    slab_base.Shape = Part.Face(Part.makePolygon([App.Vector(x * MM, y * MM, (z - SLAB) * MM) for x, y in outline + outline[:1]]))
    slab = Arch.makeStructure(slab_base, height=SLAB * MM)
    slab.IfcType = "Slab"
    slab.Label = "Slab"
    objects.append(slab)
    return objects


def make_stairs(Arch, Draft, spec, stair):
    z = stair["from"] * spec["storey_height"]
    start = App.Vector(stair["start"][0] * MM, stair["start"][1] * MM, z * MM)
    end = start + App.Vector(stair["dir"][0], stair["dir"][1], 0) * stair["len"] * MM
    obj = Arch.makeStairs(Draft.makeLine(start, end), width=stair["w"] * MM, height=spec["storey_height"] * MM, steps=stair["steps"])
    obj.Align = "Center"
    obj.Label = f"Stairs_{stair['from']}"
    return obj


def build(spec, path):
    import Arch
    import Draft
    import importers.exportIFC as exporter

    doc = App.newDocument("Flat")
    count = spec.get("storeys", 0)
    if count:
        levels, objects = [], []
        for number in range(count):
            level = Arch.makeFloor()
            level.Label = f"Level_{number}"
            level.Placement.Base = App.Vector(0, 0, number * spec["storey_height"] * MM)
            level.Group = make_storey(doc, Arch, Draft, spec, number * spec["storey_height"])
            levels.append(level)
            objects += level.Group
        for stair in spec["stairs"]:
            made = make_stairs(Arch, Draft, spec, stair)
            levels[stair["from"]].addObject(made)
            objects.append(made)
        building = Arch.makeBuilding()
        building.Group = levels
        objects = [building, *levels, *objects]
    else:
        objects = make_storey(doc, Arch, Draft, spec, 0)
    doc.recompute()
    doc.saveAs(path)
    exporter.export(objects, os.path.splitext(path)[0] + ".ifc")
    App.closeDocument(doc.Name)


def mesh(settings, geom, element):
    """World-coordinate vertices and triangles of the element, or None when it has no usable shape."""
    if element.Representation is None:
        return None
    try:
        shape = geom.create_shape(settings, element)
    except RuntimeError:
        return None
    if len(shape.geometry.verts) == 0:
        return None
    return numpy.array(shape.geometry.verts).reshape(-1, 3), numpy.array(shape.geometry.faces).reshape(-1, 3)


def rectangle_of(xy):
    boundary = numpy.array(hull([tuple(p) for p in numpy.round(xy, 9)]))
    best = None
    for a, b in zip(boundary, numpy.roll(boundary, -1, axis=0)):
        edge = b - a
        norm = numpy.linalg.norm(edge)
        if norm == 0:
            continue
        u = edge / norm
        v = numpy.array([-u[1], u[0]])
        s, t = boundary @ u, boundary @ v
        size = (s.max() - s.min(), t.max() - t.min())
        if best is None or size[0] * size[1] < best[0]:
            best = (size[0] * size[1], size, (s.max() + s.min()) / 2 * u + (t.max() + t.min()) / 2 * v)
    (_, (one, other), centre) = best
    return centre, max(one, other), min(one, other)


def segment_distance(points, starts, ends):
    d = ends - starts
    length = (d ** 2).sum(axis=1)
    t = numpy.clip(((points[:, None, :] - starts) * d).sum(axis=2) / numpy.where(length == 0, 1, length), 0, 1)
    nearest = starts + t[..., None] * d
    return numpy.sqrt(((points[:, None, :] - nearest) ** 2).sum(axis=2)).min()


def footprint_distance(one, other):
    flat = lambda tris: (tris.reshape(-1, 2), tris[:, [0, 1, 2], :].reshape(-1, 2), tris[:, [1, 2, 0], :].reshape(-1, 2))
    p1, s1, e1 = flat(one)
    p2, s2, e2 = flat(other)
    return min(segment_distance(p1, s2, e2), segment_distance(p2, s1, e1))


def top_triangles(verts, faces):
    tris = verts[faces]
    normal = numpy.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    up = numpy.abs(normal[:, 2]) > 0.99 * numpy.linalg.norm(normal, axis=1)
    up &= numpy.isclose(tris[:, :, 2].min(axis=1), tris[:, :, 2].max())
    return tris[up][:, :, :2]


def measure_scope(model, settings, geom, scope):
    """The counts, wall rectangles, spaces, adjacency and openings of the elements whose GlobalId is in *scope*, or of every element where it is None."""
    def of(name):
        return [e for e in model.by_type(name) if scope is None or e.GlobalId in scope]

    result = {"opens": True, "counts": {}}
    for name in ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcSpace"):
        result["counts"][name] = len(of(name))

    walls = []
    for wall in of("IfcWall"):
        shape = mesh(settings, geom, wall)
        if shape is None:
            continue
        verts, faces = shape
        centre, length, thickness = rectangle_of(verts[:, :2])
        walls.append((wall.GlobalId, {"c": [round(float(centre[0]), 4), round(float(centre[1]), 4)], "len": round(float(length), 4), "thk": round(float(thickness), 4), "h": round(float(verts[:, 2].max() - verts[:, 2].min()), 4)}))
    walls.sort(key=lambda w: (round(w[1]["c"][0], 2), round(w[1]["c"][1], 2), round(w[1]["len"], 2)))
    index = {gid: i for i, (gid, _) in enumerate(walls)}
    result["walls"] = [w for _, w in walls]

    spaces = []
    for space in of("IfcSpace"):
        shape = mesh(settings, geom, space)
        tris = top_triangles(*shape) if shape else numpy.zeros((0, 3, 2))
        if len(tris) == 0:
            continue
        cross = numpy.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]) / 2
        area = float(numpy.abs(cross).sum())
        centre = (tris.mean(axis=1) * numpy.abs(cross)[:, None]).sum(axis=0) / area
        spaces.append(({"c": [round(float(centre[0]), 4), round(float(centre[1]), 4)], "area": round(area, 4)}, tris))
    spaces.sort(key=lambda s: (round(s[0]["c"][0], 2), round(s[0]["c"][1], 2)))
    result["spaces"] = [{"c": s["c"], "area": s["area"]} for s, _ in spaces]
    result["adjacency"] = [
        [i, j] for i in range(len(spaces)) for j in range(i + 1, len(spaces))
        if footprint_distance(spaces[i][1], spaces[j][1]) <= ADJACENCY
    ]

    voids = []
    for rel in model.by_type("IfcRelVoidsElement"):
        if scope is not None and rel.RelatingBuildingElement.GlobalId not in scope:
            continue
        shape = mesh(settings, geom, rel.RelatedOpeningElement)
        if shape is None:
            continue
        verts, _ = shape
        voids.append(((verts[:, :2].min(axis=0) + verts[:, :2].max(axis=0)) / 2, rel.RelatingBuildingElement.GlobalId))
    openings = []
    for kind in ("IfcDoor", "IfcWindow"):
        for element in of(kind):
            shape = mesh(settings, geom, element)
            if shape is None:
                continue
            verts, _ = shape
            centre = (verts[:, :2].min(axis=0) + verts[:, :2].max(axis=0)) / 2
            _, width, _ = rectangle_of(verts[:, :2])
            host = None
            for fill in element.FillsVoids or []:
                for void in fill.RelatingOpeningElement.VoidsElements or []:
                    host = void.RelatingBuildingElement.GlobalId
            if host is None and voids:
                distance, wall = min(((float(numpy.linalg.norm(c - centre)), g) for c, g in voids), key=lambda d: d[0])
                host = wall if distance <= OPENING_MATCH else None
            openings.append({
                "type": kind, "c": [round(float(centre[0]), 4), round(float(centre[1]), 4)], "host": index.get(host),
                "w": round(float(width), 4), "h": round(float(verts[:, 2].max() - verts[:, 2].min()), 4),
            })
    result["openings"] = sorted(openings, key=lambda o: (o["type"], round(o["c"][0], 2), round(o["c"][1], 2)))
    return result


def measure(path):
    import ifcopenshell
    import ifcopenshell.geom as geom
    import ifcopenshell.util.element as element

    try:
        model = ifcopenshell.open(path)
    except Exception:
        return {"opens": False}
    settings = geom.settings()
    settings.set("use-world-coords", True)
    result = measure_scope(model, settings, geom, None)
    result["storeys"] = []
    levels = model.by_type("IfcBuildingStorey")
    if len(levels) > 1:
        for level in sorted(levels, key=lambda l: l.Elevation or 0.0):
            scope = {e.GlobalId for e in model.by_type("IfcProduct") if (element.get_container(e) or element.get_aggregate(e)) == level}
            result["storeys"].append({"elevation": round(float(level.Elevation or 0.0), 4), **measure_scope(model, settings, geom, scope)})
    result["stairs"] = []
    for stair in model.by_type("IfcStair"):
        shape = mesh(settings, geom, stair)
        if shape is None:
            continue
        verts, _ = shape
        low, high = verts.min(axis=0), verts.max(axis=0)
        result["stairs"].append({"c": [round(float((low[0] + high[0]) / 2), 4), round(float((low[1] + high[1]) / 2), 4)], "z": round(float(low[2]), 4), "rise": round(float(high[2] - low[2]), 4)})
    result["stairs"].sort(key=lambda t: (t["z"], t["c"][0], t["c"][1]))
    return result


with open(os.environ["DAE_JOB"]) as source:
    job = json.load(source)
for spec, path in job.get("build", []):
    try:
        build(spec, path)
    except Exception:
        App.Console.PrintError(f"{path}: build failed\n")
with open(os.environ["DAE_RESULT"], "w") as out:
    json.dump([measure(path) for path in job.get("measure", [])], out)
