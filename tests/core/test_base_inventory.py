"""
Level 2 -- the harness reads the base of an editing run into text, so Design
describes the part without ever reaching a FreeCAD API.
"""
import pytest

pytestmark = pytest.mark.freecad


def _document(tmp_path, with_base):
    import FreeCAD
    import Part

    doc = FreeCAD.newDocument("Inventory")
    plate = Part.makeBox(40, 30, 10)
    plate = plate.cut(Part.makeCylinder(3, 10, FreeCAD.Vector(20, 15, 0)))
    feature = doc.addObject("Part::Feature", "Imported")
    feature.Shape = plate
    body = doc.addObject("PartDesign::Body", "Body")
    if with_base:
        body.BaseFeature = feature
    doc.recompute()
    return doc


def test_the_inventory_names_the_bounding_box_the_planes_and_the_hole(tmp_path):
    import FreeCAD

    from core.freecad_apply import base_inventory

    doc = _document(tmp_path, with_base=True)
    try:
        text = base_inventory(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)

    assert "40 x 30 x 10 mm" in text
    assert "plane" in text.lower() and "z = 10" in text
    assert "hole" in text.lower() and "diameter 6" in text and "axis (0, 0, 1)" in text


def test_a_document_without_a_base_has_no_inventory(tmp_path):
    import FreeCAD

    from core.freecad_apply import base_inventory

    doc = _document(tmp_path, with_base=False)
    try:
        assert base_inventory(doc) is None
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_the_loop_reads_the_inventory_through_a_tool_not_a_freecad_import(tmp_path):
    import FreeCAD

    from core.freecad_apply import build_registry

    doc = _document(tmp_path, with_base=True)
    try:
        tools = build_registry(document=doc)
        assert "40 x 30 x 10 mm" in tools.call("base_inventory")["inventory"]
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_the_bounding_box_is_the_surface_s_and_not_the_tessellation_s(tmp_path):
    import FreeCAD
    import Part

    from core.freecad_apply import base_inventory

    doc = FreeCAD.newDocument("Curved")
    feature = doc.addObject("Part::Feature", "Imported")
    feature.Shape = Part.makeCylinder(25, 10)
    feature.Shape.tessellate(0.5)
    body = doc.addObject("PartDesign::Body", "Body")
    body.BaseFeature = feature
    doc.recompute()
    try:
        assert body.BaseFeature.Shape.BoundBox.YLength < 50 - 1e-3
        text = base_inventory(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)

    assert "50 x 50 x 10 mm" in text
    assert "x -25..25" in text
