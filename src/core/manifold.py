"""
The mesh gate CADGenBench runs on every STEP, and the repair for the one defect OCC's own
`isValid()` lets through: a cylinder face exactly tangent to another face, a wall of zero
thickness. Runs on `OCC.Core` shapes inside FreeCAD's interpreter; `check_fc` and `repair_fc` take
FreeCAD `Part.Shape`s.
"""
import os
import shutil
import tempfile

import numpy as np
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid, BRepBuilderAPI_MakeVertex, BRepBuilderAPI_Sewing
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCC.Core.BRepTools import BRepTools_ReShape, BRepTools_WireExplorer, breptools
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.GProp import GProp_GProps
from OCC.Core.Geom import Geom_TrimmedCurve
from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCC.Core.GeomConvert import GeomConvert_CompCurveToBSplineCurve, geomconvert
from OCC.Core.GeomFill import GeomFill_BSplineCurves, GeomFill_CurvedStyle
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Interface import Interface_Static
from OCC.Core.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCC.Core.ShapeFix import ShapeFix_Face, ShapeFix_Solid
from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE
from OCC.Core.TopExp import TopExp_Explorer, topexp
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape, TopTools_ListIteratorOfListOfShape
from OCC.Core.TopoDS import TopoDS_Shape, topods
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Vec
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

DEFLECTION_LADDER = (1, 4, 16, 32)
CONTACT_TOLERANCE = 0.02
CLEARANCE = 0.01
MAX_VOLUME_CHANGE = 0.005
STEP_PRECISION_MODES = (1, -1)
MIN_FACE_AREA = 1e-6
SEWING_TOLERANCE = 0.1
FACE_PRECISION = 1e-3
CUSP_FLATNESS = 1e-5
PATCH_SIDES = 4


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


def _read(path):
    reader = STEPControl_Reader()
    if reader.ReadFile(path) != IFSelect_RetDone:
        return None
    reader.TransferRoots()
    return reader.OneShape()


def _invalid_faces(shape):
    analyzer = BRepCheck_Analyzer(shape)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    faces = []
    while explorer.More():
        if not analyzer.IsValid(explorer.Current()):
            faces.append(explorer.Current())
        explorer.Next()
    return faces


def _describe_invalid(face):
    props = GProp_GProps()
    brepgprop.SurfaceProperties(face, props)
    centre = props.CentreOfMass()
    return "%s, area %.3f, centre (%.3f, %.3f, %.3f)" % (
        _describe(face), abs(props.Mass()), centre.X(), centre.Y(), centre.Z())


def step_round_trip_fc(fc_shape):
    """
    The sentence naming up to 5 faces that read back invalid from the STEP written for *fc_shape*, or None.

    Every mode `_survives_step` tries is written and read back; one that reads back valid passes the shape.
    The faces named are those of the first mode, the one the exporter writes first.
    """
    if not fc_shape.Solids:
        return None
    shape = from_freecad(fc_shape)
    handle, path = tempfile.mkstemp(suffix=".step")
    os.close(handle)
    try:
        bad = None
        for mode in (0,) + STEP_PRECISION_MODES:
            _write(shape, path, mode)
            read = _read(path)
            faces = _invalid_faces(read) if read is not None else []
            if read is not None and BRepCheck_Analyzer(read).IsValid():
                return None
            if bad is None:
                bad = faces
    finally:
        os.remove(path)
    return "; ".join(["%d face(s) read back invalid" % len(bad)] + [_describe_invalid(f) for f in bad[:5]])


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


def _reads_back(path):
    reader = STEPControl_Reader()
    if reader.ReadFile(path) != IFSelect_RetDone:
        return False
    reader.TransferRoots()
    shape = reader.OneShape()
    return BRepCheck_Analyzer(shape).IsValid() and check(shape) is None


def _write(shape, path, mode):
    Interface_Static.SetIVal("write.precision.mode", mode)
    try:
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(path)
    finally:
        Interface_Static.SetIVal("write.precision.mode", 0)


def rewrite_step(fc_shape, path):
    """
    The note on how the STEP at *path* was rewritten, or None when it reads back valid and manifold as written.
    Writing a file declares one uncertainty for the whole shape, and an imported solid with loose edge tolerances
    can lose a face's triangulation on reading it back; each of `STEP_PRECISION_MODES` declares another, and the
    first file that reads back clean is kept, the one written first where none does.
    """
    if _reads_back(path):
        return None
    shape = from_freecad(fc_shape)
    first = path + ".first"
    shutil.copyfile(path, first)
    for mode in STEP_PRECISION_MODES:
        _write(shape, path, mode)
        if _reads_back(path):
            os.remove(first)
            return "rewritten in STEP precision mode %d, the file as first written did not read back clean" % mode
    os.replace(first, path)
    return "no STEP precision mode reads back clean, the file goes out as first written"


