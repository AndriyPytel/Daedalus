"""
The recorded Step 8 of 127: Thickness reports "Failed to make thick solid" and
the Step is handed back. Plan is given an alternative construction of the same
wall, because a Plan told only that the Step failed asks for the shell again.
"""
import json

from core.events import PLAN_REVISED
from core.loop.execute import _FAILED_THIS_WAY

from tests.loop_helpers import _turns_of, drive, plan_reply
from tests.event_helpers import events_of

_HANDBACK = ("PartDesign::Thickness reports 'Failed to make thick solid' at 1, 2, 3 "
             "and 4 mm and with only the bottom face removed")


def _the_plan_call_after(handback):
    _, events, llm, _ = drive([
        plan_reply("Shell the cover to a wall"),
        json.dumps({"declare": {"unbuildable": handback}}),
        plan_reply("Rebuild the wall"),
        json.dumps({"declare": {"unbuildable": "still nothing"}}),
    ])
    assert len(events_of(events, PLAN_REVISED)) == 1
    return json.dumps(_turns_of(llm, "plan")[-1])


def test_a_failed_thickness_is_in_the_phrase_table():
    phrases = [phrase for phrase, _, _ in _FAILED_THIS_WAY]
    assert "Failed to make thick solid" in phrases


def test_the_alternative_construction_reaches_the_next_plan_call():
    sent = _the_plan_call_after(_HANDBACK)
    assert "offset by the wall thickness" in sent
    assert "Boolean cut" in sent


def test_another_hand_back_does_not_carry_the_thickness_directive():
    sent = _the_plan_call_after("the arm cannot reach the tangent plane")
    assert "offset by the wall thickness" not in sent
