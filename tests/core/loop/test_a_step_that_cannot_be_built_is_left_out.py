"""Where nobody answers, the material Steps Execute cannot build in a pass go back to Plan once, together, and are left out only if they still fail."""
import json

from core.events import PLAN_REVISED, UNBUILT
from core.loop.gate import nobody_to_ask
from tests.event_helpers import events_of
from tests.loop_helpers import (
    code_reply,
    drive,
    plan_reply,
)


def _step_one_then_unbuildable(reason):
    return [json.dumps({"code": "pad()", "step": 1, "label": "Step"}),
            json.dumps({"declare": {"steps": [{"step": 1, "label": "Step"}],
                                    "unbuildable": reason}})]


def test_the_steps_nothing_could_build_go_back_to_plan_once_with_their_reasons_and_are_rebuilt():
    result, events, llm, tools = drive(
        [plan_reply("Pad the outline", "Pocket the slot", "Pocket the hole"),
         *_step_one_then_unbuildable("the slot has no face to sit on"),
         code_reply("hole()", first=3),
         plan_reply("Pocket the slot from the top face"),
         code_reply("slot()", first=3)],
        ask=nobody_to_ask)

    assert result.ok and result.steps_applied == 3
    assert tools.applied_codes() == ["pad()", "hole()", "slot()"]
    revisions = events_of(events, PLAN_REVISED)
    assert len(revisions) == 1 and "the slot has no face to sit on" in revisions[0].data["reason"]
    assert "Pocket the slot" in revisions[0].data["reason"]
    assert not events_of(events, UNBUILT)


def test_a_step_that_still_cannot_be_built_after_the_one_revision_is_left_out():
    result, events, _, tools = drive(
        [plan_reply("Pad the outline", "Pocket the slot"),
         *_step_one_then_unbuildable("no face"),
         plan_reply("Pocket the slot from the top face"),
         json.dumps({"declare": {"unbuildable": "still no face"}})],
        ask=nobody_to_ask)

    assert result.ok and result.steps_applied == 1
    assert len(events_of(events, PLAN_REVISED)) == 1
    assert [event.data["why"] for event in events_of(events, UNBUILT)] == ["still no face"]


def test_a_run_in_which_no_step_can_be_built_is_blocked_after_the_one_revision():
    result, events, _, _ = drive(
        [plan_reply("Pocket the slot"),
         json.dumps({"declare": {"unbuildable": "there is nothing to cut"}}),
         plan_reply("Pocket the slot at the top"),
         json.dumps({"declare": {"unbuildable": "there is nothing to cut"}})],
        ask=nobody_to_ask)

    assert result.blocked and result.steps_applied == 0
    assert "nothing to cut" in result.reason
    assert len(events_of(events, PLAN_REVISED)) == 1
