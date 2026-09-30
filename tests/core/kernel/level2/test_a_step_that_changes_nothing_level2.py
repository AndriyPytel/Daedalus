"""
Level 2 -- a Step or a fix that changes nothing in the Document is not a
Step: a recorded run delivered a `print` as Step 4 and then had to write the
real Step 4 as a fix of it.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_building_turn_that_changes_nothing_fails(kernel):
    kernel.check("pass", document=None)

    result = kernel.check("print(doc.Objects)", builds=True)

    assert not result.ok
    assert "changed nothing" in result.traceback


def test_a_building_turn_that_adds_an_object_lands(kernel):
    kernel.check("pass", document=None)

    result = kernel.check("doc.addObject('Part::Box', 'Box')", builds=True)

    assert result.ok, result.traceback


def test_a_look_that_changes_nothing_is_still_fine(kernel):
    kernel.check("pass", document=None)

    assert kernel.check("print(doc.Objects)", exploring=True).ok


def test_a_building_turn_that_changes_nothing_fails_after_a_sketch_and_a_pad(kernel):
    """A recorded run landed a Step 7 that only printed, over a Body whose shapes read as new objects on every look."""
    kernel.check("pass", document=None)
    kernel.check(
        "import Part\n"
        "body = doc.addObject('PartDesign::Body', 'Body')\n"
        "sketch = sketch_at(body, 'Profile', 'z', 0)\n"
        "sketch.addGeometry(Part.Circle(App.Vector(), App.Vector(0, 0, 1), 10))\n"
        "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
        "body.addObject(pad)\n"
        "pad.Profile = sketch\n"
        "pad.Length = 5\n"
        "doc.recompute()\n", builds=True)

    result = kernel.check("print(doc.Objects)", builds=True)

    assert not result.ok
    assert "changed nothing" in result.traceback
