"""
The second Design pass: the part goes back to the drawing once before Plan, in a run with nobody to ask and in one with a person alike.
"""
from dataclasses import replace

from core.events import ANSWER
from core.loop.budgets import Budgets
from core.loop.prompt import nobody_answering, prompt
from tests.event_helpers import events_of
from tests.loop_helpers import (
    A_PART,
    assertion_reply,
    RENDER_PNG,
    _asked_of_design,
    code_reply,
    design_reply,
    drive,
    second_pass_reply,
    everything,
    plan_reply,
    settled,
    suspect_reply,
    verdict_reply,
)

A_GUESS = {"features": [{"id": "hub", "kind": "hub", "diameter": settled("230 mm", "no callout")}]}

THE_PART = {**A_PART, **A_GUESS}

AFTER = [plan_reply("Only", satisfies=everything(THE_PART)), code_reply("box()"),
         suspect_reply(), assertion_reply(), verdict_reply("pass")]


def _the_drawing_answered(events):
    return [answer for answer in events_of(events, ANSWER) if answer.data["by"] == "drawing"]


def test_a_headless_run_goes_back_to_the_drawing_with_its_open_gaps():
    _, events, llm, _ = drive([design_reply(THE_PART),
                               second_pass_reply({"features": [{"id": "lobe", "kind": "R30 lobe"}]}),
                               *AFTER], attachments=[RENDER_PNG])

    second = _asked_of_design(llm)[1]["the_question"]
    assert "hub" in second and "230 mm" in second
    assert "measure" in second and "cross-check the views" in second
    assert len(_the_drawing_answered(events)) == 1


def test_an_interactive_run_asks_a_person_only_for_what_the_second_pass_left_open():
    asked = []
    drive([design_reply(THE_PART, question="How thick is the hub?"),
           second_pass_reply({"features": [{"id": "lobe", "kind": "R30 lobe"}]}), *AFTER],
          attachments=[RENDER_PNG], ask=lambda question: asked.append(question) or "")
    assert asked == []

    drive([design_reply(THE_PART, question="How thick is the hub?"), design_reply(),
           second_pass_reply(), *AFTER], attachments=[RENDER_PNG],
          ask=lambda question: asked.append(question) or "5 mm")
    assert asked == ["How thick is the hub?"]

    _, _, llm, _ = drive([design_reply(THE_PART), second_pass_reply(), *AFTER],
                         attachments=[RENDER_PNG], ask=lambda question: asked.append(question))
    assert len(_asked_of_design(llm)) == 2
    assert asked == ["How thick is the hub?"]


def test_a_run_with_no_drawing_makes_no_second_pass():
    result, events, llm, _ = drive([design_reply(THE_PART), *AFTER])

    assert len(_asked_of_design(llm)) == 1
    assert _the_drawing_answered(events) == []
    assert result.ok, result.reason


def test_the_second_pass_is_bounded_by_the_rereads_budget():
    def design_calls(rereads):
        _, _, llm, _ = drive([design_reply(THE_PART), second_pass_reply(), *AFTER],
                             attachments=[RENDER_PNG], budgets=replace(Budgets(), rereads=rereads))
        return len(_asked_of_design(llm))

    assert design_calls(0) == 1
    assert design_calls(1) == 2


def test_both_design_prompts_say_to_settle_only_what_the_drawing_does_not_give():
    rule = "settle a value yourself only for what the drawing does not give"
    with nobody_answering():
        headless = prompt("design")

    assert rule in headless.lower() and rule in prompt("design").lower()
    assert "`because`" in headless