def _area(face):
    props = GProp_GProps()
    brepgprop.SurfaceProperties(face, props)
    return abs(props.Mass())


def _faces(shape):
    found = _indexed(shape, TopAbs_FACE)
    return [topods.Face(found.FindKey(i)) for i in range(1, found.Size() + 1)]


def _bad_faces(shape):
    """
    The faces to drop: those OCC calls invalid, those with no triangulation at the finest rung of the
    ladder, and those under 1e-6 mm2. Larger slivers mesh fine and sewing absorbs them, while refilling every one leaves new faces no mesh resolves.
    """
    analyzer = BRepCheck_Analyzer(shape)
    breptools.Clean(shape)
    BRepMesh_IncrementalMesh(shape, _deflection(shape) / DEFLECTION_LADDER[-1], False, 0.5, False)
    return [face for face in _faces(shape)
            if not analyzer.IsValid(face) or BRep_Tool.Triangulation(face, TopLoc_Location()) is None
            or _area(face) < MIN_FACE_AREA]


def _straightened(shape, faces):
    """
    *shape* with each edge of *faces* that is a curve flat to 1e-5 of its length replaced by the straight edge
    between its vertices. Two faces meeting along such an edge at a zero angle are a cusp OCC cannot orient.
    """
    ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
    topexp.MapShapesAndAncestors(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
    reshape = BRepTools_ReShape()
    touched = []
    for face in faces:
        for edge in (topods.Edge(e) for e in _members(face, TopAbs_EDGE)):
            if BRepAdaptor_Curve(edge).GetType() == GeomAbs_Line:
                continue
            props = GProp_GProps()
            brepgprop.LinearProperties(edge, props)
            first, last = topexp.FirstVertex(edge, True), topexp.LastVertex(edge, True)
            if props.Mass() - BRep_Tool.Pnt(first).Distance(BRep_Tool.Pnt(last)) < CUSP_FLATNESS * props.Mass():
                reshape.Replace(edge, BRepBuilderAPI_MakeEdge(first, last).Edge().Oriented(edge.Orientation()))
                walker = TopTools_ListIteratorOfListOfShape(ancestors.FindFromKey(edge))
                while walker.More():
                    if not any(walker.Value().IsSame(seen) for seen in touched):
                        touched.append(topods.Face(walker.Value()))
                    walker.Next()
    if not touched:
        return shape
    rebuilt = BRepTools_ReShape()
    for face in touched:
        moved = reshape.Apply(face)
        rebuilt.Replace(moved, _fixed(moved))
    return rebuilt.Apply(reshape.Apply(shape))


def _members(shape, kind):
    found = _indexed(shape, kind)
    return [found.FindKey(i) for i in range(1, found.Size() + 1)]


def _closed_wires(shape):
    wires = ShapeAnalysis_FreeBounds(shape, SEWING_TOLERANCE, False, False).GetClosedWires()
    found = []
    explorer = TopExp_Explorer(wires, TopAbs_WIRE)
    while explorer.More():
        found.append(topods.Wire(explorer.Current()))
        explorer.Next()
    return found


def _fixed(face):
    fixer = ShapeFix_Face(face)
    fixer.SetPrecision(FACE_PRECISION)
    fixer.SetMaxTolerance(SEWING_TOLERANCE)
    fixer.Perform()
    return fixer.Result()


def _sound(face, base):
    if not BRepCheck_Analyzer(face).IsValid():
        return False
    for divisor in DEFLECTION_LADDER:
        BRepMesh_IncrementalMesh(face, base / divisor, False, 0.5, False)
        if BRep_Tool.Triangulation(face, TopLoc_Location()) is None:
            return False
    return True


def _curves(wire):
    """The wire's edges as B-spline curves chained head to tail, in wire order."""
    chained = []
    walk = BRepTools_WireExplorer(wire)
    while walk.More():
        edge = walk.Current()
        walk.Next()
        curve, first, last = BRep_Tool.Curve(edge)
        spline = geomconvert.CurveToBSplineCurve(Geom_TrimmedCurve(curve, first, last))
        if edge.Orientation() == TopAbs_REVERSED:
            spline.Reverse()
        if chained and spline.StartPoint().Distance(chained[-1].EndPoint()) > spline.EndPoint().Distance(chained[-1].EndPoint()):
            spline.Reverse()
        chained.append(spline)
    return chained


def _turn(before, after):
    tail, head = gp_Pnt(), gp_Pnt()
    out, into = gp_Vec(), gp_Vec()
    before.D1(before.LastParameter(), tail, out)
    after.D1(after.FirstParameter(), head, into)
    return out.Angle(into) if out.Magnitude() and into.Magnitude() else 0.0


def _patch(wire):
    """
    A Coons patch over *wire*, whose four sharpest turns are its corners. The surface a hole was cut out of
    can be the very thing no mesh resolves, so it is not used again.
    """
    curves = _curves(wire)
    count = len(curves)
    if count < PATCH_SIDES:
        raise ValueError("a wire of %d edge(s) has no four sides" % count)
    turns = [_turn(curves[i], curves[(i + 1) % count]) for i in range(count)]
    corners = sorted(sorted(range(count), key=lambda i: -turns[i])[:PATCH_SIDES])
    sides = []
    for k in range(PATCH_SIDES):
        joined = GeomConvert_CompCurveToBSplineCurve(curves[(corners[k] + 1) % count])
        index = (corners[k] + 1) % count
        while index != corners[(k + 1) % PATCH_SIDES]:
            index = (index + 1) % count
            if not joined.Add(curves[index], SEWING_TOLERANCE, False, False, 0):
                raise ValueError("a side of the patch does not join")
        sides.append(joined.BSplineCurve())
    surface = GeomFill_BSplineCurves(*sides, GeomFill_CurvedStyle).Surface()
    return _fixed(BRepBuilderAPI_MakeFace(surface, wire, False).Face())


def _refill(wire, dropped, base):
    """
    A face on *wire*: on the surface of the largest dropped face among those nearest to it, else a Coons patch
    where that face is invalid or does not mesh at every rung of the ladder.
    """
    distances = []
    for face in dropped:
        distance = BRepExtrema_DistShapeShape(wire, face)
        distance.Perform()
        distances.append(distance.Value())
    nearest = [face for face, value in zip(dropped, distances) if value <= min(distances) + SEWING_TOLERANCE]
    donor = max(nearest, key=_area)
    face = _fixed(BRepBuilderAPI_MakeFace(BRep_Tool.Surface(donor), wire, False).Face())
    if _sound(face, base):
        return face
    try:
        return _patch(wire)
    except ValueError:
        return face


def _sewn(faces):
    sewing = BRepBuilderAPI_Sewing(SEWING_TOLERANCE, True, True, True, False)
    for face in faces:
        sewing.Add(face)
    sewing.Perform()
    return sewing.SewedShape()


def _rebuilt(shape, dropped):
    kept = [face for face in _faces(shape) if not any(face.IsSame(gone) for gone in dropped)]
    sewn = _sewn(kept)
    base = _deflection(shape)
    refills = [_refill(wire, dropped, base) for wire in _closed_wires(sewn)]
    closed = _sewn(_faces(sewn) + refills)
    shells = _indexed(closed, TopAbs_SHELL)
    if shells.Size() != 1:
        return None
    fixer = ShapeFix_Solid(BRepBuilderAPI_MakeSolid(topods.Shell(shells.FindKey(1))).Solid())
    fixer.Perform()
    return fixer.Solid()


def _survives_step(shape):
    handle, path = tempfile.mkstemp(suffix=".step")
    os.close(handle)
    try:
        for mode in (0,) + STEP_PRECISION_MODES:
            _write(shape, path, mode)
            if _reads_back(path):
                return True
        return False
    finally:
        os.remove(path)


def repair_base(fc_shape):
    """
    `(shape, note)`: *fc_shape* itself and None when OCC calls it valid and it passes the gate, else the solid
    rebuilt without its bad faces: cusp edges straightened, the rest sewn, each hole refilled. The original and
    the reason come back where the rebuild is not one valid, manifold solid that survives a STEP round trip within 0.5% of the volume.
    """
    if not fc_shape.Solids:
        return fc_shape, None
    shape = from_freecad(fc_shape)
    if BRepCheck_Analyzer(shape).IsValid() and check(shape) is None:
        return fc_shape, None
    original = abs(_volume(shape))
    shape = _straightened(shape, _bad_faces(shape))
    dropped = _bad_faces(shape)
    try:
        rebuilt = _rebuilt(shape, dropped) if dropped else shape
    except (RuntimeError, ValueError) as failed:
        return fc_shape, "rebuilding without %d face(s) failed: %s" % (len(dropped), failed)
    if rebuilt is None:
        return fc_shape, "rebuilding without %d face(s) did not close one shell" % len(dropped)
    change = (abs(_volume(rebuilt)) - original) / original if original else 1.0
    if _indexed(rebuilt, TopAbs_SOLID).Size() != 1 or not BRepCheck_Analyzer(rebuilt).IsValid():
        return fc_shape, "rebuilding without %d face(s) gave no valid solid" % len(dropped)
    if check(rebuilt) is not None:
        return fc_shape, "rebuilding without %d face(s) still fails the mesh gate" % len(dropped)
    if not _survives_step(rebuilt):
        return fc_shape, "rebuilding without %d face(s) does not survive a STEP round trip" % len(dropped)
    if abs(change) >= MAX_VOLUME_CHANGE:
        return fc_shape, "rebuilding without %d face(s) would change the volume by %.4f%%" % (len(dropped), 100 * change)
    return to_freecad(rebuilt), "dropped %d face%s and refilled the holes, volume %+.4f%%" % (
        len(dropped), "" if len(dropped) == 1 else "s", 100 * change)
