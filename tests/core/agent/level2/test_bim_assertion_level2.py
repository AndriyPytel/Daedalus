"""
Level 2 -- the BIM Assertion measured on real Arch objects in headless FreeCAD, and the IFC4 it writes reopened in ifcopenshell.
"""
import pytest

from core.agent.bim import measured, refusals, unfinished
from core.kernel import Kernel, find_freecadcmd
from tests.bim_helpers import FLAT, NO_BUILDING, STRAY_WALL, UNHOSTED_WINDOW, WITHOUT_SPACE, ZERO_HEIGHT_WALL

pytestmark = pytest.mark.freecad

CASES = {
    "no space": (WITHOUT_SPACE, "no Space"),
    "unhosted window": (UNHOSTED_WINDOW, "host"),
    "zero-height wall": (ZERO_HEIGHT_WALL, "height"),
    "no building": (NO_BUILDING, "Building"),
    "wall outside the building": (STRAY_WALL, "not under"),
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


def test_a_valid_flat_passes_and_its_ifc4_has_every_kind(kernel):
    assert kernel.check(FLAT).ok

    facts = measured(kernel)

    assert refusals(facts) == [] and unfinished(kernel) is None
    assert facts["ifc"]["schema"] == "IFC4"
    assert all(facts["ifc"][kind] >= 1 for kind in ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcSpace"))


@pytest.mark.parametrize("name", CASES)
def test_a_flat_that_breaks_one_condition_is_refused_with_the_reason(kernel, name):
    code, reason = CASES[name]
    _fresh(kernel)
    assert kernel.check(code).ok

    found = unfinished(kernel)

    assert found is not None and reason in found, found
    assert "BIM Assertion" in found


def test_measuring_leaves_the_document_as_it_was(kernel):
    _fresh(kernel)
    assert kernel.check(FLAT).ok
    before = kernel.check("print(len(doc.Objects))").stdout

    measured(kernel)

    assert kernel.check("print(len(doc.Objects))").stdout == before
