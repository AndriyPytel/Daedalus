"""Where nobody answers, a fillet, chamfer or draft gets one attempt and is left out when it fails, without a revision."""
from core.events import PLAN_REVISED, UNBUILT
from tests.event_helpers import events_of
from tests.loop_helpers import (
    code_reply,
    drive,
    failed,
    plan_reply,
    suspect_reply,
    verdict_reply,
)


def test_a_round_that_fails_once_is_left_out_and_the_run_goes_on_unrevised():
    result, events, _, tools = drive(
        [plan_reply("Pad the outline 10 mm", "Fillet the four corner edges at R2"),
         code_reply("pad()", "fillet()"), suspect_reply(), verdict_reply("pass")],
        applies=[None, failed("Fillet (Part::Fillet): FilletId is not part of the shape")])

    assert result.ok and result.steps_applied == 1
    assert [event.data["step"] for event in events_of(events, UNBUILT)] == [2]
    assert not events_of(events, PLAN_REVISED)
    assert tools.applied_codes() == ["pad()", "fillet()"]
