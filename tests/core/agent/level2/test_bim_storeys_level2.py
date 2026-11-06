"""
Level 2 -- a two-storey building with stairs, built by code in headless FreeCAD, passes the Assertion, and each refusal of a building fires on the real objects.
"""
import pytest

from core.agent.bim import measured, refusals, unfinished
from core.kernel import Kernel, find_freecadcmd
from tests.bim_helpers import FLAT

pytestmark = pytest.mark.freecad

HEIGHT = 2800.0

BUILDING = FLAT.replace("level = Arch.makeFloor()\n", "level = Arch.makeFloor()\nupper = Arch.makeFloor()\n") + """
upper.Placement.Base = App.Vector(0, 0, 2800)
upper_wall = Arch.makeWall(Draft.makeLine(App.Vector(0, 0, 2800), App.Vector(4000, 0, 2800)), width=200, height=2600, align="Center")
upper_slab = Arch.makeStructure(length=4000, width=3000, height=200)
upper_slab.IfcType = "Slab"
upper_slab.Placement.Base = App.Vector(2000, 1500, 2700)
upper.Group = [upper_wall, upper_slab]
building.Group = [level, upper]
line = Draft.makeLine(App.Vector(500, 1500, 0), App.Vector(3750, 1500, 0))
stairs = Arch.makeStairs(line, width=1000, height=2800, steps=15)
stairs.Align = "Center"
level.Group = level.Group + [stairs]
doc.recompute()
"""
SAME_ELEVATION = BUILDING.replace("upper.Placement.Base = App.Vector(0, 0, 2800)", "upper.Placement.Base = App.Vector(0, 0, 0)")
NO_STAIRS = BUILDING + "\ndoc.removeObject(stairs.Name)\ndoc.removeObject(line.Name)\ndoc.recompute()\n"
SHORT_STAIRS = BUILDING + "\nstairs.Height = 2000\ndoc.recompute()\n"
STAIRS_ASIDE = BUILDING + "\nstairs.Placement.Base = App.Vector(9000, 0, 0)\ndoc.recompute()\n"
LEVEL_OUTSIDE = BUILDING + "\nbuilding.Group = [level]\ndoc.recompute()\n"
STAIRS_OUTSIDE = BUILDING + "\nlevel.Group = [o for o in level.Group if o != stairs]\ndoc.recompute()\n"
EMPTY_UPPER = BUILDING + "\nupper.Group = []\ndoc.recompute()\n"

CASES = {
    "levels at one elevation": (SAME_ELEVATION, "elevation"),
    "no stairs": (NO_STAIRS, "Stairs"),
    "stairs that stop short": (SHORT_STAIRS, "Stairs"),
    "stairs outside every level": (STAIRS_OUTSIDE, "not under a Level"),
    "a level without walls": (EMPTY_UPPER, "no Wall"),
    "stairs beside the walls": (STAIRS_ASIDE, "walls"),
    "a level outside the building": (LEVEL_OUTSIDE, "not under the Building"),
}


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=180) as running:
        yield running


def _fresh(kernel):
    kernel.check("for o in list(doc.Objects):\n    doc.removeObject(o.Name)")


def _found(kernel, code):
    _fresh(kernel)
    assert kernel.check(code).ok
    return refusals(measured(kernel))


def test_a_two_storey_building_with_stairs_passes(kernel):
    _fresh(kernel)
    assert kernel.check(BUILDING).ok

    facts = measured(kernel)

    assert refusals(facts) == [], refusals(facts)
    assert unfinished(kernel) is None
    assert [round(level["elevation"]) for level in facts["levels"]] == [0, 2800]
    assert len(facts["stairs"]) == 1 and facts["stairs"][0]["bottom"] == pytest.approx(0, abs=1)
    assert facts["stairs"][0]["top"] == pytest.approx(HEIGHT, abs=1)


@pytest.mark.parametrize("name", CASES)
def test_a_building_that_breaks_one_condition_is_refused_with_the_reason(kernel, name):
    code, reason = CASES[name]

    found = _found(kernel, code)

    assert any(reason in line for line in found), found


def test_the_single_flat_assertion_is_unchanged_by_the_levels_it_measures(kernel):
    _fresh(kernel)
    assert kernel.check(FLAT).ok

    assert refusals(measured(kernel)) == []
    assert unfinished(kernel) is None
