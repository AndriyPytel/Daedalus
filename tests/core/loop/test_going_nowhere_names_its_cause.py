"""The `going_nowhere` block, closed with what this phase's kernel actually said."""
from core.events import TOOL_RESULT, EventStream
from core.ledger import Ledger
from core.loop.execute import _FAILED_THIS_WAY
from core.loop.helpers import _going_nowhere, _last_kernel_result


def test_a_step_never_tried_this_phase_with_no_events_leaves_no_kernel_result():
    """No attempt was ever declared for the stuck Step and nothing else this
    phase touched the kernel, so there is no kernel result to weigh."""
    assert _last_kernel_result([], EventStream()) is None


def test_the_last_tried_attempt_this_phase_is_the_kernel_result():
    """`outcome["tried"]`'s own last entry is Preflight or apply's own
    traceback for the Step that was handed back -- the phase's most direct
    account, read before the event stream is."""
    tried = [{"step": "Pad the rib", "code": "pad()",
              "traceback": "recompute left these objects in error: Pad"}]

    assert _last_kernel_result(tried, EventStream()) == \
        "recompute left these objects in error: Pad"


def test_an_earlier_steps_success_leaves_no_kernel_result_for_the_stuck_one():
    """An earlier Step's own `apply_step` landed clean -- the most recent
    tool result this phase produced was a success, so the stuck Step (never
    itself declared) inherits no refusal from it."""
    events = EventStream()
    events.emit(TOOL_RESULT, name="apply_step", result={"status": "applied", "created": ["Pad"]})

    assert _last_kernel_result([], events) is None


def test_the_phases_own_refusal_off_the_event_stream_is_the_kernel_result():
    """Run A's own shape: Execute never got as far as `apply_step` -- the
    phase itself refused the code turn for the sequencing gap, off
    `stream.emit(TOOL_RESULT, name=phase, ...)` in `core/agent/agent.py`,
    and that is what `_last_kernel_result` reads back with no attempt on
    record in `outcome["tried"]`."""
    events = EventStream()
    events.emit(TOOL_RESULT, name="execute",
                result={"ok": False, "status": "refused", "step": 15,
                        "traceback": "Step 14 has neither landed nor been declared. "
                                     "Write Step 14 first."})

    assert _last_kernel_result([], events) == \
        "Step 14 has neither landed nor been declared. Write Step 14 first."


def test_a_kernel_result_is_quoted_and_the_description_is_not_blamed():
    """AC1: the last kernel result this phase had for the Step is a refusal,
    so the block quotes it and does not tell the user to correct the
    description."""
    reason = _going_nowhere(
        "Step 15 of 29 could not be built as written: refused by the kernel",
        "Step 14 has neither landed nor been declared. Write Step 14 first.")

    assert "Step 14 has neither landed nor been declared" in reason
    assert "Correct the description" not in reason


def test_a_named_description_conflict_still_asks_for_a_correction():
    """AC2: no kernel result is on record for the stuck Step, so the
    hand-back's own account of a description conflict still gets the
    correct-the-description advice."""
    reason = _going_nowhere(
        "Step 10 of 29 could not be built as written: the Step's diameter and the "
        "Document's HoleLeft axis disagree, a description conflict.",
        None)

    assert "Correct the description" in reason


def test_an_unrelated_ledger_entry_does_not_change_a_named_description_conflict():
    """A run-scoped `Ledger` can hold an earlier, unrelated Step's rejection
    for the whole run -- that must not suppress the advice for a later Step
    whose own hand-back names a description conflict and never touched the
    kernel itself. `_going_nowhere` is never handed the `Ledger` at all; this
    proves the earlier entry changes nothing for it."""
    ledger = Ledger(_FAILED_THIS_WAY)
    ledger.record("Pad the base", "recompute left these objects in error: Pad")
    assert ledger

    kernel_result = _last_kernel_result([], EventStream())
    reason = _going_nowhere(
        "Step 2 of 2 could not be built as written: the bore's axis and the "
        "envelope's length disagree, a description conflict.",
        kernel_result)

    assert "Correct the description" in reason
    assert "Pad" not in reason
