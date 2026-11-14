"""
Level 2 -- Preflight refuses a Step that leaves a Body with a zero-thickness wall.

A pocket whose cylinder is exactly tangent to the plate's side face recomputes clean and passes
OCC's `isValid()`, yet the official mesh gate rejects the STEP. The Step is refused with the two
faces named, so the next Step can move the pocket.
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


BODY_WITH_A_POCKET = """
class Plate:
    def __init__(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        plate = Part.makeBox(100, 60, 4)
        obj.Shape = plate.cut(Part.makeCylinder(%(r)s, 4, App.Vector(50, %(y)s, 0)))

body = doc.addObject('PartDesign::Body', 'Body')
feat = doc.addObject('PartDesign::FeaturePython', 'Plate')
Plate(feat)
body.addObject(feat)
body.Tip = feat
"""


def test_a_step_that_makes_a_tangent_contact_names_the_two_faces(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(BODY_WITH_A_POCKET % {"r": 8, "y": 52})

    assert not result.ok
    assert "is tangent to face" in result.traceback
    assert "cylinder R8" in result.traceback
    assert "(plane)" in result.traceback


def test_the_same_pocket_moved_off_the_wall_passes(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(BODY_WITH_A_POCKET % {"r": 8, "y": 40})

    assert result.ok, result.traceback
