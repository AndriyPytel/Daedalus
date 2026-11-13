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
import task  # noqa: E402

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

    doc = FreeCAD.openDocument(task.seed_from_step(step))
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

    client = Client(document=task.seed_from_step(step), kernel=NO_KERNEL)
    out = str(tmp_path / "out.step")
    try:
        assert client.has_solid()
        client.export_step(out)
    finally:
        client.close()

    exported = Part.read(out)
    assert len(exported.Solids) == 1
    assert abs(exported.Volume - volume) < 1e-3


def test_an_invalid_base_is_repaired_before_it_becomes_the_base_feature(tmp_path, capsys):
    import FreeCAD
    from tests.core.level2.test_base_repair_level2 import _cube_with_a_needle

    step = str(tmp_path / "input.step")
    _cube_with_a_needle(0).exportStep(step)

    doc = FreeCAD.openDocument(task.seed_from_step(step))
    try:
        shape = doc.getObject("Body").Shape
        assert shape.isValid()
        assert abs(shape.Volume - 1000) < 1
    finally:
        FreeCAD.closeDocument(doc.Name)
    assert "base repair" in capsys.readouterr().out


def _edit_client_with_a_helper_solid(step):
    import FreeCAD
    import Part

    from sdk import NO_KERNEL, Client

    client = Client(document=task.seed_from_step(step), kernel=NO_KERNEL)
    helper = client.document.addObject("Part::Feature", "LiftZoneClear")
    helper.Shape = Part.makeBox(5, 5, 5, FreeCAD.Vector(100, 0, 0))
    client.document.recompute()
    return client


def test_an_edit_run_exports_the_body_alone_not_the_helper_solid_beside_it(tmp_path):
    import Part

    step = str(tmp_path / "input.step")
    volume = _plate_with_a_hole(step)
    client = _edit_client_with_a_helper_solid(step)
    out = str(tmp_path / "out.step")
    try:
        assert run.finish(client, type("R", (), {"ok": True})(), out, only="Body") == 0
    finally:
        client.close()

    exported = Part.read(out)
    assert len(exported.Solids) == 1
    assert abs(exported.Volume - volume) < 1e-3


def test_a_run_without_a_base_step_still_exports_every_top_level_object(tmp_path):
    import Part

    step = str(tmp_path / "input.step")
    volume = _plate_with_a_hole(step)
    client = _edit_client_with_a_helper_solid(step)
    out = str(tmp_path / "out.step")
    try:
        assert run.finish(client, type("R", (), {"ok": True})(), out) == 0
    finally:
        client.close()

    assert len(Part.read(out).Solids) == 2


def test_the_step_a_run_starts_from_is_kept_as_it_came_when_it_is_valid(tmp_path):
    import FreeCAD

    step, keep = str(tmp_path / "input.step"), str(tmp_path / "run.base.step")
    _plate_with_a_hole(step)

    FreeCAD.closeDocument(FreeCAD.openDocument(task.seed_from_step(step, keep)).Name)

    assert open(keep, "rb").read() == open(step, "rb").read()


def test_the_step_a_run_starts_from_is_kept_mended_when_it_was_invalid(tmp_path):
    import Part
    from tests.core.level2.test_base_repair_level2 import _cube_with_a_needle

    step, keep = str(tmp_path / "input.step"), str(tmp_path / "run.base.step")
    _cube_with_a_needle(0).exportStep(step)

    import FreeCAD

    FreeCAD.closeDocument(FreeCAD.openDocument(task.seed_from_step(step, keep)).Name)

    assert Part.read(keep).isValid()
    assert abs(Part.read(keep).Volume - 1000) < 1
