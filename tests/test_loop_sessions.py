"""
Level 1 — the Plan -> Execute -> Review loop: resumed runs, the reading distiller, evidence and gap gate handling.

Split out of the single `test_loop.py` god-file so `--dist
loadfile` can spread these tests across xdist workers; the fixtures and
scripted-LLM harness common to every split live in `loop_helpers.py`.
"""

import ast
import base64
import json
import math
import os
import re
import tempfile
from dataclasses import replace
from unittest.mock import Mock

import pytest

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, BUDGET_EXHAUSTED, CALL_STARTED, COST, ERROR,
    GATE, INTENT, PART, PLAN, PLAN_REVISED, DESIGN, PREFLIGHT_FAILED, QUESTION,
    REVIEW_SUSPECTED, REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL,
    TOOL_RESULT, UNBUILT, WRITING, EventStream, replay,
)
from core.design import design
from core.design import part_file
from core.sheets import sheet_reader
from core.freecad.apply import TRAPS
from core.llm import REASONING, TEXT, Chunk, ProviderRefused, ScriptedLLMClient, StreamCutOff
from core.design.design import (
    UNDER, carried_by_no_source, decided_for_you, distilled, each_disagreement_once,
    entries, entry_at, measured_from_what_is_gone, merge, pointing,
    read_off_the_wrong_view, render, said_at,
)
from core.loop.budgets import Budgets, revisions_ceiling
from core.loop.prompt import _PHASE_VOCABULARY, _language_sections, prompt
from core.loop.run import run
from core.loop.prompt import PROMPTS
from core.loop.plan import _acceptance_of
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness
from tests.loop_helpers import (
    RENDER_PNG,
    DESCRIPTION,
    read,
    settled,
    A_CUBE,
    A_DATUM,
    A_PART,
    plan_reply,
    everything,
    acceptance_of,
    design_reply,
    code_reply,
    assertion_reply,
    obsolete_reply,
    look_reply,
    verdict_reply,
    suspect_reply,
    FakeTools,
    failed,
    scripted,
    drive,
    _turns_of,
    MODEL_MANIA,
    THE_2023_CLAMP,
    _the_clamp_as_it_was_read,
    _occupied_by,
    READ_AGAIN_OFF_THE_LATER_PAGE,
    THE_HOLE_THAT_CANNOT_BE_BOTH,
    THE_LEFT_LUG_PLANE,
    THE_ARMS_FREE_END,
    MEASURED_FROM_A_DELETED_FACE,
    RE_MEASURED,
    _asked_for_phase_1,
    NAMED_WHAT_ENDED_THE_RUN,
    THE_CLAMP_READ_OFF_ONE_VIEW,
)


def test_a_trailing_gaps_free_continuation_does_not_count_against_attempts_per_step():
    """
    `attempts_per_step=1` alone would block on the first failure; the one free
    trailing-gap continuation `trailing_gap_retries` grants (its default) means
    the Step is tried twice before that single-attempt budget is actually spent.
    """
    result, events, _, _ = drive(
        [plan_reply("Only")] + [json.dumps({"declare": {"note": "not sure what to build here"}})] * 2,
        budgets=Budgets(attempts_per_step=1, revisions=0),
    )

    assert not result.ok
    blocked = events.first(BUDGET_EXHAUSTED)
    assert blocked is not None
    assert len(blocked.data["tried"]) == 2


def test_an_interior_gap_gets_no_free_continuation():
    """
    A Step skipped while a later one in the same reply got code is not room
    pressure, so it spends `attempts_per_step` from the first occurrence —
    `attempts_per_step=1` blocks on it immediately, unlike the trailing-gap
    case above.
    """
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second", "Third"),
            json.dumps({"declare": {"steps": [{"step": 1, "code": "first()"},
                                  {"step": 3, "code": "third()"}]}}),
        ],
        budgets=Budgets(attempts_per_step=1, revisions=0),
    )

    assert not result.ok
    blocked = events.first(BUDGET_EXHAUSTED)
    assert blocked is not None
    assert len(blocked.data["tried"]) == 1
    assert "skipped a Step it could have written" in blocked.data["tried"][0]["traceback"]


