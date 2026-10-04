"""
Level 2 -- an editing run starts from a Document whose one Body carries the
part to edit as its BaseFeature, so an added feature fuses into that part and
a run that adds nothing exports that part once.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402

pytestmark = pytest.mark.freecad


def _plate_with_a_hole(path):
    import FreeCAD
    import Part

    plate = Part.makeBox(40, 30, 10)
    bore = Part.makeCylinder(3, 10, FreeCAD.Vector(20, 15, 0))
    plate.cut(bore).exportStep(path)
    return plate.Volume - bore.Volume


def test_the_base_step_is_the_body_s_base_feature_and_an_edit_fuses_into_it(tmp_path):
    import FreeCAD
    import Part

    step = str(tmp_path / "input.step")
    Part.makeBox(20, 10, 5).exportStep(step)

    doc = FreeCAD.openDocument(run.base_document(step))
    try:
        body = doc.getObject("Body")
        assert body.BaseFeature is not None
        assert abs(body.Shape.Volume - 1000) < 1e-6

        box = body.newObject("PartDesign::AdditiveBox", "Box")
        box.Length, box.Width, box.Height = 2, 2, 2
        box.Placement = FreeCAD.Placement(FreeCAD.Vector(19, 9, 4), FreeCAD.Rotation())
        doc.recompute()

        assert len(body.Shape.Solids) == 1
        assert abs(body.Shape.Volume - 1007) < 1e-6
        assert [obj.Name for obj in doc.Objects if not obj.InList] == ["Body"]
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_run_that_edits_nothing_exports_the_base_once(tmp_path):
    import Part

    from sdk import NO_KERNEL, Client

    step = str(tmp_path / "input.step")
    volume = _plate_with_a_hole(step)

    client = Client(document=run.base_document(step), kernel=NO_KERNEL)
    out = str(tmp_path / "out.step")
    try:
        assert client.has_solid()
        client.export_step(out)
    finally:
        client.close()

    exported = Part.read(out)
    assert len(exported.Solids) == 1
    assert abs(exported.Volume - volume) < 1e-3
