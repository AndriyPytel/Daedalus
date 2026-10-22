"""
Runs inside freecadcmd, never the harness venv: projects the STEP named by
DAE_STEP (mm) in first angle and writes the visible and hidden edges of the
Front, Top, Left, Right, Bottom, Rear and isometric views, with each view's direction and the
bounding-box centre TechDraw centres every view on, to DAE_VIEWS. When hidden
edges exceed 30% of all edges it also writes a Section view cut at the middle of
the axis whose plane crosses the most hidden edges, with the hatch triangles of
the cut face. Headless TechDraw cannot export a page, so only the projection is taken here.
"""
import json
import os

import FreeCAD as App
import Part
import TechDraw

TEMPLATE = App.getResourceDir() + "Mod/TechDraw/Templates/ISO/A2_Landscape_ISO5457_minimal.svg"
VIEWS = ["Front", "Top", "Left", "Right", "Bottom", "Rear", "FrontTopLeft"]
SECTION_HIDDEN_SHARE = 0.3
DEFLECTION = 0.2
COINCIDENT = 0.05


def edge_data(view, center):
    visible = view.getVisibleEdges()
    hidden = view.getHiddenEdges()
    box = Part.Compound(visible + hidden).BoundBox
    return {
        "direction": list(view.Direction),
        "x_direction": list(view.XDirection),
        "center": center,
        "visible_svg": TechDraw.exportSVGEdges(Part.Compound(visible)) if visible else "",
        "hidden_svg": TechDraw.exportSVGEdges(Part.Compound(hidden)) if hidden else "",
        "visible_lines": [[(p.x, -p.y) for p in e.discretize(Deflection=DEFLECTION)] for e in visible],
        "bbox2d": [box.XMin, box.YMin, box.XMax, box.YMax],
    }, visible, hidden


def uncovered(hidden, visible):
    """Hidden edges that do not lie on a visible edge: a box's back edges coincide with its front ones."""
    kept = []
    for edge in hidden:
        mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2)
        near = [e for e in visible if (e.BoundBox.XMin - COINCIDENT <= mid.x <= e.BoundBox.XMax + COINCIDENT and e.BoundBox.YMin - COINCIDENT <= mid.y <= e.BoundBox.YMax + COINCIDENT)]
        if not any(Part.Vertex(mid).distToShape(e)[0] < COINCIDENT for e in near):
            kept.append(edge)
    return kept


def straddles(edges, coordinate):
    count = 0
    for edge in edges:
        box = edge.BoundBox
        low, high = (box.XMin, box.XMax) if coordinate == "x" else (box.YMin, box.YMax)
        count += low < -1e-6 and high > 1e-6
    return count


def cut_axis(result, hidden_edges):
    """Axis whose mid plane is edge-on in two views and crosses the most hidden edges."""
    best = {}
    for axis in range(3):
        total = 0
        for name, edges in hidden_edges.items():
            if name == "FrontTopLeft":
                continue
            view = result[name]
            if abs(view["x_direction"][axis]) > 0.5:
                total += straddles(edges, "x")
            elif abs(view["direction"][axis]) < 0.5:
                total += straddles(edges, "y")
        best[axis] = total
    return max(best, key=best.get)


def section(page, shape, axis, direction, x_direction, center):
    box = shape.BoundBox
    size = max(box.XLength, box.YLength, box.ZLength) * 4
    origin = App.Vector(box.XMin - size, box.YMin - size, box.ZMin - size)
    lengths = [size * 2 + box.XLength, size * 2 + box.YLength, size * 2 + box.ZLength]
    plane = [box.Center.x, box.Center.y, box.Center.z][axis]
    sign = 1 if direction[axis] > 0 else -1
    lengths[axis] = size
    if sign > 0:
        origin[axis] = plane
    else:
        origin[axis] = plane - size
    cutter = Part.makeBox(*lengths, origin)
    rest = shape.cut(cutter)
    doc = page.Document
    body = doc.addObject("Part::Feature", "Cut")
    body.Shape = rest
    view = doc.addObject("TechDraw::DrawViewPart", "SectionView")
    page.addView(view)
    view.Source = [body]
    view.Direction = App.Vector(*direction)
    view.XDirection = App.Vector(*x_direction)
    view.HardHidden = False
    doc.recompute()
    rest_center = rest.BoundBox.Center
    data, _, _ = edge_data(view, [rest_center.x, rest_center.y, rest_center.z])
    data["axis"] = axis
    data["plane"] = plane
    xd = App.Vector(*x_direction)
    yd = App.Vector(*direction).cross(xd)
    triangles = []
    for face in rest.Faces:
        fb = face.BoundBox
        if [fb.XLength, fb.YLength, fb.ZLength][axis] > 1e-4:
            continue
        if abs([fb.Center.x, fb.Center.y, fb.Center.z][axis] - plane) > 1e-4:
            continue
        points, faces = face.tessellate(DEFLECTION)
        for tri in faces:
            flat = []
            for i in tri:
                offset = points[i] - rest_center
                flat += [offset.dot(xd), -offset.dot(yd)]
            triangles.append(flat)
    data["hatch"] = triangles
    return data


def project(step_path):
    doc = App.newDocument("sheet")
    body = doc.addObject("Part::Feature", "Body")
    body.Shape = Part.read(step_path)
    page = doc.addObject("TechDraw::DrawPage", "Page")
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = TEMPLATE
    page.Template = template
    group = doc.addObject("TechDraw::DrawProjGroup", "Views")
    page.addView(group)
    group.Source = [body]
    group.ProjectionType = "First angle"
    for name in VIEWS:
        group.addProjection(name)
    for view in group.Views:
        view.HardHidden = True
    doc.recompute()
    centre = body.Shape.BoundBox.Center
    center = [centre.x, centre.y, centre.z]
    result = {"center": center}
    hidden_edges = {}
    n_visible = n_hidden = 0
    for view in group.Views:
        result[view.Type], visible, hidden = edge_data(view, center)
        distinct = uncovered(hidden, visible)
        hidden_edges[view.Type] = distinct
        if view.Type in ("Front", "Top", "Left"):
            n_visible += len(visible)
            n_hidden += len(distinct)
    result["hidden_share"] = n_hidden / max(1, n_hidden + n_visible)
    if result["hidden_share"] > SECTION_HIDDEN_SHARE:
        axis = cut_axis(result, hidden_edges)
        face_on = {0: "Left", 1: "Front", 2: "Top"}[axis]
        base = result[face_on]
        result["Section"] = section(page, body.Shape, axis, base["direction"], base["x_direction"], center)
    return result


with open(os.environ["DAE_VIEWS"], "w") as out:
    json.dump(project(os.environ["DAE_STEP"]), out)
