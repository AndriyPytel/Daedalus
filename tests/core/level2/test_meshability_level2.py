"""
Level 2 -- the grader's mesh timeout, caught before `finish`.

`steps/149.step` is a BRepCheck-valid, watertight solid whose sub-millimetre fillets and chamfers make
the grader's mesher run past its 180 s timeout, which scores a valid part 0. The other five are
recorded STEPs that scored above 0.9 and must pass.
"""
import pytest

pytestmark = pytest.mark.freecad

Part = pytest.importorskip("FreeCAD") and pytest.importorskip("Part")

import os

STEPS = os.path.join(os.path.dirname(__file__), "steps")
SCORED_WELL = ("132", "128", "147", "109", "105")


def _read(name):
    return Part.read(os.path.join(STEPS, name + ".step"))


def test_the_step_the_grader_cannot_mesh_is_refused():
    from core.manifold import unmeshable_fc

    assert unmeshable_fc(_read("149"), limit=10) is not None


def test_the_refusal_names_the_sub_millimetre_features():
    from core.manifold import unmeshable_fc

    message = unmeshable_fc(_read("149"), limit=10)

    assert "sub-millimetre fillets and chamfers" in message
    assert "mm wide at (" in message


@pytest.mark.parametrize("name", SCORED_WELL)
def test_a_step_that_scored_well_is_not_refused(name):
    from core.manifold import unmeshable_fc

    assert unmeshable_fc(_read(name)) is None
