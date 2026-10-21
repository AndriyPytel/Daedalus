"""
A replay whose total volume differs from Preflight's by float noise must
still land -- the same geometry computed twice in two OCC processes does not
round to the same sixth decimal, and `signature_of` already rounds before
this comparison ever runs.
"""
import pytest

from core.freecad_apply import SignatureMismatch, run_in_transaction
from core.kernel import Kernel, find_freecadcmd
from core.kernel.kernel_worker import signature_of

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def _freecadcmd():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def doc(_freecadcmd):
    import FreeCAD

    document = FreeCAD.newDocument("SignatureToleranceLevel2")
    document.UndoMode = 1
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


def test_a_volume_difference_of_float_noise_still_lands(doc):
    doc.addObject("Part::Box", "Box")
    doc.recompute()

    expected = signature_of(doc)
    expected["volume"] += expected["volume"] * 5e-10

    run_in_transaction(doc, "pass", names={}, expected_signature=expected)

    assert len(doc.Objects) == 1


def test_a_volume_difference_past_the_tolerance_still_aborts(doc):
    probe = doc.addObject("Part::Box", "Box")
    probe.recompute()
    expected = signature_of(doc)
    expected["volume"] += expected["volume"] * 1e-3
    doc.removeObject("Box")

    with pytest.raises(SignatureMismatch):
        run_in_transaction(doc, "doc.addObject('Part::Box', 'Box')", names={},
                           expected_signature=expected)

    assert doc.Objects == []


def test_the_drift_a_live_fillet_replay_measured_still_matches():
    """
    Two OCC processes built the same fillet 4.4e-3 mm3 apart on a 275,674
    mm3 part, a relative 1.6e-8 with count, names and links identical.
    """
    from core.kernel.kernel_worker import signatures_match

    same = {"count": 3, "names": ["Body", "Fillet", "Pad"], "links": {}}
    assert signatures_match(dict(same, volume=275673.872855),
                            dict(same, volume=275673.877298))


def test_a_relative_difference_of_7e_5_matches_and_1e_3_does_not():
    from core.kernel.kernel_worker import signatures_match

    same = {"count": 3, "names": ["Body", "Fillet", "Pad"], "links": {}}
    assert signatures_match(dict(same, volume=1000000.0), dict(same, volume=1000000.0 * (1 + 7.8e-5)))
    assert not signatures_match(dict(same, volume=1000000.0), dict(same, volume=1000000.0 * (1 + 1e-3)))