def test_steps_applied_before_a_trailing_gap_stay_applied():
    """
    A reply that wrote Step 1 and ran dry on Step 2 does not undo Step 1 —
    the free continuation is for the tail Execute did not finish, not a
    reason to revisit what already landed.
    """
    result, _, _, tools = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()"),
            code_reply("second()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    assert tools.applied_codes() == ["first()", "second()"]


def test_execute_leaving_no_code_does_not_apply_anything_and_a_regeneration_does():
    """
    A reply with no `steps`, no `obsolete` and no `unbuildable` is a failed
    attempt now (FCC-304 deleted the mid-turn look Execute used to be able to
    ask with) — the free trailing-gap continuation gives it one more try, and
    that one writes the Step.
    """
    result, _, _, tools = drive([
        plan_reply("Add a box"),
        json.dumps({"declare": {"note": "what does this build have?"}}),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["doc.addObject('Part::Box', 'Box')"]


def test_the_whole_plan_is_written_in_one_call():
    """
    Execute cost 22 of roughly 29 provider calls in the recorded run, for six
    applied Steps. It is asked once, with every Step of the Plan in front of it,
    and answers with the code for all of them. FCC-95.
    """
    result, _, llm, tools = drive([
        plan_reply("First", "Second", "Third"),
        code_reply("first()", "second()", "third()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["first()", "second()", "third()"]

    turns = _turns_of(llm, "execute")
    assert len(turns) == 1, "Execute was asked more than once for a Plan that built"
    assert [s["step"] for s in turns[0]["steps"]] == [1, 2, 3]
    assert [s["do"] for s in turns[0]["steps"]] == ["First", "Second", "Third"]


def test_a_plan_that_needs_no_look_costs_no_look():
    """
    A Plan whose Steps are all buildable is one Execute call and nothing
    else the model has to be asked for — `check_assertions` still runs the
    deterministic self-intersection check (FCC-256), which asks nobody.
    """
    _, events, _, tools = drive([
        plan_reply("First", "Second"),
        code_reply("first()", "second()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    looked = [e.data["name"] for e in events.of(TOOL_CALL)]
    assert set(looked) == {"apply_step", "check_assertions"}
    assert not [name for name, _ in tools.calls if name in ("supported_types", "describe_type")]


def test_each_step_still_gets_its_own_started_event():
    """
    The code arrives in one call; it still lands one Step at a time. The panel
    is unchanged and so is the undo history. FCC-95.
    """
    _, events, _, _ = drive([
        plan_reply("First", "Second"),
        code_reply("first()", "second()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    started = events.of(STEP_STARTED)
    assert [(e.data["index"], e.data["total"]) for e in started] == [(1, 2), (2, 2)]
    assert [e.data["step"] for e in events.of(APPLIED)] == [1, 2]


def test_a_preflight_failure_rewrites_the_failing_step_and_the_whole_tail():
    """
    The code for Step N+1 was written assuming objects Step N was going to
    create. Regenerating Step N alone breaks silently — at the *next* Step's
    Preflight, not at this one. FCC-95.
    """
    result, _, llm, tools = drive(
        [
            plan_reply("First", "Second", "Third"),
            code_reply("first()", "second()", "third()"),
            code_reply("second_again()", "third_again()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[None, failed("AttributeError: no Length on this")],
    )

    assert result.ok
    assert tools.applied_codes() == ["first()", "second()", "second_again()", "third_again()"]

    regeneration = _turns_of(llm, "execute")[1]
    assert [s["step"] for s in regeneration["steps"]] == [2, 3], \
        "the tail after the failing Step was not written again"
    assert regeneration["your_failed_attempts_at_this_step"] == [
        {"code": "second()", "traceback": "AttributeError: no Length on this"}]
    assert "Step 2" in regeneration["what_to_do_now"]


def test_the_regeneration_is_written_against_the_document_as_it_stands():
    """
    Not against the Plan's assumption of it: the Steps before the failure are
    applied and the failing one is not, and the Snapshot is re-read so the code
    is written over what is actually there. FCC-95.
    """
    _, _, llm, tools = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()", "second()"),
            code_reply("second_again()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[None, failed("boom")],
    )

    regeneration = _turns_of(llm, "execute")[1]
    assert regeneration["document"]["objects"] == [{"name": "Box"}]
    assert regeneration["document"]["object_count"] == 1

    last_failure = max(index for index, (name, args) in enumerate(tools.calls)
                       if name == "apply_step" and args["code"] == "second()")
    assert "snapshot" in [name for name, _ in tools.calls[last_failure:]]


def test_a_failure_at_every_step_costs_no_more_than_it_costs_today():
    """
    The worst case degrades to the old shape and no further: one call up front,
    then one per failure. Six Steps that each fail once is seven Execute calls,
    where the per-Step loop spent twelve. FCC-95.
    """
    _, _, llm, _ = drive(
        [
            plan_reply("First", "Second", "Third"),
            code_reply("a1()", "b1()", "c1()"),
            code_reply("a2()", "b2()", "c2()"),
            code_reply("b3()", "c3()", first=2),
            code_reply("c4()", first=3),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("a"), None, failed("b"), None, failed("c"), None],
    )

    assert len(_turns_of(llm, "execute")) == 4


def test_a_step_that_applied_carries_none_of_its_failures_into_the_next_writing():
    """
    `your_failed_attempts_at_this_step` is the failures of the Step being
    written again, and a Step that applied has none. What used to keep Step 2
    from seeing Step 1's traceback was that they were different calls; what
    keeps it now is that the attempts reset when a Step lands. FCC-95.
    """
    _, _, llm, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first_bad()", "second()"),
            code_reply("first_good()", "second()"),
            code_reply("second_good()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("AttributeError: no Length on this"), None, failed("a different boom")],
    )

    retry_of_first, retry_of_second = _turns_of(llm, "execute")[1:]

    assert "AttributeError: no Length on this" in json.dumps(retry_of_first)
    assert "AttributeError: no Length on this" not in json.dumps(retry_of_second), \
        "Step 2 was shown Step 1's failure"
    assert retry_of_second["your_failed_attempts_at_this_step"] == [
        {"code": "second()", "traceback": "a different boom"}]


def test_a_step_the_reply_left_out_fails_that_step_and_not_the_run():
    """A Step with no code is an attempt spent, the same as one that failed."""
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()"),
            code_reply("second()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    failure = events.first(PREFLIGHT_FAILED)
    assert failure.data["step"] == 2
    assert "no code for Step 2" in failure.data["traceback"]


def test_a_trailing_gap_says_it_ran_out_of_room_and_steers_shorter():
    """
    Step 2 is the Plan's last Step and nothing after it got code either — the
    reply ran out of room rather than judging the Step, so the retry is told
    to write shorter code rather than asked to judge it differently.
    """
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()"),
            code_reply("second()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    failure = events.first(PREFLIGHT_FAILED)
    assert failure.data["step"] == 2
    assert "ran out of room before finishing this reply" in failure.data["traceback"]
    assert "nothing after it either" in failure.data["traceback"]
    assert "shorter this time" in failure.data["traceback"]


def test_an_interior_gap_says_a_step_was_skipped_not_that_room_ran_out():
    """
    Step 2 is missing while Step 3, after it, carries code in the same reply —
    the model could write further down and skipped this one, which is a
    different failure than running dry and gets no "shorter this time" steer.
    """
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second", "Third"),
            json.dumps({"declare": {"steps": [{"step": 1, "code": "first()"},
                                  {"step": 3, "code": "third()"}]}}),
            code_reply("second()", "third()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    failure = events.first(PREFLIGHT_FAILED)
    assert failure.data["step"] == 2
    assert "skipped a Step it could have written" in failure.data["traceback"]
    assert "ran out of room" not in failure.data["traceback"]
    assert "shorter this time" not in failure.data["traceback"]


def test_a_step_left_out_with_no_reason_records_what_the_reply_carried():
    """
    Case 2 of the three ways `execute()` can leave a Step without code: a
    reply that numbered other Steps but not this one, with no `unbuildable`
    to say why. The recorded run could not tell this apart from a reply that
    said nothing at all — this is what closes that hole.
    """
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()"),
            code_reply("second()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    failure = events.first(PREFLIGHT_FAILED)
    assert "keys: steps" in failure.data["traceback"]
    assert "steps carried: [1], with code: [1]" in failure.data["traceback"]


def test_a_reply_with_nothing_usable_says_so_in_the_traceback():
    """
    Case 3: no `steps`, no `unbuildable`, no `obsolete`, no `look` — a reply
    Execute cannot be asked to explain itself over twice, because the second
    ask is what `_ask` already spent asking for the shape it did answer in.
    """
    result, events, _, _ = drive(
        [
            plan_reply("Only"),
            json.dumps({"declare": {"note": "not sure what to build here"}}),
            code_reply("built()"),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    failure = events.first(PREFLIGHT_FAILED)
    assert failure.data["attempt"] == 1
    assert "no code for Step 1" in failure.data["traceback"]
    assert "keys: note" in failure.data["traceback"]
    assert "not sure what to build here" in failure.data["traceback"]


def test_the_rendering_of_what_was_said_is_capped_not_the_reply():
    """
    An Execute reply carries the code for a whole Plan. What travels in the
    traceback is the shape of the reply, not its content — the numbers, never
    the code — so a Step's worth of code arriving beside the one left out
    cannot make the traceback the size of the reply that produced it.
    """
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second"),
            json.dumps({"declare": {"steps": [{"step": 1, "code": "x" * 5000, "label": "Step"}]}}),
            code_reply("second()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    failure = events.first(PREFLIGHT_FAILED)
    assert failure.data["step"] == 2
    assert "x" * 100 not in failure.data["traceback"]
    assert len(failure.data["traceback"]) < 500


def test_a_step_that_lands_is_unaffected_by_what_the_reply_said():
    """
    `said` only ever reaches the traceback of the Step it was missing for — a
    reply that wrote code for every Step asked has nothing to render and
    nothing shows up.
    """
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()", "second()"),
            suspect_reply(), verdict_reply("pass"),
        ],
    )

    assert result.ok
    assert events.first(PREFLIGHT_FAILED) is None


def test_the_reason_a_recompute_failed_reaches_the_next_attempts_request():
    """
    The reason has to be in the request, not merely on the event stream — a
    causeless message is what made every attempt a re-spelling. FCC-11.
    """
    _, _, llm, _ = drive(
        [
            plan_reply("Add a pad"),
            code_reply("pad_one()"),
            code_reply("pad_two()"),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("Pad (PartDesign::Pad): Linked shape object is empty"), None],
    )

    retry = json.loads(llm.calls[3][-1]["content"])

    assert retry["your_failed_attempts_at_this_step"] == [
        {"code": "pad_one()",
         "traceback": "Pad (PartDesign::Pad): Linked shape object is empty"},
    ]


def test_a_failed_attempt_is_surfaced_before_the_retry():
    _, events, _, _ = drive(
        [
            plan_reply("Only"),
            code_reply("bad()"),
            code_reply("good()"),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("ValueError: nope"), None],
    )

    kinds = [e.kind for e in events]
    assert kinds.index(PREFLIGHT_FAILED) < kinds.index(APPLIED)
    assert events.first(PREFLIGHT_FAILED).data["traceback"] == "ValueError: nope"
    assert events.first(PREFLIGHT_FAILED).data["attempt"] == 1


def test_execute_can_declare_a_step_obsolete_and_plan_revises():
    result, events, _, tools = drive([
        plan_reply("Delete the old pad", "Add the new one"),
        obsolete_reply("There is no pad in this Document; nothing to delete."),
        plan_reply("Add the new one"),
        code_reply("add_new()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None, "the revision was not surfaced"
    assert revised.data["steps"] == ["Add the new one"]
    assert "no pad in this Document" in revised.data["reason"]
    assert tools.applied_codes() == ["add_new()"]


def test_a_revised_plan_does_not_reapply_steps_that_already_landed():
    """
    Matched to `logs/sessions/0de34f88d1274dcebb02ac67e0e52fb0.jsonl`: an
    11-Step Plan revised over Steps 3 and 7 produced 28 `step_started` events
    where 11 would say the same thing, because a revision always restarted
    Execute at Step 1 no matter how much of the Plan it left alone.

    Here the revision leaves Steps 1 and 2 exactly as they read the first
    time, so the second pass must not start them, must not apply them again,
    and must still show them applied on the stream — they already are.
    """
    result, events, llm, tools = drive([
        plan_reply("Pad the outline", "Fillet the marked edges",
                   "Drill the hole", "Chamfer the edge"),
        json.dumps({"declare": {"steps": [{"step": 1, "code": "pad()", "label": "Pad"},
                              {"step": 2, "code": "fillet()", "label": "Fillet"}],
                    "unbuildable": "Step 3 needs a hole diameter nobody supplied."}}),
        plan_reply("Pad the outline", "Fillet the marked edges",
                   "Drill a 6mm hole", "Chamfer the edge"),
        code_reply("drill()", "chamfer()", first=3),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok, result.reason
    assert tools.applied_codes() == ["pad()", "fillet()", "drill()", "chamfer()"], \
        "Steps 1 and 2 were applied again on the second pass"

    revised_at = [e.kind for e in events].index(PLAN_REVISED)
    after = events[revised_at + 1:]
    assert [e.data["index"] for e in after if e.kind == STEP_STARTED] == [3, 4], \
        "a carried Step was started again"
    carried = [e.data["step"] for e in after if e.kind == APPLIED and e.data["step"] in (1, 2)]
    assert carried == [1, 2], "a carried Step was not marked applied on the second pass"

    second_pass = _turns_of(llm, "execute")[-1]
    assert [s["step"] for s in second_pass["steps"]] == [3, 4], \
        "Execute was asked to write Steps it had already applied"


def test_a_revised_plan_whose_first_step_differs_reexecutes_everything():
    """
    The guard is a prefix match, not a hunch: a revision that changes the
    very first Step carries nothing, and the second pass is a full Execute
    turn over every Step of the revised Plan, same as before this existed.
    """
    result, events, llm, tools = drive([
        plan_reply("Pad the outline", "Fillet the marked edges"),
        json.dumps({"declare": {"steps": [{"step": 1, "code": "pad()", "label": "Pad"}],
                    "unbuildable": "Step 2 needs edges nobody named."}}),
        plan_reply("Pad a wider outline", "Fillet the named edges"),
        code_reply("pad_wider()", "fillet_named()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok, result.reason
    assert tools.applied_codes() == ["pad()", "pad_wider()", "fillet_named()"]

    second_pass = _turns_of(llm, "execute")[-1]
    assert [s["step"] for s in second_pass["steps"]] == [1, 2], \
        "a Step that only reads differently was still treated as carried"


def test_a_revised_plan_says_which_revision_it_is_and_what_caused_it():
    """
    Three Revised Plans hitting the same wall read as three fresh starts, and
    the reason alone never said otherwise — which is what a run that looks
    stalled for no reason actually is.
    """
    _, events, _, _ = drive([
        plan_reply("Delete the old pad", "Add the new one"),
        obsolete_reply("There is no pad in this Document."),
        plan_reply("Add the new one"),
        code_reply("add_new()"),
        suspect_reply(), verdict_reply("pass"),
    ], budgets=Budgets(revisions=3))

    revised = events.first(PLAN_REVISED).data
    assert (revised["revision"], revised["of"]) == (1, 3)
    assert revised["because"] == "Execute found the Plan out of date"


def test_the_plan_gate_leaves_both_ends_of_its_wait_on_the_stream():
    """
    The Gate is the longest silence in a run. Without it on the stream, a
    session read back cannot tell a user thinking for half an hour from a
    provider taking half an hour.
    """
    _, events, _, _ = drive([
        plan_reply("Add a box"),
        code_reply("add_box()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    opened, decided = events.of(GATE)
    assert opened.data == {"steps": 1, "asked": True}
    assert decided.data == {"decision": "approve", "standing": False, "steps": ["Add a box"]}
    kinds = [e.kind for e in events]
    assert kinds.index(PLAN) < kinds.index(GATE) < kinds.index(STEP_STARTED)


def test_obsolete_beside_code_does_not_throw_that_code_away():
    """
    A complaint about a Step is not a verdict on the Plan.

    A recorded run replanned three times and applied nothing: Execute read the
    whole Plan, found one late Step it did not like, and declared the lot
    obsolete before writing a line — so Steps nobody had attempted died with
    it. Where a reply carries code, what it could not do is what it left out,
    and only that goes back to Plan.
    """
    result, events, _, tools = drive([
        plan_reply("Pad the outline", "Fillet the marked edges"),
        json.dumps({"declare": {"steps": [{"step": 1, "code": "pad()", "label": "Pad"}],
                    "obsolete": "Step 2 does not say which edges are marked."}}),
        plan_reply("Fillet the four corner edges"),
        code_reply("fillet()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["pad()", "fillet()"]
    revised = events.first(PLAN_REVISED)
    assert "which edges are marked" in revised.data["reason"]
    assert revised.data["because"] == "Execute could not build a Step"


def test_a_step_left_out_with_a_reason_hands_back_without_spending_attempts():
    """
    Leaving a Step out is what Execute is told to do with one it cannot build,
    so it must not cost three identical regenerations to say so. The reason is
    the only thing that lets Plan write the Step differently: without it Plan
    hears "no code for Step 2" three times over and learns nothing.
    """
    result, events, llm, tools = drive([
        plan_reply("Pad the outline", "Pocket the hex recess"),
        json.dumps({"declare": {"steps": [{"step": 1, "code": "pad()", "label": "Pad"}],
                    "unbuildable": "Step 2 cuts a hex inside a bore of the same "
                                   "diameter, so it moves no material."}}),
        plan_reply("Pocket a wider hex recess"),
        code_reply("hex()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["pad()", "hex()"]
    assert len(_turns_of(llm, "execute")) == 2, "the left-out Step was written again"
    assert events.first(PREFLIGHT_FAILED) is None
    revised = events.first(PLAN_REVISED)
    assert "moves no material" in revised.data["reason"]
    assert revised.data["because"] == "Execute could not build a Step"


def test_an_unbuildable_reply_on_a_retry_hands_back_instead_of_being_asked_again():
    """
    Matched to the recorded run
    (`logs/sessions/5d9249424bce45d2b25485002ba1c630.jsonl`, Step 6 of 13,
    twice over): attempt 1 carried a real traceback, and Execute's *second*
    reply was `unbuildable` rather than another attempt at the code. The
    reason was discarded because the handback only fired on a Step's first
    attempt — the loop asked a third time, spent the whole
    `attempts_per_step` budget on two provider calls that recorded nothing
    but the synthetic "no code for Step 6", and Plan never learned why. The
    reason has to reach the handback on whichever attempt it arrives.
    """
    result, events, llm, tools = drive([
        plan_reply("Fillet the root edges of the lug"),
        code_reply("fillet_root()"),
        json.dumps({"declare": {"unbuildable": "the fillet radius exceeds the material at "
                                   "the root edge; there is nothing left to round."}}),
        plan_reply("Fillet the root edges at a smaller radius"),
        code_reply("fillet_root_smaller()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed("Fillet (Part::Fillet): FilletId is not part of the shape")])

    assert result.ok
    assert tools.applied_codes() == ["fillet_root()", "fillet_root_smaller()"]
    first_plan, retry, after_replan = _turns_of(llm, "execute")
    assert "your_failed_attempts_at_this_step" not in first_plan
    assert retry["your_failed_attempts_at_this_step"] == [
        {"code": "fillet_root()",
         "traceback": "Fillet (Part::Fillet): FilletId is not part of the shape"}]
    assert "your_failed_attempts_at_this_step" not in after_replan, \
        "handed back after the second reply, not asked a third time within the same Plan"
    assert len(events.of(PREFLIGHT_FAILED)) == 1, "the real failure was the only attempt spent"
    revised = events.first(PLAN_REVISED)
    assert "nothing left to round" in revised.data["reason"]
    assert revised.data["because"] == "Execute could not build a Step"


def test_an_obsolete_step_is_never_applied():
    _, _, _, tools = drive([
        plan_reply("Obsolete one"),
        obsolete_reply("Already done."),
        plan_reply("Something else"),
        code_reply("something()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert tools.applied_codes() == ["something()"]


def test_review_receives_the_intent_the_render_and_the_document_state():
    _, _, llm, _ = drive([
        plan_reply("Only"),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    asked = json.dumps(llm.calls[-1])
    assert "Make a 10mm box" in asked
    assert "Part::Box" in asked, "Review did not see the final Document state"


def test_review_and_assertion_are_both_given_the_whole_description():
    """
    Both phases judge a finished part, so both get the same description of it
    — the features with their parameters and the geometry off the Shape, not
    a list of type names. FCC-81.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    to_review = json.loads(llm.calls[3][-1]["content"][0]["text"].split("\n\n")[0])
    to_assertion = json.loads(llm.calls[4][-1]["content"])

    assert to_assertion["the_document_as_it_now_stands"] == DESCRIPTION
    assert to_review["the_document_as_it_now_stands"] == DESCRIPTION


def test_review_is_shown_the_render_as_a_picture_not_as_a_path():
    """
    A path is meaningless to a model — it cannot open files. Sending one would
    mean Review judged on the object list while the panel showed the user an
    image the model never saw.
    """
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                         views=[RENDER_PNG])

    content = llm.calls[-1][-1]["content"]
    images = [block for block in content if block.get("type") == "image_url"]

    assert images, "Review was sent no image"
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert RENDER_PNG not in json.dumps(content), "the path was sent instead of the bytes"


def test_review_is_told_when_there_is_no_render_to_judge_by():
    """A sketch-only Step produces no render; Review must not be left guessing."""
    tools = FakeTools(applies=[{"status": "applied", "created": ["Sketch"],
                                "description": {"features": [], "geometry": {}}}])
    llm = scripted([plan_reply("Only"), code_reply("sketch()"), suspect_reply(), verdict_reply("pass")])

    run("Sketch a square", llm=llm, tools=tools.registry)

    content = llm.calls[-1][-1]["content"]
    assert not [b for b in content if b.get("type") == "image_url"]
    assert "No render was produced" in content[0]["text"]


def test_an_attachment_reaches_the_design_phase_as_a_picture():
    """
    The panel used to collect the attached paths and throw them away, so the
    user showed the assistant a photo it never saw. FCC-4. The phase that
    receives it is the Design phase since FCC-86; Plan never sees a picture.
    """
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this bracket", llm=llm, tools=FakeTools().registry, attachments=[RENDER_PNG])

    content = llm.calls[0][-1]["content"]
    images = [block for block in content if block.get("type") == "image_url"]

    assert "Build this bracket" in content[-1]["text"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert RENDER_PNG not in json.dumps(content), "the path was sent instead of the bytes"
    assert isinstance(llm.calls[1][-1]["content"], str), "Plan was sent the picture"




def test_the_part_design_read_is_written_to_a_file_the_run_names():
    """
    The reading used to exist only as prose in the Plan request and one line in
    the transcript, so it could be neither diffed nor corrected.
    """
    from core import part_file

    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    written = events.last(PART).data["path"]
    assert os.path.isfile(written)
    assert part_file.read(written) == events.first(DESIGN).data["model"]


def test_a_missing_pyyaml_ends_the_run_as_a_sentence_and_not_a_crash_report(monkeypatch):
    """
    A missing PyYAML is a known, actionable outcome — absent from FreeCAD's
    own Python — and its own message already names the package and the fix,
    so it belongs on the stream as that sentence, the same way a PhaseError
    does, not as a traceback swallowed into a crash report by whichever
    caller happens to be watching.
    """
    monkeypatch.setattr(part_file, "yaml", None)

    result, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert not result.ok
    error = events.last(ERROR)
    assert "PyYAML" in error.data["message"]
    assert "🚨" not in error.data["message"]
    assert error.data["message"] == str(part_file.MissingPyYAML())


def test_the_file_says_which_sheet_the_part_was_read_from():
    from core import part_file

    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[MODEL_MANIA])

    read_from = part_file.provenance(events.last(PART).data["path"])
    assert read_from["sheet"] == os.path.basename(MODEL_MANIA)
    assert read_from["views"], "the views it was read from are not recorded"


def test_a_run_given_a_prior_part_skips_the_design_phase():
    """
    The second half of one conversation plans from what the first half already
    read off the sheet, rather than asking Design to describe it again — the
    bug this fixes (FCC-210, ev160-178 of session 796bf66c).
    """
    from core import part_file

    _, first, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    written = first.last(PART).data["path"]

    result, events, llm, _ = drive([
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], the_part_path=written)

    assert not events.of(DESIGN)
    assert not _turns_of(llm, "design")
    assert _turns_of(llm, "plan")[0]["what_the_part_is"] == render(part_file.read(written))
    assert result.the_part_path == written


def test_a_run_with_no_prior_part_still_runs_design():
    """A fresh Session, with nothing to resume, behaves exactly as today."""
    result, events, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.of(DESIGN)
    assert llm.calls, "Design never asked the provider anything"
    written = events.last(PART).data["path"]
    assert result.the_part_path == written


def test_an_attachment_on_a_resumed_run_overrides_the_prior_part():
    """
    A new drawing mid-chat replaces the carried Part rather than resuming it —
    Design runs again, on the new sheet.
    """
    _, first, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    written = first.last(PART).data["path"]

    result, events, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Only", satisfies=everything(A_PART)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], the_part_path=written)

    assert events.of(DESIGN)
    assert llm.calls, "Design never asked the provider anything"
    assert result.the_part_path != written
    assert result.the_part_path == events.last(PART).data["path"]




def test_plan_is_handed_the_document_and_not_everything_design_read():
    """
    The recorded run of the 2023 pipe clamp handed Plan every scrap of evidence
    Design had gathered — a sweep of every callout on the sheet, and every
    disagreement, none of which is the part. Distilled it is a document a
    person reads at a sitting, and both numbers here are that reading's.
    """
    _, _, llm, _ = drive([
        design_reply(THE_2023_CLAMP),
        plan_reply("Build the clamp", satisfies=acceptance_of(THE_2023_CLAMP)),
        code_reply("clamp()"),
        suspect_reply(),
        assertion_reply("assert True, 'the clamp'"),
        verdict_reply("pass"),
    ], budgets=Budgets(rereads=0), intent="Build the 2023 pipe clamp")

    handed = _turns_of(llm, "plan")[0]["what_the_part_is"]
    assert len(render(merge({}, THE_2023_CLAMP))) > 43_000, \
        "this is no longer the reading the run took"
    assert len(handed) < 9_000, "Plan is being handed the evidence again"






def test_no_number_in_the_document_came_from_the_distiller():
    """
    The boundary the distiller is held to, over the run that named it: every
    number the document states is in the evidence log beside it or in the
    Intent it was read under. The half that decides identity and catches what
    does not follow is a model reading prose, and a model free to decide those
    is free to write a value no source carried — which is the one thing this
    phase exists to prevent. The added 77 mm is what that looks like.
    """
    document, logged, intent = _the_clamp_as_it_was_read()
    invented = dict(document, envelope=dict(document["envelope"], height=read("77 mm")))

    assert carried_by_no_source(document, logged, intent) == []
    assert carried_by_no_source(invented, logged, intent) == [77.0]




def test_the_document_says_why_a_value_is_the_users_and_the_log_says_what_proved_it():
    """
    What survives distillation with a value and what does not, measured on the
    recorded reading. `from` and `seen_in` prove a number rather than being one
    and are cut mechanically into the log — 2,190 characters of them. The 357
    characters of `because` stay, because they are the only thing telling a
    user the model chose a value rather than read it (ADR-0022), and so does
    the flag on every one of the 46 entries the Acceptance is derived from.

    The document says more of it than the reading does — 1,376 characters — and
    the whole of the difference is the seven values re-measured from a datum
    that survives, which is the one thing the distiller decides and the reading
    never said.
    """
    document, logged, _ = _the_clamp_as_it_was_read()

    assert _occupied_by(document, ("from", "seen_in")) == 0
    assert _occupied_by({section: logged[section] for section in document},
                     ("from", "seen_in")) == 2_190
    assert _occupied_by({section: logged[section] for section in document},
                     ("because",)) == 357
    assert _occupied_by(document, ("because",)) == 1_376
    assert [entry for entry in entries(document) if not entry["flag"]] == []
    assert len(entries(document)) == 46
    assert [decision for decision in decided_for_you(document)
            if READ_AGAIN_OFF_THE_LATER_PAGE not in decision] == \
        decided_for_you(logged) != []


def test_the_file_the_user_opens_is_the_document_the_run_was_planned_from():
    """
    The reading and the request used to be one rendering of the same undistilled
    model each, so the file a user opens to correct an axis held the evidence
    as well. One document now: what `part_file.read` hands back is what Plan was
    asked to account for, described and measured alike.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Only", satisfies=everything(A_PART)),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    request = _turns_of(llm, "plan")[0]
    document = part_file.read(events.last(PART).data["path"])
    assert document == distilled(merge({}, A_PART))
    assert render(document) == request["what_the_part_is"]
    assert _acceptance_of(document) == request["what_the_plan_must_satisfy"]


def test_a_value_corrected_in_the_file_is_the_one_the_run_is_planned_from(monkeypatch):
    """
    The Gate links to the file rather than showing the description, so the file
    has to be the description the run is held to. Corrected between the writing
    and the reading back — which is what a user does when the link opens on a
    misread length — the correction is what Plan is handed (FCC-108).
    """
    written = part_file.saved

    def corrects_the_length(model, source, **head):
        path = written(model, source, **head)
        with open(path, encoding="utf-8") as handle:
            document = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(document.replace("80 mm", "999 mm"))
        return path

    monkeypatch.setattr(part_file, "saved", corrects_the_length)

    _, events, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Only", satisfies=everything(A_PART)),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    handed = _turns_of(llm, "plan")[0]["what_the_part_is"]
    assert "length: 999 mm" in handed, "the run was planned from something other than the file"
    assert "80 mm" not in handed
    assert "999 mm" in render(part_file.read(events.last(PART).data["path"]))


def test_the_evidence_is_logged_beside_the_reading_distilled_out_of_it():
    """
    Nothing downstream needs the proof of a number, and whoever asks tomorrow
    where one came from needs all of it — so it is kept where they will already
    be looking, under the name of the reading it belongs to.
    """
    _, events, _, _ = drive([
        design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
        design_reply({}),
        plan_reply("Pad the outer profile",
                   satisfies=everything(THE_CLAMP_READ_OFF_ONE_VIEW)),
        code_reply("pad()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    written = events.last(PART).data["path"]
    beside = part_file.evidence(written)
    assert os.path.isfile(beside)
    assert "conflicts" not in part_file.read(written)
    assert "the two axes cross" in json.dumps(part_file.read(beside)["conflicts"])




def test_two_entries_that_cannot_both_be_true_still_reach_the_gate():
    """
    The half of distillation no rule can do is the model's: it decides which
    two entries are one feature under two names, and it catches values that do
    not follow from each other — 15 mm from one end and 70 mm from the other on
    a part 80 mm long, which every check in `core/design.py` reads as two
    positions and nothing more. Distilling is not where such a finding is
    settled and not where it is lost: it rides to the Gate with the rest and
    stands in the log beside the document, and the only entry distillation may
    take out of that list is one another entry at the same address stands for.
    """
    _, events, _, _ = drive([
        design_reply(THE_HOLE_THAT_CANNOT_BE_BOTH),
        plan_reply("Drill it", satisfies=everything(THE_HOLE_THAT_CANNOT_BE_BOTH)),
        code_reply("drill()"),
        suspect_reply(),
        assertion_reply("assert True, 'the hole'"),
        verdict_reply("pass"),
    ])

    written = events.last(PART).data["path"]
    kept = part_file.read(part_file.evidence(written))["conflicts"]
    flagged = THE_HOLE_THAT_CANNOT_BE_BOTH["conflicts"][0]
    lost = [conflict for conflict in merge({}, THE_HOLE_THAT_CANNOT_BE_BOTH)["conflicts"]
            if conflict not in kept]

    assert dict(flagged, kind=design.A_CONFLICT) in kept
    assert flagged in events.last(DESIGN).data["model"]["conflicts"]
    assert [conflict for conflict in lost
            if conflict["about"] not in {standing["about"] for standing in kept}] == []







def test_nothing_the_document_states_is_measured_from_a_face_the_sheet_deleted():
    """
    The recorded run of the 2023 pipe clamp measures seven of its criteria from
    two faces the second page deletes: the 108 mm overall and the lengthwise
    position of the outer profile, the bore and the outer ring from the left
    lug's tangent plane, and the slot from the axis of the hole it replaces.
    Every one of them is a criterion the finished part is checked against and
    none of them is on it (FCC-153).
    """
    document, logged, _ = _the_clamp_as_it_was_read()

    assert [found["about"] for found in measured_from_what_is_gone(logged, logged)] == \
        MEASURED_FROM_A_DELETED_FACE
    assert measured_from_what_is_gone(document, logged) == []


def test_each_of_the_seven_is_re_measured_from_a_datum_that_survives():
    """
    Re-measured and not dropped: what the sheet gave stays a criterion, keyed by
    the datum that takes over from the dead one — the arm's own free end face
    where the left lug's tangent plane was, the slot's axis where the hole's
    was — with what it said from the face that is gone kept under `was`, so the
    Gate shows a person which seven the later page unsettled (ADR-0022). The
    dead datum leaves `datums` with the feature that owned it.
    """
    document, _, _ = _the_clamp_as_it_was_read()
    named = [datum["name"] for datum in document["datums"]]

    assert [entry_at(document, at)["was"] for at in RE_MEASURED] == [
        "108 mm", "0 mm", "108 mm from %s" % THE_LEFT_LUG_PLANE,
        "65.5 mm from %s" % THE_LEFT_LUG_PLANE, "65.5 mm", "65.5 mm", "concentric"]
    assert THE_LEFT_LUG_PLANE not in named
    assert THE_ARMS_FREE_END in named


def test_what_the_sheet_said_still_reaches_the_user_at_the_gate():
    """
    The re-measure is the distiller's and never the merge's: the merge stays
    monotonic (ADR-0019), so the description the Gate is read against is still
    the sheet's own — the 108 mm the second page unsettles, and the datum it is
    measured from, both written where Design put them.
    """
    reading = merge({}, THE_2023_CLAMP)

    assert THE_LEFT_LUG_PLANE in [datum["name"] for datum in reading["datums"]]
    assert entry_at(reading, MEASURED_FROM_A_DELETED_FACE[0])["value"] == "108 mm"




def test_a_run_asked_for_the_earlier_revision_re_measures_nothing():
    """
    The invariant is what has to hold when the user asks for the later revision.
    Asked for Phase 1 off the same sheet, nothing is superseded, no datum dies,
    every criterion keeps the address it was read under — and the 108 mm the
    Phase 2 document loses is still the length of the part.
    """
    phase_1 = _asked_for_phase_1(merge({}, THE_2023_CLAMP))
    document = distilled(phase_1)

    assert measured_from_what_is_gone(document, phase_1) == []
    assert [entry["about"] for entry in entries(document)] == \
        [entry["about"] for entry in entries(phase_1)]
    assert entry_at(document, MEASURED_FROM_A_DELETED_FACE[0])["value"] == "108 mm"





def test_the_log_of_the_recorded_run_keeps_one_entry_to_an_address():
    """
    The list the recorded run of the 2023 pipe clamp read past. It tripled
    without a feature changing — the same 13 ids throughout — because the merge
    drops an entry only where the whole record repeats, so one disagreement
    written twice in two words was two entries and a re-read that spelled a
    view differently was a third. The reading keeps all 33, because the merge
    only ever grows (ADR-0019); the log beside the document keeps one to an
    address, and the document keeps none of them at all — Plan is handed the
    part and nothing that argues about it (FCC-156).

    Twenty-five of the twenty-six say what they are about. The twenty-sixth
    names the two views that disagree about the right-lug recess and no path at
    all, so it addresses nothing and stands beside them all.
    """
    document, logged, _ = _the_clamp_as_it_was_read()
    addressed = [conflict["about"] for conflict in logged["conflicts"]
                 if "about" in conflict]

    assert len(THE_2023_CLAMP["conflicts"]) == 33
    assert len(logged["features"]) == len(THE_2023_CLAMP["features"]) == 13
    assert len(logged["conflicts"]) == 26
    assert len(set(addressed)) == len(addressed) == 25
    assert "conflicts" not in document


def test_every_entry_of_the_recorded_run_says_which_of_the_three_it_is():
    """
    Three kinds and no fourth: two readings that cannot both be true, a value
    nothing on the part supplies, and a check that could not be run. The Gate
    cannot act on a list where all three read alike — and a check that could
    not be run is not a sheet that agrees with itself.

    Twenty-five of the recorded run's are disagreements, which is what the
    model writes into the section and what the merge finds. One is the gap
    `placement` finds: the ring split is 3 mm wide and nothing places it in the
    width. None is a check hazard, because no headless FreeCAD ran.
    """
    _, logged, _ = _the_clamp_as_it_was_read()
    of_each_kind = {kind: [conflict for conflict in logged["conflicts"]
                           if conflict["kind"] == kind] for kind in design.KINDS}

    assert sum(len(found) for found in of_each_kind.values()) == 26
    assert [len(of_each_kind[kind]) for kind in design.KINDS] == [25, 1, 0]
    assert of_each_kind[design.A_GAP][0]["about"] == \
        "features \u2192 ring-split \u2192 position \u2192 the width"


def test_the_four_that_named_what_ended_the_run_stand_under_conflict():
    """
    The run stopped asking for the description to be corrected — "the values it
    holds in conflict, and the datums and overalls a superseded feature left
    behind". Four entries had named exactly that: the 108 mm overall measured
    from the left lug the second page replaces, twice, and the 73 mm along the
    arm measured to the ring contour rather than to the ring axis, twice. They
    are readings that cannot both be true, so they stand where a user settling
    the part looks, and not among the checks nobody could run.

    Where a user reads them is the reading the Gate is shown, since the
    document Plan is handed argues about nothing (FCC-156).
    """
    _, events, llm, _ = drive([
        design_reply(THE_2023_CLAMP),
        plan_reply("Build the clamp", satisfies=acceptance_of(THE_2023_CLAMP)),
        code_reply("clamp()"),
        suspect_reply(),
        assertion_reply("assert True, 'the clamp'"),
        verdict_reply("pass"),
    ], budgets=Budgets(rereads=0), intent="Build the 2023 pipe clamp")

    logged = part_file.read(part_file.evidence(events.last(PART).data["path"]))
    typed = {conflict.get("about"): conflict["kind"]
             for conflict in logged["conflicts"]}
    shown = render(events.last(DESIGN).data["model"])
    under_conflict = shown.split(design.HEADED[design.A_CONFLICT])[1] \
        .split(design.HEADED[design.A_GAP])[0]

    assert [typed.get(at) for at in NAMED_WHAT_ENDED_THE_RUN] == \
        [design.A_CONFLICT] * 4
    assert all(at in under_conflict for at in NAMED_WHAT_ENDED_THE_RUN)
    assert "In conflict" not in _turns_of(llm, "plan")[0]["what_the_part_is"]
