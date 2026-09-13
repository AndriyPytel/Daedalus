"""
Level 2 -- a proposed Trap's claim proved or disproved in a real throwaway
kernel, per FCC-314's own TESTING STRATEGY.
"""
import pytest

from core.freecad_apply import RETIRED_TRAPS, SEED_TRAPS, TRAPS
from core.kernel import find_freecadcmd
from core.freecad_traps import Trap, verify_seed_traps, verify_trap

pytestmark = pytest.mark.freecad


@pytest.fixture(autouse=True)
def _skip_without_freecad():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))


def test_a_trap_whose_script_reproduces_its_claim_comes_back_verified_pass():
    trap = Trap(
        claim="Body.addObject hands back a list, not the object.",
        script=(
            "box = doc.addObject('Part::Box', 'Box')\n"
            "added = doc.addObject('Part::Feature', 'F')\n"
            "assert isinstance([added], list)"
        ),
    )

    evidence = verify_trap(trap)

    assert evidence.status == "verified-pass"
    assert evidence.result.ok
    assert evidence.trap is trap


def test_a_trap_whose_script_fails_to_reproduce_its_claim_comes_back_verified_fail():
    trap = Trap(
        claim="A claim this build does not actually hold.",
        script="assert False, 'this build does not reproduce the claim'",
    )

    evidence = verify_trap(trap)

    assert evidence.status == "verified-fail"
    assert not evidence.result.ok
    assert "this build does not reproduce" in evidence.result.traceback


def test_a_trap_with_no_script_is_marked_unverified_not_dropped():
    trap = Trap(claim="A trap that cannot be reduced to a runnable check.")

    evidence = verify_trap(trap)

    assert evidence.status == "unverified"
    assert evidence.result is None


def test_every_seed_trap_gets_a_result_none_silently_skipped():
    """AC2: verified-pass or verified-fail for each seed trap, one result per trap held."""
    results = verify_seed_traps()

    assert len(results) == len(SEED_TRAPS)
    assert all(r.status in ("verified-pass", "verified-fail") for r in results), (
        "a seed trap came back unverified -- every seed trap carries a script or is "
        "retired, so this run should never see the third label"
    )


def test_every_seed_trap_carries_a_script():
    """AC1: a seed trap without a script belongs in RETIRED_TRAPS, not here."""
    assert all(trap.script for trap in SEED_TRAPS)


def test_a_retired_trap_carries_the_reason_it_was_dropped():
    """AC1: a trap whose script could not be written is retired with why, not silently gone."""
    assert RETIRED_TRAPS
    for claim, reason in RETIRED_TRAPS:
        assert claim not in TRAPS
        assert reason


def test_the_fillet_trap_reproduces_the_fcc_388_mixed_rim_case():
    """AC4: a selection mixing a plate's outer rim and a hole's inner rim, which the
    old normals/length/plane heuristic cannot tell apart and Preflight refuses."""
    fillet_trap = next(trap for trap in SEED_TRAPS if "UseAllEdges" in trap.claim)

    evidence = verify_trap(fillet_trap)

    assert evidence.status == "verified-fail"
    assert "mixes concavities" in evidence.result.traceback
