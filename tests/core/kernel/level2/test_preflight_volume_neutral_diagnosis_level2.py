
"""
Level 2 -- the message names the measured cause, not the trap
(the existing level2 case in test_preflight_idle_features_level2.py already
covers the direction-fixable Pocket; this covers its opposite, the profile
that misses the solid at either sign of Reversed).
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

MISSES_EITHER_WAY = BLOCK + """
bore = doc.addObject('Sketcher::SketchObject', 'Bore')
body.addObject(bore)
bore.Placement = App.Placement(App.Vector(500, 0, 0), App.Rotation(App.Vector(1, 0, 0), -90))
bore.addGeometry(Part.Circle(App.Vector(50, -25, 0), App.Vector(0, 0, 1), 20), False)
pocket = doc.addObject('PartDesign::Pocket', 'Bore1')
body.addObject(pocket)
pocket.Profile = bore
pocket.Length = 100
doc.recompute()
"""


def test_a_profile_that_misses_the_solid_either_way_is_told_apart_from_a_direction_error(worker):
    assert worker.seed(rules=part_rules).ok
    said = worker.rehearse(MISSES_EITHER_WAY).traceback

    assert "does not overlap the solid at all" in said, said
    assert "Reversed will not fix this" in said, said
    assert "flipping Reversed changed its volume" not in said, said
