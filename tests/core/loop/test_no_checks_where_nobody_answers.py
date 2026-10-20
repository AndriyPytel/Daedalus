"""Where nobody answers, the run ends when its Steps are built: no suspect check, assertion or review."""
from core.events import ASSERTION, REVIEW_SUSPECTED, REVIEW_VERDICT
from core.loop.gate import nobody_to_ask
from tests.event_helpers import events_of
from tests.loop_helpers import code_reply, drive, plan_reply


def test_a_run_nobody_answers_ends_after_its_last_step_without_a_check():
    result, events, llm, _ = drive([plan_reply("Pad the outline 10 mm"), code_reply("pad()")],
                                   ask=nobody_to_ask)

    assert result.ok and result.verdict is None and result.steps_applied == 1
    assert not events_of(events, REVIEW_SUSPECTED) and not events_of(events, ASSERTION)
    assert not events_of(events, REVIEW_VERDICT)
