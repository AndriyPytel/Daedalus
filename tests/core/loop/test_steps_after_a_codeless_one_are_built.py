"""Where nobody answers, Steps Execute wrote code for beyond a Step it passed over are built, not discarded and asked for again."""
import json

from core.events import PLAN_REVISED, UNBUILT
from core.loop.gate import nobody_to_ask
from tests.event_helpers import events_of
from tests.loop_helpers import drive, nothing_to_add, plan_reply


def _code(step):
    return json.dumps({"code": "s%d()" % step, "step": step, "label": "Step"})


def test_the_steps_after_two_left_out_ones_are_built_and_not_asked_for_again():
    result, events, _, tools = drive(
        [plan_reply("Pad the outline 10 mm", "Fillet the corner edges at R2", "Fillet the top edges at R1",
                    "Pad the boss 5 mm", "Pad the lug 3 mm"),
         _code(1),
         json.dumps({"code": "s4()", "step": 4, "label": "Step",
                     "leaves_out": {"2": "no edge", "3": "no edge"}}),
         _code(5), json.dumps({"declare": {"steps": []}}), nothing_to_add()],
        ask=nobody_to_ask)

    assert result.ok and result.steps_applied == 3
    assert tools.applied_codes() == ["s1()", "s4()", "s5()"]
    assert [(e.data["step"], e.data["why"]) for e in events_of(events, UNBUILT)] == \
        [(2, "no edge"), (3, "no edge")]
    assert not events_of(events, PLAN_REVISED)
