"""
Runs inside freecadcmd, never the harness venv: projects the STEP named by
DAE_STEP (mm) in first angle and writes the visible and hidden edges of the
Front, Top, Left and isometric views, with each view's direction and the
bounding-box centre TechDraw centres every view on, to DAE_VIEWS.
Headless TechDraw cannot export a page, so only the projection is taken here.
"""
import json
import os

import FreeCAD as App
import Part
import TechDraw

TEMPLATE = App.getResourceDir() + "Mod/TechDraw/Templates/ISO/A2_Landscape_ISO5457_minimal.svg"
VIEWS = ["Front", "Top", "Left", "FrontTopLeft"]


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
    result = {"center": [centre.x, centre.y, centre.z]}
    for view in group.Views:
        visible = view.getVisibleEdges()
        hidden = view.getHiddenEdges()
        box = Part.Compound(visible + hidden).BoundBox
        result[view.Type] = {
            "direction": list(view.Direction),
            "x_direction": list(view.XDirection),
            "visible_svg": TechDraw.exportSVGEdges(Part.Compound(visible)) if visible else "",
            "hidden_svg": TechDraw.exportSVGEdges(Part.Compound(hidden)) if hidden else "",
            "bbox2d": [box.XMin, box.YMin, box.XMax, box.YMax],
        }
    return result


with open(os.environ["DAE_VIEWS"], "w") as out:
    json.dump(project(os.environ["DAE_STEP"]), out)
