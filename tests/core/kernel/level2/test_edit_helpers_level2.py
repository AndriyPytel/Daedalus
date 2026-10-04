"""
Level 2 -- on a Body whose BaseFeature is a plate with a bore, faces are picked by
plane and by cylinder with their count asserted, and `no_collateral` tells the
edit that changed the bore from the one that also cut material elsewhere.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


A_PLATE_WITH_A_BORE = """
plate = Part.makeBox(40, 30, 10)
plate = plate.cut(Part.makeCylinder(3, 10, App.Vector(20, 15, 0)))
base = doc.addObject('Part::Feature', 'Imported')
base.Shape = plate
body = doc.addObject('PartDesign::Body', 'Body')
body.BaseFeature = base
doc.recompute()
"""


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
held = top + bore
"""
    result = worker.check(code, None)
    assert result.ok, result.traceback
    assert len(result.held) == 2


def test_pick_faces_with_the_wrong_count_raises_with_what_it_found(worker):
    code = A_PLATE_WITH_A_BORE + "pick_faces(body.Shape, plane_face('z', 10), 2)\n"
    result = worker.check(code, None)
    assert not result.ok
    assert "wanted 2 faces, found 1" in result.traceback


def test_widening_the_bore_and_nothing_else_has_no_collateral(worker):
    code = (A_PLATE_WITH_A_BORE + _pocket(4, 20, 15, "Widen")
            + "no_collateral(doc, volume_change=-(3.141592653589793 * (16 - 9) * 10))\n")
    result = worker.check(code, None)
    assert result.ok, result.traceback


def test_a_pocket_elsewhere_fails_the_volume_asked(worker):
    code = (A_PLATE_WITH_A_BORE + _pocket(4, 20, 15, "Widen") + _pocket(6, 8, 8, "Extra")
            + "no_collateral(doc, volume_change=-(3.141592653589793 * (16 - 9) * 10))\n")
    result = worker.check(code, None)
    assert not result.ok
    assert "these did not hold" in result.traceback
    assert "volume" in result.traceback


def test_a_second_solid_fails_no_collateral(worker):
    code = (A_PLATE_WITH_A_BORE
            + "extra = body.newObject('PartDesign::AdditiveBox', 'Floating')\n"
              "extra.Length = extra.Width = extra.Height = 2\n"
              "extra.Placement = App.Placement(App.Vector(100, 100, 100), App.Rotation())\n"
              "doc.recompute()\nno_collateral(doc)\n")
    result = worker.check(code, None)
    assert not result.ok
    assert "these did not hold" in result.traceback
    assert "solids" in result.traceback
