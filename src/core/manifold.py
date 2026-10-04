"""
The mesh gate CADGenBench runs on every STEP, and the repair for the one defect OCC's own
`isValid()` lets through: a cylinder face exactly tangent to another face, a wall of zero
thickness. Runs on `OCC.Core` shapes inside FreeCAD's interpreter; `check_fc` and `repair_fc` take
FreeCAD `Part.Shape`s.
"""
import os
import tempfile

import numpy as np
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCC.Core.BRepTools import breptools
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.GProp import GProp_GProps
from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_VERTEX
from OCC.Core.TopExp import TopExp_Explorer, topexp
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopTools import TopTools_IndexedMapOfShape
from OCC.Core.TopoDS import TopoDS_Shape, topods
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Vec
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

DEFLECTION_LADDER = (1, 4, 16, 32)
CONTACT_TOLERANCE = 0.02
CLEARANCE = 0.01
MAX_VOLUME_CHANGE = 0.005


class Finding:
    """What the gate rejected: its own sentence, and the points of the mesh where it failed."""

    def __init__(self, message, points):
        self.message = message
        self.points = points


def _indexed(shape, kind):
    found = TopTools_IndexedMapOfShape()
    topexp.MapShapes(shape, kind, found)
    return found


def _deflection(shape):
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    diagonal = float(np.linalg.norm([xmax - xmin, ymax - ymin, zmax - zmin]))
    return min(0.5, max(0.005, 0.001 * diagonal))


def _mesh(shape, deflection):
    """
    The welded triangle mesh the official gate builds: every face's own nodes, merged along each
    BRep edge by index and at each BRep vertex, zero-area triangles and mirrored pairs dropped.
    Raises `ValueError` where the official mesher would raise.
    """
    breptools.Clean(shape)
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, False)
    faces = _indexed(shape, TopAbs_FACE)
    vertices_map = _indexed(shape, TopAbs_VERTEX)
    nodes, triangles, first = [], [], {}
    triangulation = {}
    for fi in range(1, faces.Size() + 1):
        face = topods.Face(faces.FindKey(fi))
        location = TopLoc_Location()
        tri = BRep_Tool.Triangulation(face, location)
        if tri is None:
            raise ValueError("face %d missing triangulation" % fi)
        transform = location.Transformation()
        first[fi] = len(nodes)
        triangulation[fi] = (tri, location)
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(transform)
            nodes.append((p.X(), p.Y(), p.Z()))
        flip = face.Orientation() == TopAbs_REVERSED
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = (first[fi] + n - 1 for n in tri.Triangle(i).Get())
            triangles.append((b, a, c) if flip else (a, b, c))
    if not triangles:
        raise ValueError("tessellation produced no triangles")

    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    xyz = np.asarray(nodes, dtype=np.float64)
    edges_map = _indexed(shape, TopAbs_EDGE)
    along_edge, at_vertex = {}, {}
    for fi in range(1, faces.Size() + 1):
        tri, location = triangulation[fi]
        explorer = TopExp_Explorer(faces.FindKey(fi), TopAbs_EDGE)
        while explorer.More():
            edge = topods.Edge(explorer.Current())
            explorer.Next()
            polygon = BRep_Tool.PolygonOnTriangulation(edge, tri, location)
            if polygon is None:
                continue
            array = polygon.Nodes()
            ids = [first[fi] + array.Value(i) - 1 for i in range(array.Lower(), array.Upper() + 1)]
            along_edge.setdefault(edges_map.FindIndex(edge), []).append((edge, ids))
    for ei, listed in along_edge.items():
        reference = listed[0][1]
        for _edge, other in listed[1:]:
            if len(other) != len(reference):
                raise ValueError("edge %d: shared-edge node arrays differ in length" % ei)
            forward = np.abs(xyz[reference] - xyz[other]).max()
            backward = np.abs(xyz[reference] - xyz[other][::-1]).max()
            if backward < forward:
                raise ValueError("edge %d: shared-edge node arrays in opposite order" % ei)
            for u, v in zip(reference, other):
                union(u, v)
        edge = listed[0][0]
        for _e, ids in listed:
            for vertex, node in ((topexp.FirstVertex(edge), ids[0]), (topexp.LastVertex(edge), ids[-1])):
                index = vertices_map.FindIndex(vertex)
                if index:
                    at_vertex.setdefault(index, []).append(node)
    for index, members in at_vertex.items():
        point = BRep_Tool.Pnt(topods.Vertex(vertices_map.FindKey(index)))
        target = np.array([point.X(), point.Y(), point.Z()])
        if np.abs(xyz[members] - target).max() > deflection:
            raise ValueError("vertex %d: edge endpoint node far from the vertex" % index)
        for node in members[1:]:
            union(members[0], node)

    roots = np.array([find(i) for i in range(len(nodes))], dtype=np.int64)
    unique, inverse = np.unique(roots, return_inverse=True)
    sums = np.zeros((len(unique), 3))
    counts = np.zeros(len(unique))
    np.add.at(sums, inverse, xyz)
    np.add.at(counts, inverse, 1)
    merged = inverse[np.asarray(triangles, dtype=np.int64)]
    merged = merged[(merged[:, 0] != merged[:, 1]) & (merged[:, 1] != merged[:, 2])
                    & (merged[:, 0] != merged[:, 2])]
    merged = _without_mirrored_pairs(merged)
    if merged.shape[0] == 0:
        raise ValueError("stitching collapsed all triangles")
    return sums / np.maximum(counts, 1)[:, None], merged


