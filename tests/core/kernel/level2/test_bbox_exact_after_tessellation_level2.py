"""
Level 2 -- `bbox()` against a shape that has been tessellated for display,
the discrepancy `FCC-458` names between Preflight (headless kernel, never
tessellated) and Apply (GUI Document, already tessellated for the 3D view).
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


def test_bbox_matches_the_exact_extents_after_tessellation(kernel):
    """AC1: bbox(shape) on a tessellated shape holds within 1e-6 of the exact extents."""
    code = """
shape = Part.makeCylinder(15, 10)
shape.tessellate(0.5)
box = bbox(shape)
assert abs(box.XLength - 30.0) < 1e-6, box.XLength
assert abs(box.YLength - 30.0) < 1e-6, box.YLength
assert abs(box.ZLength - 10.0) < 1e-6, box.ZLength
"""
    result = kernel.check(code)

    assert result.ok, result.traceback
