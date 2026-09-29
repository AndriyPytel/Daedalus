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
