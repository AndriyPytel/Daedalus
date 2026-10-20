"""Where nobody answers, a Step that cannot be built is left out and the Steps after it are still tried."""
import json

from core.events import PLAN_REVISED, UNBUILT
from core.loop.gate import nobody_to_ask
from tests.event_helpers import events_of
from tests.loop_helpers import code_reply, drive, plan_reply, suspect_reply, verdict_reply


def _step_one_then_unbuildable(reason):
    return [json.dumps({"code": "pad()", "step": 1, "label": "Step"}),
            json.dumps({"declare": {"steps": [{"step": 1, "label": "Step"}],
                                    "unbuildable": reason}})]


def test_a_step_nothing_can_build_is_left_out_and_the_next_one_is_still_built():
    result, events, _, tools = drive(
        [plan_reply("Pad the outline", "Pocket the slot", "Pocket the hole"),
         *_step_one_then_unbuildable("Step 2 has no face to sit on"),
         code_reply("hole()", first=3), suspect_reply(), verdict_reply("pass")],
        ask=nobody_to_ask)

    assert result.ok and result.steps_applied == 2
    assert tools.applied_codes() == ["pad()", "hole()"]
    assert [event.data["step"] for event in events_of(events, UNBUILT)] == [2]
    assert not events_of(events, PLAN_REVISED)


def test_a_run_in_which_no_step_can_be_built_is_blocked():
    result, _, _, _ = drive(
        [plan_reply("Pocket the slot"),
         json.dumps({"declare": {"unbuildable": "there is nothing to cut"}})],
        ask=nobody_to_ask)

    assert result.blocked and result.steps_applied == 0
    assert "nothing to cut" in result.reason
