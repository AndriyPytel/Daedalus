"""
Level 2 -- a PartDesign feature the Body's Tip never reaches builds nothing
into the Body, whatever its own Shape says.
"""
import pytest

from core.agent import part_rules
from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

PADDED_WITH_A_HOLE = (
    "import Part\n"
    "body = doc.addObject('PartDesign::Body', 'Body')\n"
    "disc = sketch_at(body, 'Disc', 'z', 0)\n"
    "disc.addGeometry(Part.Circle(App.Vector(), App.Vector(0, 0, 1), 50))\n"
    "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
    "body.addObject(pad)\n"
    "pad.Profile = disc\n"
    "pad.Length = 10\n"
    "hole = sketch_at(body, 'HoleProfile', 'z', 10)\n"
    "hole.addGeometry(Part.Circle(App.Vector(30, 0, 0), App.Vector(0, 0, 1), 5))\n"
    "pocket = doc.addObject('PartDesign::Pocket', 'Pocket')\n"
    "body.addObject(pocket)\n"
    "pocket.Profile = hole\n"
    "pocket.Type = 'ThroughAll'\n"
    "doc.recompute()\n"
)

PATTERNED = (
    "body = doc.getObject('Body')\n"
    "pattern = doc.addObject('PartDesign::PolarPattern', 'Pattern')\n"
    "body.addObject(pattern)\n"
    "pattern.Originals = [doc.getObject('Pocket')]\n"
    "pattern.Axis = (doc.getObject('Z_Axis'), [''])\n"
    "pattern.Occurrences = 3\n"
    "doc.recompute()\n"
)


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_pattern_the_tip_never_reaches_fails_and_names_the_tip(kernel):
    """FreeCAD's Body.addObject moves the Tip onto a Pad but not onto a PolarPattern; a recorded run probed the uncut Body for nine minutes."""
    assert kernel.seed(rules=part_rules).ok
    assert kernel.rehearse(PADDED_WITH_A_HOLE).ok

    result = kernel.rehearse(PATTERNED)

    assert not result.ok
    assert "body.Tip = doc.getObject('Pattern')" in result.traceback


def test_a_pattern_made_the_tip_lands(kernel):
    assert kernel.seed(rules=part_rules).ok
    assert kernel.rehearse(PADDED_WITH_A_HOLE).ok

    result = kernel.rehearse(PATTERNED + "body.Tip = pattern\ndoc.recompute()\n")

    assert result.ok, result.traceback
