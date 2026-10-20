"""Where nobody answers, a Step whose citations disagree about the plane is kept and logged, not sent back with the whole Plan."""
from core.loop.gate import nobody_to_ask
from tests.event_helpers import events_of
from tests.loop_helpers import (
    RENDER_PNG,
    THE_CLAMP_OUTLINE,
    _both_planes_in_one_step,
    _turns_of,
    code_reply,
    design_reply,
    drive,
)
from core.events import ANSWER


def test_a_step_citing_two_planes_is_built_as_planned_and_the_plan_is_asked_for_once():
    _, events, llm, tools = drive(
        [design_reply(THE_CLAMP_OUTLINE), _both_planes_in_one_step(), code_reply("profile()")],
        attachments=[RENDER_PNG], ask=nobody_to_ask)

    assert len(_turns_of(llm, "plan")) == 1
    assert tools.applied_codes() == ["profile()"]
    assert any("2 different values of `plane`" in answer.data["text"]
               for answer in events_of(events, ANSWER))
