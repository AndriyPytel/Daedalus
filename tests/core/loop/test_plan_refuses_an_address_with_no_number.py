"""Loop level -- a Step citing an address Design left without its number is a Plan complaint, not a KeyError."""
import json

from core.events import PLAN_REVISED
from core.loop.budgets import Budgets
from core.loop.gate import nobody_to_ask
from core.loop.plan import _cited_without_a_number

from tests.loop_helpers import (request_in, nothing_to_add, RENDER_PNG, _grown, assertion_reply, code_reply, design_reply, drive, plan_reply,
                                read, suspect_reply, verdict_reply)
from tests.event_helpers import events_of

LOST = {"value": "to be read again off the later page", "flag": "assumed",
        "because": "the face went with the feature the later page deleted"}
A_HOLE = {
    "envelope": {"length": dict(read("80 mm"), mm=80)},
    "features": [{"id": "hole-b", "kind": "hole", "depth": LOST}],
}
DEPTH = "features \u2192 hole-b \u2192 depth"
LENGTH = "envelope \u2192 length"


def test_an_address_with_no_number_is_named_when_a_step_cites_it():
    part = _grown([A_HOLE])

    assert _cited_without_a_number(part, [[LENGTH, DEPTH]]) == [DEPTH]
    assert _cited_without_a_number(part, [[LENGTH]]) == []


def test_an_address_with_its_number_is_never_named():
    kept = _grown([{"envelope": A_HOLE["envelope"],
                    "features": [{"id": "hole-b", "kind": "hole", "depth": dict(LOST, mm=12)}]}])

    assert _cited_without_a_number(kept, [[LENGTH, DEPTH]]) == []


def test_a_step_citing_a_numberless_address_gets_a_patch_naming_it_and_no_exception():
    result, events, llm, _ = drive([
        design_reply(A_HOLE),
        plan_reply({"do": "Drill", "satisfies": [LENGTH, DEPTH]}),
        plan_reply({"do": "Drill", "satisfies": [LENGTH]}),
        code_reply("a()"), suspect_reply(), assertion_reply(),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(mechanical_revisions=0))

    told = request_in(llm.calls[2])["what_went_wrong_last_time"]
    assert result.ok, result.reason
    assert DEPTH in told and "no number" in told
    assert events_of(events, PLAN_REVISED)[0].data["revision"] == 0


def test_where_nobody_answers_the_address_is_dropped_from_the_citations_and_the_step_is_kept():
    held = dict(LOST, was="12 mm")
    result, events, _, tools = drive([
        design_reply({"envelope": A_HOLE["envelope"],
                      "features": [{"id": "hole-b", "kind": "hole", "depth": held}]}),
        plan_reply({"do": "Drill", "satisfies": [LENGTH, DEPTH]}),
        code_reply("a()"), nothing_to_add(),
    ], attachments=[RENDER_PNG], ask=nobody_to_ask)

    assert result.ok, result.reason
    assert not events_of(events, PLAN_REVISED)
    assert tools.applied_codes() == ["a()"]
