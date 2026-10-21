"""Where nobody answers, Plan runs at low effort and a gap in its coverage goes to `unmet` instead of back to Plan."""
from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop.gate import nobody_to_ask
from core.loop.plan import plan
from core.nobody import nobody_answering
from tests.loop_helpers import (
    A_PART,
    a_person_who_declines,
    code_reply,
    design_reply,
    drive,
    nothing_to_add,
    plan_reply,
    suspect_reply,
    verdict_reply,
)


class _Dial(ScriptedLLMClient):
    def __init__(self, turns, effort="medium"):
        super().__init__(turns)
        self.effort = effort
        self.seen = []

    def stream(self, messages, **overrides):
        self.seen.append(self.effort)
        return super().stream(messages, **overrides)


def test_a_plan_call_runs_at_low_effort_only_where_nobody_answers_and_the_original_is_untouched():
    llm = _Dial([plan_reply("Pad it")])
    with nobody_answering():
        plan("x", {}, llm, EventStream())
    kept = _Dial([plan_reply("Pad it")])
    plan("x", {}, kept, EventStream())

    assert llm.seen == ["low"] and llm.effort == "medium"
    assert kept.seen == ["medium"]


def test_a_gap_in_coverage_is_unmet_not_a_revision_where_nobody_answers():
    result, events, _, _ = drive(
        [design_reply(A_PART), plan_reply("Pad the outline 10 mm"), code_reply("pad()"),
         nothing_to_add()], ask=nobody_to_ask)

    assert result.ok and result.unmet
    assert not [e for e in events if e.kind == "plan_revised"]


def test_a_gap_in_coverage_is_still_sent_back_to_plan_when_a_person_answers():
    _, events, _, _ = drive(
        [design_reply(A_PART), plan_reply("Pad the outline 10 mm"),
         plan_reply("Pad the outline 10 mm"), code_reply("pad()"), suspect_reply(),
         verdict_reply("pass")], ask=a_person_who_declines)

    assert [e for e in events if e.kind == "plan_revised"]
