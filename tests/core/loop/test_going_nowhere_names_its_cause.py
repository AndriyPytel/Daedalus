"""The `going_nowhere` block, closed with what the run actually knows about the Step."""
from core.ledger import Ledger
from core.loop.execute import _FAILED_THIS_WAY
from core.loop.helpers import _going_nowhere


def test_a_ledger_refusal_is_quoted_and_the_description_is_not_blamed():
    """AC1: the last kernel result was a ledger refusal, so the block quotes
    it and does not tell the user to correct the description."""
    ledger = Ledger(_FAILED_THIS_WAY)
    ledger.record("Step 15", "recompute left these objects in error: FilletWindowRing")

    reason = _going_nowhere("Step 15 of 29 could not be built as written: refused by the kernel",
                            ledger)

    assert ledger.render() in reason
    assert "Correct the description" not in reason


def test_a_named_description_conflict_still_asks_for_a_correction():
    """AC2: no kernel refusal is on record, so the hand-back's own account
    of a description conflict still gets the correct-the-description advice."""
    ledger = Ledger(_FAILED_THIS_WAY)

    reason = _going_nowhere(
        "Step 10 of 29 could not be built as written: the Step's diameter and the "
        "Document's HoleLeft axis disagree, a description conflict.",
        ledger)

    assert "Correct the description" in reason
