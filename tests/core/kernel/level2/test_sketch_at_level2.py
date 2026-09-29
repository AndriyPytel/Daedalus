"""
Level 2 -- `sketch_at` puts a sketch at a global coordinate along an axis
whatever the sign of the Origin plane's own normal (XZ_Plane's is -Y).
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

AXES = {"x": 0, "y": 1, "z": 2}


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


@pytest.mark.parametrize("normal", sorted(AXES))
@pytest.mark.parametrize("at", [37, -130])
def test_a_circle_at_local_origin_lands_at_the_requested_coordinate(worker, normal, at):
    code = (
        "import Part\n"
        "body = doc.addObject('PartDesign::Body', 'Body')\n"
        "sketch = sketch_at(body, 'S', %r, %r)\n"
        "sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), False)\n"
        "doc.recompute()\n"
        "centre = sketch.Placement.multVec(App.Vector(0, 0, 0))\n"
        "held = [centre.x, centre.y, centre.z, sketch.Label]\n"
    ) % (normal, at)

    result = worker.check(code, None)

    assert result.ok, result.traceback
    expected = [0.0, 0.0, 0.0]
    expected[AXES[normal]] = at
    assert result.held[:3] == pytest.approx(expected, abs=1e-6)
    assert result.held[3] == "S"


def test_an_unknown_normal_is_refused(worker):
    code = "body = doc.addObject('PartDesign::Body', 'Body')\nsketch_at(body, 'S', 'w', 1)\n"

    assert not worker.check(code, None).ok
