"""Loop level -- a gap in the Plan's bookkeeping is a free patch, and a repeat ends as unmet."""
import json

from core.events import PLAN_REVISED
from core.loop.budgets import Budgets
from core.loop.plan import _unconsumed

from tests.loop_helpers import (RENDER_PNG, _grown, assertion_reply, code_reply, design_reply, drive, plan_reply,
                                read, suspect_reply, verdict_reply)

A_FILLET = {
    "envelope": {"length": read("80 mm")},
    "features": [{"id": "fillet-bottom-centre", "kind": "fillet", "radius": read("R4 mm"),
                  "edges": read("1"), "plane": read("the top face"),
                  "angle": read("90 degrees"),
                  "position": {"the front face": read("20 mm")}}],
}
ADDRESS = "features \u2192 fillet-bottom-centre \u2192 "
RADIUS = ADDRESS + "radius"
BOOKKEEPING = [ADDRESS + leaf for leaf in ("edges", "plane", "angle", "position \u2192 the front face")]
LENGTH = "envelope \u2192 length"


def _the_description():
    return _grown([A_FILLET])


def test_a_step_citing_the_radius_covers_the_features_bookkeeping_addresses():
    """Plan 120 cited a fillet's radius and was refused for the edges, plane, angle and position."""
    assert _unconsumed(_the_description(), [LENGTH, RADIUS]) == []


def test_a_step_citing_only_bookkeeping_leaves_the_radius_uncovered():
    """Dimensional values with their own number are cited one by one."""
    assert _unconsumed(_the_description(), [LENGTH] + BOOKKEEPING) == [RADIUS]


def test_a_bookkeeping_citation_of_another_feature_covers_nothing_here():
    assert ADDRESS + "edges" in _unconsumed(_the_description(),
                                            [LENGTH, "features \u2192 other \u2192 radius"])


def test_an_uncovered_address_is_patched_without_spending_a_mechanical_revision():
    """Plan 120's first Plan left the radius out; the patch cost the run nothing."""
    result, events, llm, _ = drive([
        _design(),
        plan_reply({"do": "Fillet", "satisfies": [LENGTH] + BOOKKEEPING}),
        plan_reply({"do": "Fillet", "satisfies": [LENGTH] + BOOKKEEPING},
                   {"do": "Fillet R4 mm", "satisfies": [RADIUS]}),
        code_reply("a()", "b()"), suspect_reply(), assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[_png()], budgets=Budgets(mechanical_revisions=0))

    assert result.ok, result.reason
    patched = events.of(PLAN_REVISED)
    assert len(patched) == 1
    assert patched[0].data["revision"] == 0


def test_the_patch_is_asked_for_the_uncovered_addresses_only_and_as_an_addition():
    _, _, llm, _ = drive([
        _design(),
        plan_reply({"do": "Fillet", "satisfies": [LENGTH] + BOOKKEEPING}),
        plan_reply({"do": "Fillet", "satisfies": [LENGTH] + BOOKKEEPING},
                   {"do": "Fillet R4 mm", "satisfies": [RADIUS]}),
        code_reply("a()", "b()"), suspect_reply(), assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[_png()])

    told = json.loads(llm.calls[2][-1]["content"])["what_went_wrong_last_time"]
    assert RADIUS in told
    assert not any(address in told for address in BOOKKEEPING + [LENGTH])
    assert "`satisfies` of an existing Step" in told and "end of the Plan" in told


def test_an_address_still_uncovered_after_its_patch_ends_as_unmet_and_the_run_builds():
    """Loop safety: one patch, one repeat, then the Plan is accepted."""
    never = plan_reply({"do": "Fillet", "satisfies": [LENGTH] + BOOKKEEPING})
    result, events, _, tools = drive([
        _design(), never, never, never, never,
        code_reply("a()"), suspect_reply(), assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[_png()], budgets=Budgets(mechanical_revisions=0))

    assert result.ok, result.reason
    assert len(events.of(PLAN_REVISED)) == 1
    assert result.unmet == [RADIUS]
    assert tools.applied_codes() == ["a()"]


def test_a_rollback_deeper_than_the_undo_stack_is_a_patch_that_spends_no_revision():
    """Runs 102, 108 and 116 lost a revision to the stack depth."""
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
               for n in range(1, 7)]
    result, events, _, tools = drive([
        plan_reply(*("Step %d" % n for n in range(1, 6))),
        *[json.dumps({"code": "s%d()" % n, "step": n, "label": "Step"}) for n in range(1, 6)],
        json.dumps({"declare": {"steps": [{"step": n, "label": "Step"} for n in range(1, 6)]}}),
        suspect_reply(),
        verdict_reply("fail", "Step 1's footprint is wrong"),
        plan_reply(rollback={"to": 1, "why": "the footprint is wrong from the start"}),
        plan_reply("Fix it"),
        json.dumps({"code": "fix()", "step": 6, "label": "Step"}),
        json.dumps({"declare": {"steps": [{"step": 6, "label": "Step"}]}}),
        suspect_reply(), verdict_reply("pass"),
    ], applies=applies, undo_capacity=2, budgets=Budgets(revisions=1, mechanical_revisions=0))

    assert result.ok, result.reason
    revised = events.of(PLAN_REVISED)
    assert [e.data["revision"] for e in revised] == [1, 1]


def test_plan_is_told_the_real_undo_depth_up_front():
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
               for n in range(1, 7)]
    _, _, llm, _ = drive([
        plan_reply(*("Step %d" % n for n in range(1, 4))),
        *[json.dumps({"code": "s%d()" % n, "step": n, "label": "Step"}) for n in range(1, 4)],
        json.dumps({"declare": {"steps": [{"step": n, "label": "Step"} for n in range(1, 4)]}}),
        suspect_reply(), verdict_reply("fail", "wrong"),
        plan_reply("Fix it"),
        json.dumps({"code": "fix()", "step": 4, "label": "Step"}),
        json.dumps({"declare": {"steps": [{"step": 4, "label": "Step"}]}}),
        suspect_reply(), verdict_reply("pass"),
    ], applies=applies, undo_capacity=2)

    requests = [json.loads(call[-1]["content"]) for call in llm.calls
                if "already_built" in call[-1]["content"]]
    assert requests and requests[0]["undo_depth"] == 2


def _design():
    return design_reply(A_FILLET)


def _png():
    return RENDER_PNG
