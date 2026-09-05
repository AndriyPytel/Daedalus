
"""
Level 2 — Preflight refuses a Step whose feature moved no material (FCC-96).

A Pocket sketched on the face at YMin with its normal pointing into the part
cuts along -Y, away from the solid, and returns the base shape unchanged.
FreeCAD calls it Up-to-date and Valid.
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


BLOCK = """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'Outer')
body.addObject(sketch)
sketch.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(100, 0, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(100, 0, 0), App.Vector(100, 100, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(100, 100, 0), App.Vector(0, 100, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(0, 100, 0), App.Vector(0, 0, 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = sketch
pad.Length = 50
doc.recompute()
"""

BORE = """
bore = doc.addObject('Sketcher::SketchObject', 'Bore')
body.addObject(bore)
bore.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(1, 0, 0), -90))
bore.addGeometry(Part.Circle(App.Vector(50, -25, 0), App.Vector(0, 0, 1), 20), False)
pocket = doc.addObject('PartDesign::Pocket', 'Bore1')
body.addObject(pocket)
pocket.Profile = bore
pocket.Length = 100
%s
doc.recompute()
"""

CUTS_NOTHING = BLOCK + BORE % ""
CUTS = BLOCK + BORE % "pocket.Reversed = True"


def test_a_pocket_that_cut_nothing_fails_preflight(worker):
    result = worker.check(CUTS_NOTHING, None)

    assert not result.ok, "a Pocket that removed no material was passed to the Document"


def test_the_failure_says_what_happened_and_what_to_do(worker):
    said = worker.check(CUTS_NOTHING, None).traceback

    assert "neither removed nor added any material" in said, said
    assert "Bore1" in said, "the failure does not name the feature: %s" % said
    assert "Reversed" in said, "the failure does not say how to fix it: %s" % said
    assert "SideType" in said and "Midplane" not in said, \
        "the failure names the property FreeCAD warns on every write to: %s" % said


def test_the_same_pocket_the_right_way_round_passes(worker):
    result = worker.check(CUTS, None)

    assert result.ok, result.traceback


def test_the_first_pad_in_an_empty_body_passes(worker):
    result = worker.check(BLOCK, None)

    assert result.ok, result.traceback


def test_a_step_that_only_draws_a_sketch_is_not_judged_by_volume(worker):
    result = worker.check(
        BLOCK + "\nsk = doc.addObject('Sketcher::SketchObject', 'Later')\n"
                "body.addObject(sk)\n"
                "sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), False)\n"
                "doc.recompute()\n", None)

    assert result.ok, result.traceback
