"""
Level 2 -- a proposed Trap's claim proved or disproved in a real throwaway
kernel, per FCC-314's own TESTING STRATEGY.
"""
import pytest

from core.freecad.freecad_tools import TRAPS
from core.kernel import find_freecadcmd
from core.freecad.traps import Trap, verify_seed_traps, verify_trap

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
    """AC3: verified-pass, verified-fail or unverified for each of the 15+ seed traps."""
    results = verify_seed_traps()

    assert len(results) == len(TRAPS)
    assert all(r.status in ("verified-pass", "verified-fail", "unverified") for r in results)
    assert all(r.status == "unverified" for r in results), (
        "the seed traps are prose, not scripts -- this run is their first exercise, "
        "not a claim that they were reproduced"
    )