def _without_mirrored_pairs(triangles):
    groups = {}
    for i, t in enumerate(triangles.tolist()):
        groups.setdefault(tuple(sorted(t)), []).append(i)
    drop = []
    for members in groups.values():
        if len(members) < 2:
            continue
        even = [i for i in members if _is_even(triangles[i])]
        odd = [i for i in members if not _is_even(triangles[i])]
        paired = min(len(even), len(odd))
        drop += even[:paired] + odd[:paired]
    if not drop:
        return triangles
    keep = np.ones(triangles.shape[0], dtype=bool)
    keep[drop] = False
    return triangles[keep]


def _is_even(t):
    x, y, z = sorted(int(v) for v in t)
    return (int(t[0]), int(t[1]), int(t[2])) in {(x, y, z), (y, z, x), (z, x, y)}


def _pinched(triangles, count):
    src = np.concatenate([triangles[:, 0], triangles[:, 1], triangles[:, 2]])
    p = np.concatenate([triangles[:, 1], triangles[:, 2], triangles[:, 0]])
    q = np.concatenate([triangles[:, 2], triangles[:, 0], triangles[:, 1]])
    half = np.concatenate([np.stack([src, p], 1), np.stack([src, q], 1)])
    node = np.unique(half[:, 0] * (count + 1) + half[:, 1], return_inverse=True)[1]
    size = int(node.max()) + 1
    m = src.size
    graph = coo_matrix((np.ones(m, dtype=np.int8), (node[:m], node[m:])), shape=(size, size))
    components, label = connected_components(graph, directed=False)
    owner = np.empty(size, dtype=np.int64)
    owner[node[:m]] = src
    pairs = np.unique(owner * components + label)
    owners, cycles = np.unique(pairs // components, return_counts=True)
    return owners[cycles > 1]


def _verdict(vertices, triangles):
    count = len(vertices)
    undirected = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    keys, degree = np.unique(undirected[:, 0] * (count + 1) + undirected[:, 1], return_counts=True)
    if degree.max() > 2:
        bad = keys[degree > 2]
        ends = np.unique(np.concatenate([bad // (count + 1), bad % (count + 1)]))
        return Finding("mesh non-manifold: %d edge(s) shared by more than 2 triangles" % len(bad), vertices[ends])
    if 2 * len(keys) != 3 * len(triangles):
        return Finding("mesh not closed: %d open edge(s)" % int((degree == 1).sum()), vertices[:0])
    directed = triangles[:, 0] * (count + 1) + triangles[:, 1]
    directed = np.concatenate([directed, triangles[:, 1] * (count + 1) + triangles[:, 2],
                               triangles[:, 2] * (count + 1) + triangles[:, 0]])
    if len(np.unique(directed)) != len(directed):
        return Finding("mesh orientation inconsistent", vertices[:0])
    pinched = _pinched(triangles, count)
    if pinched.size:
        return Finding("mesh non-manifold: %d vertex/vertices where two surface sheets meet at a point"
                       % pinched.size, vertices[pinched])
    return None


def check(shape):
    """
    None when *shape* meshes to a closed manifold at any rung of the official deflection ladder,
    else the `Finding` of the finest rung. A shape the official mesher would refuse is a `Finding` too.
    """
    base = _deflection(shape)
    finding = None
    for divisor in DEFLECTION_LADDER:
        try:
            finding = _verdict(*_mesh(shape, base / divisor))
        except ValueError as refused:
            finding = Finding("mesh not built: %s" % refused, np.zeros((0, 3)))
        if finding is None:
            return None
    return finding


def _near(shape, point):
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(c) for c in point])).Vertex()
    faces = _indexed(shape, TopAbs_FACE)
    for fi in range(1, faces.Size() + 1):
        distance = BRepExtrema_DistShapeShape(vertex, faces.FindKey(fi))
        if distance.Perform() and distance.Value() < CONTACT_TOLERANCE:
            yield fi, topods.Face(faces.FindKey(fi))


def _describe(face):
    surface = BRepAdaptor_Surface(face)
    if surface.GetType() == GeomAbs_Cylinder:
        return "cylinder R%g" % surface.Cylinder().Radius()
    if surface.GetType() == GeomAbs_Plane:
        return "plane"
    return "surface"


def _tangent_planes(cylinder_face, planes):
    cylinder = BRepAdaptor_Surface(cylinder_face).Cylinder()
    axis = cylinder.Axis()
    tangent = []
    for fi, face in planes:
        plane = BRepAdaptor_Surface(face).Plane()
        normal = plane.Axis().Direction()
        offset = abs(gp_Vec(plane.Location(), axis.Location()).Dot(gp_Vec(normal)))
        if abs(normal.Dot(axis.Direction())) < 1e-3 and abs(offset - cylinder.Radius()) < 1e-3:
            tangent.append(fi)
    return tangent


def contacts(shape, finding):
    """The sentences that name, at each failing point, the cylinder and the plane it is tangent to."""
    seen, lines = set(), []
    for point in finding.points:
        meeting = list(_near(shape, point))
        key = tuple(fi for fi, _ in meeting)
        if len(meeting) < 2 or key in seen:
            continue
        seen.add(key)
        planes = [(fi, face) for fi, face in meeting if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane]
        pairs = []
        for fi, face in meeting:
            if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder:
                pairs += ["face Face%d (%s) is tangent to face Face%d (plane)" % (fi, _describe(face), other)
                          for other in _tangent_planes(face, planes)]
        if not pairs:
            pairs = ["faces " + ", ".join("Face%d (%s)" % (fi, _describe(face)) for fi, face in meeting[:6])
                     + " pinch together"]
        lines.append("%s at (%.3f, %.3f, %.3f)" % ("; ".join(pairs), *point))
    return lines


def _volume(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


def _cylinders(shape, finding):
    chosen = {}
    for point in finding.points:
        for fi, face in _near(shape, point):
            if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder:
                chosen[fi] = face
    return list(chosen.values())


def _widened(face):
    cylinder = BRepAdaptor_Surface(face).Cylinder()
    axis = cylinder.Axis()
    _u0, _u1, v0, v1 = breptools.UVBounds(face)
    base = axis.Location().Translated(gp_Vec(axis.Direction()).Multiplied(v0))
    return BRepPrimAPI_MakeCylinder(gp_Ax2(base, axis.Direction()), cylinder.Radius() + CLEARANCE, v1 - v0).Shape()


def repair(shape, finding):
    """
    `(repaired, change, note)`: *shape* with each cylinder at the failing points widened by 0.01 mm,
    by Cut or by Fuse -- whichever moves the volume less and passes the gate. `repaired` is None,
    with the reason in `note`, when neither passes the gate or the volume would move by 0.5% or more.
    """
    cylinders = _cylinders(shape, finding)
    if not cylinders:
        return None, 0.0, "no cylinder face at the failing points"
    original = _volume(shape)
    best = None
    for operation in (BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse):
        candidate = shape
        for face in cylinders:
            candidate = operation(candidate, _widened(face)).Shape()
        change = (_volume(candidate) - original) / original
        if check(candidate) is None and (best is None or abs(change) < abs(best[1])):
            best = (candidate, change)
    if best is None:
        return None, 0.0, "widening the cylinder by %g mm did not give a manifold mesh" % CLEARANCE
    if abs(best[1]) >= MAX_VOLUME_CHANGE:
        return None, best[1], "the repair would change the volume by %.2f%%" % (100 * best[1])
    return best[0], best[1], "widened %d cylinder face(s) by %g mm, volume %+.4f%%" % (
        len(cylinders), CLEARANCE, 100 * best[1])


def from_freecad(fc_shape):
    handle, path = tempfile.mkstemp(suffix=".brep")
    os.close(handle)
    try:
        fc_shape.exportBrep(path)
        shape = TopoDS_Shape()
        breptools.Read(shape, path, BRep_Builder())
    finally:
        os.remove(path)
    return shape


def to_freecad(shape):
    import Part

    handle, path = tempfile.mkstemp(suffix=".brep")
    os.close(handle)
    try:
        breptools.Write(shape, path)
        return Part.read(path)
    finally:
        os.remove(path)


def check_fc(fc_shape):
    """The sentence that tells a Step which faces touch along a zero-thickness wall, or None."""
    if not fc_shape.Solids:
        return None
    shape = from_freecad(fc_shape)
    finding = check(shape)
    if finding is None:
        return None
    return "; ".join([finding.message] + contacts(shape, finding)[:5])


def repair_fc(fc_shape):
    """`(shape, note)`: *fc_shape* itself and None when it passes the gate, else `repair`'s outcome."""
    if not fc_shape.Solids:
        return fc_shape, None
    shape = from_freecad(fc_shape)
    finding = check(shape)
    if finding is None:
        return fc_shape, None
    repaired, _change, note = repair(shape, finding)
    return (to_freecad(repaired) if repaired is not None else fc_shape), note
