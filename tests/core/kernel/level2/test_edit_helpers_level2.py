"""
Level 2 -- on a Body whose BaseFeature is a plate with a bore, faces are picked by
plane and by cylinder with their count asserted, and `no_collateral` tells the
edit that changed the bore from the one that also cut material elsewhere.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def worker(tmp_path):
    import FreeCAD
    import Part

    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    step = str(tmp_path / "input.step")
    plate = Part.makeBox(40, 30, 10)
    plate.cut(Part.makeCylinder(3, 10, FreeCAD.Vector(20, 15, 0))).exportStep(step)
    with Kernel() as running:
        assert running.seed(run.base_document(step)).ok
        yield running


A_PLATE_WITH_A_BORE = "body = doc.getObject('Body')\n"


def _pocket(radius, x, y, name):
    return """
sk = body.newObject('Sketcher::SketchObject', '%(name)s')
sk.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sk.MapMode = 'FlatFace'
sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
sk.addGeometry(Part.Circle(App.Vector(%(x)s, %(y)s, 0), App.Vector(0, 0, 1), %(radius)s), False)
doc.recompute()
pocket = body.newObject('PartDesign::Pocket', '%(name)sPocket')
pocket.Profile = sk
pocket.Type = 1
doc.recompute()
""" % {"name": name, "x": x, "y": y, "radius": radius}


def test_pick_faces_by_plane_and_cylinder_with_the_count_asserted(worker):
    code = A_PLATE_WITH_A_BORE + """
top = pick_faces(body.Shape, plane_face('z', 10), 1)
bore = pick_faces(body.Shape, cylinder_face(6, 'z'), 1)
facts = top + bore
import json
print("FACTS " + json.dumps(facts))
"""
    facts = worker.measure(code)
    assert facts is not None
    assert len(facts) == 2


def test_pick_faces_with_the_wrong_count_raises_with_what_it_found(worker):
    code = A_PLATE_WITH_A_BORE + "pick_faces(body.Shape, plane_face('z', 10), 2)\n"
    result = worker.rehearse(code)
    assert not result.ok
    assert "wanted 2 faces, found 1" in result.traceback


def test_widening_the_bore_and_nothing_else_has_no_collateral(worker):
    code = (A_PLATE_WITH_A_BORE + _pocket(4, 20, 15, "Widen")
            + "no_collateral(doc, volume_change=-(3.141592653589793 * (16 - 9) * 10))\n")
    result = worker.rehearse(code)
    assert result.ok, result.traceback


def test_a_pocket_elsewhere_fails_the_volume_asked(worker):
    code = (A_PLATE_WITH_A_BORE + _pocket(4, 20, 15, "Widen") + _pocket(6, 8, 8, "Extra")
            + "no_collateral(doc, volume_change=-(3.141592653589793 * (16 - 9) * 10))\n")
    result = worker.rehearse(code)
    assert not result.ok
    assert "these did not hold" in result.traceback
    assert "volume" in result.traceback


def test_a_second_solid_fails_no_collateral(worker):
    code = (A_PLATE_WITH_A_BORE
            + "extra = body.newObject('PartDesign::AdditiveBox', 'Floating')\n"
              "extra.Length = extra.Width = extra.Height = 2\n"
              "extra.Placement = App.Placement(App.Vector(100, 100, 100), App.Rotation())\n"
              "doc.recompute()\nno_collateral(doc)\n")
    result = worker.rehearse(code)
    assert not result.ok
    assert "these did not hold" in result.traceback
    assert "solids" in result.traceback
