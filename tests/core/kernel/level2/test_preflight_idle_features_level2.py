
"""
Level 2 — Preflight refuses a Step whose feature moved no material.

A Pocket sketched on the face at YMin with its normal pointing into the part
cuts along -Y, away from the solid, and returns the base shape unchanged.
FreeCAD calls it Up-to-date and Valid.
"""
import pytest

from core.agent import part_rules
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

BORE_FLIPPED = BORE.replace("App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(1, 0, 0), -90))",
                            "App.Placement(App.Vector(0, 100, 0), App.Rotation(App.Vector(1, 0, 0), -90))")

CUTS_NOTHING = BLOCK + BORE % ""
CUTS = BLOCK + BORE % "pocket.Reversed = True"
CUTS_NOTHING_WHEN_REVERSED = BLOCK + BORE_FLIPPED % "pocket.Reversed = True"


def test_a_pocket_that_cut_nothing_fails_preflight(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(CUTS_NOTHING)

    assert not result.ok, "a Pocket that removed no material was passed to the Document"


def test_the_failure_says_what_happened_and_what_to_do(worker):
    assert worker.seed(rules=part_rules).ok
    said = worker.rehearse(CUTS_NOTHING).traceback

    assert "neither removed nor added any material" in said, said
    assert "Bore1" in said, "the failure does not name the feature: %s" % said
    assert "Reversed" in said, "the failure does not say how to fix it: %s" % said
    assert "SideType" in said and "Midplane" not in said, \
        "the failure names the property FreeCAD warns on every write to: %s" % said


def test_reversed_false_that_cut_nothing_names_reversed_true(worker):
    assert worker.seed(rules=part_rules).ok
    said = worker.rehearse(CUTS_NOTHING).traceback

    assert "Reversed=True" in said, said
    assert "Reversed=False" not in said, said


def test_reversed_true_that_cut_nothing_names_reversed_false(worker):
    assert worker.seed(rules=part_rules).ok
    said = worker.rehearse(CUTS_NOTHING_WHEN_REVERSED).traceback

    assert "Reversed=False" in said, said
    assert "Reversed=True" not in said, said


def test_the_same_pocket_the_right_way_round_passes(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(CUTS)

    assert result.ok, result.traceback


def test_the_first_pad_in_an_empty_body_passes(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(BLOCK)

    assert result.ok, result.traceback


def test_a_step_that_only_draws_a_sketch_is_not_judged_by_volume(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(
        BLOCK + "\nsk = doc.addObject('Sketcher::SketchObject', 'Later')\n"
                "body.addObject(sk)\n"
                "sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), False)\n"
                "doc.recompute()\n")

    assert result.ok, result.traceback
