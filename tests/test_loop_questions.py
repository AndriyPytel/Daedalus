"""
Level 1 — the Plan -> Execute -> Review loop: question routing, re-read ceilings, replans and the assertion phase core.

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
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, CALL_STARTED, CHANGE_SET, COST,
    ERROR, GATE, INTENT, PART, PLAN, PLAN_REVISED, DESIGN, PREFLIGHT_FAILED, QUESTION,
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
from core.loop.prompt import PROMPTS, _PHASE_VOCABULARY, _language_sections, prompt
from core.loop.run import run
from core.loop.plan import _in_the_prose
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness
from tests.loop_helpers import (
    RENDER_PNG,
    DESCRIPTION,
    read,
    settled,
    A_PART,
    plan_reply,
    everything,
    design_reply,
    code_reply,
    assertion_reply,
    question_reply,
    look_reply,
    verdict_reply,
    suspect_reply,
    FakeTools,
    failed,
    drive,
    _turns_of,
    _cut_off,
    GEAR,
    MISSING,
    A_NUT,
    ACCEPTANCE,
    a_run_over_the_nut,
    planning_the_nut,
    CHECKS,
    assertion_failed,
    SELF_INTERSECTION_HELD,
    ASSERT_FAILED,
    CHECK_IS_BROKEN,
    THE_CAVITY,
    CAVITY_DEPTH,
    CAVITY_POCKET,
    THE_BRACKET,
    BUILT,
    LEFT_TO_BUILD,
    _four_steps_applied_then_a_handback,
    SIX_FAILED,
    MIXED_TRACEBACK,
    assertion_mixed,
    STILL_WRONG,
)


def test_the_questions_nobody_answered_are_shown_beside_the_plan():
    """
    A Plan told to choose for itself carries values nobody supplied, and the
    Gate is the only place they are correctable — so the Gate is handed the
    questions, not only the Steps. FCC-96.
    """
    _, events, _, _ = drive(
        [question_reply("What module?"), plan_reply("Sketch it"), code_reply("a()"),
         suspect_reply(), verdict_reply("pass")],
        intent=GEAR, ask=lambda _: "")

    assert events.first(PLAN).data["unanswered"] == ["What module?"]


def test_a_replan_does_not_refill_the_re_read_ceiling():
    """
    The counters used to be locals of one Plan, so every re-plan started the
    questioning over: the same question went to the user twice and the sheet
    could be re-read without end. FCC-96.
    """
    asked = []

    _, events, _, _ = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply({"datums": [{"name": "d1"}]}),
        plan_reply("Impossible", satisfies=everything(A_PART)), code_reply("nope()"),
        question_reply("q2"),
        plan_reply("Possible", satisfies=everything(A_PART)), code_reply("yes()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], applies=[failed("one")], attachments=[RENDER_PNG],
        budgets=Budgets(rereads=1, attempts_per_step=1),
        ask=lambda q: asked.append(q) or "an answer")

    assert asked == []
    assert [answer.data["by"] for answer in events.of(ANSWER)] == ["drawing", "nobody"], \
        "the re-plan refilled the re-read ceiling"


def test_a_replan_after_a_reread_is_handed_the_change_set_not_the_whole_part():
    """
    FCC-311: the sheet answering Plan's own question grows the description
    between two calls to Plan within the same re-plan — the one place that
    happens — so the second call is handed what changed, named by id, rather
    than the whole re-serialized part beside the one it already saw.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply("q1"),
        design_reply({"datums": [{"name": "d1", "is": "20 mm from the left end"}]}),
        plan_reply("Impossible", satisfies=everything(A_PART)), code_reply("nope()"),
        question_reply("q2"),
        plan_reply("Possible", satisfies=everything(A_PART)), code_reply("yes()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], applies=[failed("one")], attachments=[RENDER_PNG],
        budgets=Budgets(rereads=1, attempts_per_step=1))

    reread_plan = json.loads(llm.calls[3][-1]["content"])
    assert reread_plan["what_the_part_is"] == [
        {"section": "datums", "kind": "added", "id": "d1",
         "now": {"name": "d1", "is": "20 mm from the left end"}}]
    assert events.last(CHANGE_SET).data["changes"] == reread_plan["what_the_part_is"]


def test_with_no_attachment_a_question_is_settled_by_plan_itself():
    """
    No sheet to ask and nobody to ask either, so no turn is spent on either:
    Plan is told to choose, and the only turn Design costs is the one it
    describes the part in. FCC-93, ADR-0022.
    """
    asked = []

    result, _, llm, _ = drive(
        [question_reply(MISSING), plan_reply("Sketch it, assumed module 2"),
         code_reply("a()"), suspect_reply(), verdict_reply("pass")],
        intent=GEAR, ask=lambda q: asked.append(q) or "module 2")

    assert result.ok, result.reason
    assert asked == []
    assert len(llm.calls) == 6, "a turn was spent re-reading a drawing that is not there"


def test_a_question_alongside_steps_is_still_a_plan():
    """A question is not a Plan with a caveat; steps win and nothing is asked."""
    asked = []

    result, events, _, _ = drive(
        [json.dumps({"declare": {"question": "how big?", "steps": ["Sketch it"]}}),
         code_reply("a()"), suspect_reply(), verdict_reply("pass")],
        intent=GEAR, ask=lambda q: asked.append(q) or "")

    assert result.ok
    assert asked == []
    assert events.first(QUESTION) is None


def test_a_replan_after_a_handback_may_ask_rather_than_planning_blind():
    """
    A re-plan that needs a value nobody gave it used to answer with a question
    and no Steps, and the loop took `planned.steps` blind: the question was
    dropped and the revised Plan was empty. It may still ask; what it gets back
    is "choose it yourself", and the Plan that follows is a Plan. FCC-67.
    """
    asked = []

    result, events, _, _ = drive(
        [
            plan_reply("Impossible"),
            code_reply("nope1()"), code_reply("nope2()"), code_reply("nope3()"),
            question_reply(MISSING),
            plan_reply("Possible, assumed module 2"),
            code_reply("yes()"),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
        ask=lambda question: asked.append(question) or "module 2, 20 teeth",
    )

    assert asked == [], "the re-plan put its question to a person: %s" % asked
    assert result.ok, result.reason
    assert events.first(PLAN_REVISED).data["steps"] == ["Possible, assumed module 2"]
    assert events.first(PLAN_REVISED).data["unanswered"] == [MISSING]


def test_a_replan_after_a_rejected_verdict_may_ask_too():
    asked = []

    result, events, _, _ = drive(
        [
            plan_reply("Make it"), code_reply("make()"), suspect_reply(), verdict_reply("fail", "20mm, not 10mm"),
            question_reply(MISSING),
            plan_reply("Make it 10mm across the flats"), code_reply("fix()"),
            suspect_reply(), verdict_reply("pass"),
        ],
        ask=lambda question: asked.append(question) or "10mm across the flats",
    )

    assert asked == []
    assert result.ok, result.reason
    assert events.first(PLAN_REVISED).data["unanswered"] == [MISSING]


def test_no_plan_without_steps_ever_reaches_execute(monkeypatch):
    """
    Unreachable through `plan()`, which either has Steps, or a question, or
    raises. The guard is the cheap insurance that no future path sends Review
    over a Document nothing changed. FCC-67.
    """
    import core.loop.plan as loop_plan_module
    from core.loop.plan import Plan

    monkeypatch.setattr(loop_plan_module, "plan",
                        lambda *args, **kwargs: Plan(steps=[]))

    result, _, _, tools = drive([plan_reply("never asked for")])

    assert result.blocked
    assert "no Steps" in result.reason
    assert tools.applied_codes() == []

















def test_the_assertion_phase_runs_after_the_last_step_and_between_the_two_reviews():
    """
    Once per run, on the finished Document, after `review_suspect` has had its
    look and before the gating Review sees a verdict-worthy result: what is
    checked is how close the built part is to the goal, not the arithmetic of
    a state that may not be ready yet, and not a second opinion Review has
    already given without it. Read-only, so it goes through a Tool of its own
    rather than through apply. FCC-76, FCC-257, FCC-258.
    """
    _, events, llm, tools = a_run_over_the_nut(
        planning_the_nut("Sketch it", "Pad it"),
        code_reply("sketch()", "pad()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
    )

    assert [name for name, _ in tools.calls
            if name in ("apply_step", "check_assertions")] == [
        "apply_step", "apply_step", "check_assertions", "check_assertions"]
    checked = [args for name, args in tools.calls if name == "check_assertions"][-1]
    assert checked["code"] == CHECKS.strip(), "the Tool ran something other than the checks"

    kinds = [e.kind for e in events]
    assert kinds.index(REVIEW_SUSPECTED) < kinds.index(ASSERTION) < kinds.index(REVIEW_VERDICT)
    assert events.first(ASSERTION).data["ok"] is True


def test_review_suspecting_nothing_still_lets_assertion_gate_the_verdict():
    """
    `review_suspect` raising no suspicion is not the same as Assertion having
    nothing to do: Assertion still runs its full acceptance-list check and
    still decides pass/fail, and the gating Review — when Assertion passes —
    still runs after it (FCC-258).
    """
    passing, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"),
        suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"))

    assert passing.ok
    assert passing.unmet == []
    kinds = [e.kind for e in events]
    assert kinds.index(ASSERTION) < kinds.index(REVIEW_VERDICT)

    failing, failing_events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"),
        suspect_reply(), assertion_reply(CHECKS),
        checks=[assertion_failed(SIX_FAILED)],
        decide=lambda failure: "accept",
    )

    assert failing.ok, "the user took it, so the run passed"
    assert failing.unmet, "Assertion's failed checks did not reach the result"
    assert REVIEW_VERDICT not in [e.kind for e in failing_events], \
        "a failure the user accepted never reached the gating Review"


def test_a_suspicion_never_gates_but_reaches_assertions_acceptance_list():
    """
    `review_suspect` naming something is not a verdict: the pass carries on to
    Assertion regardless of what it raised, and what it raised is folded into
    the very list Assertion measures, carrying `(suspected)` the way a Plan
    entry carries `(read)` or `(assumed)` (FCC-257, FCC-258).
    """
    raised = "envelope → width: 40 mm"
    result, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"),
        suspect_reply(raised), assertion_reply(CHECKS), verdict_reply("pass"))

    assert result.ok
    assert events.first(REVIEW_SUSPECTED).data["suspected"] == [raised + " (suspected)"]

    all_events = list(events)
    between = all_events[all_events.index(events.first(REVIEW_SUSPECTED)) + 1:
                         all_events.index(events.first(ASSERTION))]
    assert QUESTION not in [e.kind for e in between], (
        "a suspicion asked the user before Assertion had a chance to measure it")
    assert PLAN_REVISED not in [e.kind for e in between], (
        "a suspicion sent the run back to Plan on its own")

    assertion_request = json.loads(llm.calls[-2][-1]["content"])
    assert raised + " (suspected)" in assertion_request["acceptance"]
    for entry in ACCEPTANCE:
        assert entry in assertion_request["acceptance"], (
            "a suspected row must not replace the acceptance list, only extend it")


def test_a_refuted_suspicion_fails_assertion_and_replans_then_passes():
    """
    The other half of the confirmed suspicion above (FCC-258's own Testing
    Strategy: a suspicion that Assertion measures and finds false, with the
    run still passing once it is fixed). A `suspected` row is measured
    exactly like any other acceptance entry — a failed check on it rejects
    through the same `replan()` path as a Plan-authored entry, no separate
    remediation, and the run's own retry clears it.
    """
    raised = "thread → pitch: 1.25 mm"
    result, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"),
        suspect_reply(raised), assertion_reply(CHECKS),
        planning_the_nut("Make it 13 mm across the flats"), code_reply("fix()"),
        suspect_reply(), assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(ASSERT_FAILED),
                SELF_INTERSECTION_HELD, None])

    assert result.ok
    assert result.verdict == "pass"
    assert events.first(REVIEW_SUSPECTED).data["suspected"] == [raised + " (suspected)"]
    assert events.first(ASSERTION).data["ok"] is False
    assert events.first(PLAN_REVISED).data["because"] == "the acceptance checks failed"
    assert events.first(REVIEW_VERDICT).data["verdict"] == "pass"

    first_assertion_request = json.loads(llm.calls[4][-1]["content"])
    assert raised + " (suspected)" in first_assertion_request["acceptance"]


def test_an_empty_acceptance_list_costs_no_llm_call():
    """
    Nothing to check means no provider is ever asked to write one. The
    deterministic self-intersection check still runs — it is unconditional
    (FCC-256) — but it costs one `check_assertions` call, not a phase, and a
    clean Document still passes with nothing left unmet. FCC-76.
    """
    result, events, llm, tools = drive([
        plan_reply("Add a box"),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert result.unmet == [], "a clean Document must not gate the verdict"
    assert len(llm.calls) == 5, "an extra provider call on a Plan with nothing to check"
    assert events.first(ASSERTION) is None
    ran = [args["code"] for name, args in tools.calls if name == "check_assertions"]
    assert len(ran) == 1 and "BOPAlgo_SelfIntersect" in ran[0], \
        "the self-intersection check must still run deterministically"


def test_a_self_intersecting_document_is_caught_even_with_no_acceptance_list():
    """
    FCC-256 AC1: "caught once ... before Review ever judges a render of it" is
    unconditional. A Plan that writes no acceptance entries must not skip the
    self-intersection check, and a failing check must still gate the verdict
    through the same `decided()`/`replan()` path a written check uses — not
    return `None` and let the run pass regardless.
    """
    result, events, llm, tools = drive([
        plan_reply("Add a box"),
        code_reply("box()"),
        verdict_reply("pass"),
    ], checks=[{"ok": False, "traceback": "AssertionError: these did not hold: "
                                          "Twist self-intersects (BOPAlgo_SelfIntersect)"}],
        decide=lambda why: "accept")

    ran = [args["code"] for name, args in tools.calls if name == "check_assertions"]
    assert ran and "BOPAlgo_SelfIntersect" in ran[0]
    assert events.first(ASSERTION).data["unmet"] == \
        ["Twist self-intersects (BOPAlgo_SelfIntersect)"]
    assert result.unmet == ["Twist self-intersects (BOPAlgo_SelfIntersect)"], \
        "the failure must reach the caller, not be swallowed as None"
    assert "acceptance checks did not pass" in result.reason, \
        "the failure must have gone through the decided()/replan() gate, not a bare pass"
    assert not any(call[0]["content"].startswith(prompt("assertion")) for call in llm.calls), \
        "an empty acceptance list must still never reach a provider for assertion"


def test_failed_assertions_return_to_plan_and_never_to_execute():
    """
    A failed check is a rejected result: revision, feedback, a new Plan and a
    new Plan Gate. The traceback is what Plan is told, and Execute is told
    none of it — the mirror of a failed verdict. FCC-76.
    """
    _, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        planning_the_nut("Make it 13 mm across the flats"),
        code_reply("fix()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(ASSERT_FAILED),
                SELF_INTERSECTION_HELD, None])

    to_plan = json.dumps(llm.calls[5])
    to_execute = json.dumps(llm.calls[6])

    assert "13.0 mm across the flats" in to_plan
    assert "AssertionError" in to_plan
    assert "AssertionError" not in to_execute, "the failed check reached Execute"
    assert events.first(PLAN_REVISED).data["steps"][0].startswith(
        "Make it 13 mm across the flats")
    assert events.first(ASSERTION).data["ok"] is False


def test_a_feature_that_moved_without_a_retry_touching_it_fails_a_later_assertion_pass():
    """
    FCC-260: the geometry-diff defect. A feature no Step named and no retry
    touched moved between two Assertion passes of the same run — the "slot
    shortened until it passes" class `config/prompts/assertion.md` documents,
    caught here rather than only when it happens to sit inside an unmet
    acceptance entry.

    Review rejects the first pass for an unrelated reason, so a second full
    cycle runs; nothing in it retries a Step, so `entries_a_retry_touched` is
    empty. The `Fillet` fingerprint moves anyway (the fake `check_assertions`
    queue says so, standing in for a real one drifting on this run) and the
    second Assertion pass fails on it even though both checks in the fake
    themselves reported `ok: True`.
    """
    result, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        verdict_reply("fail", "wrong flank"),
        planning_the_nut("Make it again"),
        code_reply("fix()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        budgets=Budgets(revisions=1),
        checks=[
            SELF_INTERSECTION_HELD,
            {"ok": True, "confirmed": [ACCEPTANCE[0]],
             "geometry": {"Fillet": [10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]}},
            SELF_INTERSECTION_HELD,
            {"ok": True, "confirmed": [ACCEPTANCE[0]],
             "geometry": {"Fillet": [15.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]}},
        ])

    assert result.blocked
    second_pass = events.of(ASSERTION)[-1].data
    assert second_pass["ok"] is False
    assert "Fillet" in second_pass["traceback"]
    assert "Fillet" in second_pass["unmet"]


def test_a_feature_named_in_what_a_retry_touched_does_not_fail_a_later_assertion_pass():
    """
    The mirror of the test above: the same feature moves, but the retry that
    passed a Step in the second cycle already named it, so the geometry-diff
    check is not the thing that sends the workaround back — the existing
    Assertion verdict decides alone, the same way it did before FCC-260.
    """
    A_SPLIT = {"features": [{"id": "ring-split", "kind": "2 mm split",
                             "width": {"value": "2 mm", "flag": "read"}}]}

    _, events, llm, _ = drive([
        design_reply(A_SPLIT),
        plan_reply("Pocket the split", satisfies=everything(A_SPLIT)),
        code_reply("split_through()"),
        suspect_reply(),
        assertion_reply("assert True, 'the split'"),
        verdict_reply("fail", "still off"),
        plan_reply("Pocket the split again", satisfies=everything(A_SPLIT)),
        code_reply("split_through_2()"),
        code_reply("split_shortened()"),
        suspect_reply(),
        assertion_reply("assert True, 'the split'"),
        verdict_reply("pass"),
    ],
        budgets=Budgets(revisions=1),
        applies=[None, failed("this Step left a Body in more than one piece"),
                {"status": "applied", "created": ["ring-split"], "description": DESCRIPTION}],
        attachments=[RENDER_PNG],
        checks=[
            SELF_INTERSECTION_HELD,
            {"ok": True, "geometry": {"ring-split": [10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]}},
            SELF_INTERSECTION_HELD,
            {"ok": True, "geometry": {"ring-split": [8.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]}},
        ])

    touched = _turns_of(llm, "assertion")[1]["entries_a_retry_touched"]
    assert "ring-split" in " ".join(touched)
    assert events.of(ASSERTION)[-1].data["ok"] is True, \
        "a feature the retry already named should not fail the geometry check"


def test_a_run_out_of_revisions_names_the_assertions_not_review():
    """
    FCC-22's separate accounting, extended: a run stopped by failed checks must
    not tell the user Review kept rejecting the result. FCC-76.
    """
    result, _, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS)] * 5,
        budgets=Budgets(revisions=2),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(ASSERT_FAILED)] * 5,
    )

    assert result.blocked
    assert "acceptance checks failed" in result.reason
    assert "Review rejected" not in result.reason
    assert "you amended" not in result.reason


def test_review_is_told_the_facts_the_checks_confirmed():
    """
    Measured facts beside the four views, so Review judges what the checks did
    not cover rather than re-judging what they did. The views do not go
    (ADR-0015). FCC-76.
    """
    _, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply(CHECKS, uncovered=[ACCEPTANCE[1]]),
        verdict_reply("pass"),
        views=[RENDER_PNG] * 4)

    review_request = json.loads(llm.calls[-1][1]["content"][0]["text"].split("\n\n")[0])

    assert review_request["facts_the_checks_confirmed"] == [ACCEPTANCE[0]]
    assert review_request["what_the_checks_could_not_cover"] == [ACCEPTANCE[1]]
    assert events.first(ASSERTION).data["uncovered"] == [ACCEPTANCE[1]]


def test_review_is_told_not_to_fail_on_a_suspicion_the_checks_never_confirmed():
    """
    A mirrored flank or a displaced hole reads from the render alone, on a
    face no check happened to cover — `ACCEPTANCE[1]` stays uncovered here,
    the way FCC-76 leaves a check that could not run. `review.md` is what
    stops that reading from becoming its own `fail`: the same rule that
    already keeps a dimension off the picture (`a diameter was never a
    pixel`), generalised from dimensions to any visual suspicion (FCC-204).
    Proof has to be the request `review()` actually receives, not the static
    prompt text: `checked=` is the channel this carries, and it stays gated
    to `review()` alone (not `review_suspect()`, which runs before Assertion
    and cannot see what the checks found).
    """
    result, _, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply(CHECKS, uncovered=[ACCEPTANCE[1]]),
        verdict_reply("pass"),
        views=[RENDER_PNG] * 4)

    review_request = json.loads(llm.calls[-1][1]["content"][0]["text"].split("\n\n")[0])

    assert review_request["what_the_checks_could_not_cover"] == [ACCEPTANCE[1]]
    assert result.ok
    assert result.verdict == "pass"


def test_a_broken_check_is_rewritten_and_never_reaches_plan_as_geometry():
    """
    An AttributeError in the checking code says nothing about the nut. Read as
    a failed model it would send Plan to rebuild a part that is already right,
    so it is handed back to the phase that wrote it, on the same attempts
    budget Execute uses. FCC-76.
    """
    result, events, llm, tools = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply("bad_check()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(CHECK_IS_BROKEN), None])

    assert result.ok
    assert events.first(PLAN_REVISED) is None, "a broken check sent Plan to rebuild"
    assert len([c for c in tools.calls if c[0] == "check_assertions"]) == 3
    assert "AttributeError" in json.dumps(llm.calls[5])


def test_a_check_that_cannot_be_written_lets_review_judge_alone():
    """
    Attempts spent on a check that never runs is not a reason to rebuild a good
    model, and not a reason to lose a finished run either: the phase gives up,
    says so, and everything it failed to cover goes to Review. FCC-76.
    """
    result, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD] + [assertion_failed(CHECK_IS_BROKEN)] * 3)

    assert result.ok
    assert events.first(PLAN_REVISED) is None
    assert events.first(ASSERTION).data["ok"] is None, "an unwritable check is not a verdict"
    assert events.first(ASSERTION).data["confirmed"] == [], \
        "a check that never ran measured nothing to confirm"


def test_stop_during_the_checks_does_not_wait_out_three_provider_calls():
    """
    Stop is a decision, not a request. Three attempts at writing the checks is
    long enough that a run which ignored it there would feel hung. FCC-76.
    """
    llm = ScriptedLLMClient([
        design_reply(A_NUT),
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        verdict_reply("pass"),
    ])
    tools = FakeTools(checks=[assertion_failed(CHECK_IS_BROKEN)] * 3)
    events = EventStream()

    def cancelled():
        """
        Runs clean until the checks start; then the user presses Stop.
        
        One physical cavity, seen in three views and reconciled into one
        feature by the phase that owns the description. Steps 4, 5 and 6 of
        the recorded run cut it three times, once per view.
        """
        return any(name == "check_assertions" for name, _ in tools.calls)

    result = run("Make a nut", llm=llm, tools=tools.registry, events=events,
                 cancelled=cancelled, attachments=[RENDER_PNG])

    assert result.blocked
    assert "Stopped by you" in result.reason
    assert len([c for c in tools.calls if c[0] == "check_assertions"]) == 1





def test_the_acceptance_is_the_descriptions_own_entries_and_not_plans_paraphrase():
    """
    Between the sheet and the assert there were two rewrites, and in the run
    that named this defect both lied: every entry claimed `(read)` although the
    values came out of a re-read, and the depth entry carried 50 mm, so
    Assertion would have measured 50 mm on the built part and reported a pass on
    a part the drawing never showed. Derived, there is nothing to rewrite.
    FCC-94.
    """
    _, events, llm, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply("Pad the blank", "Cut the central opening",
                   satisfies=everything(THE_CAVITY)),
        code_reply("pad()", "cut()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    shown = events.first(PLAN).data["acceptance"]
    assert shown == ["envelope → depth front to rear: 100 mm (read)",
                     "features → central-opening → position → the front face: 20 mm (read)"]
    assert _turns_of(llm, "assertion")[0]["acceptance"] == shown


def test_a_value_the_user_assumed_is_still_marked_assumed_in_the_acceptance():
    """
    The kind of value most likely to be wrong is the one somebody settled, so it
    is a criterion and it says at the Gate that it is theirs to correct. FCC-94.
    """
    told = {"envelope": {"length": {"value": "150 mm", "flag": "assumed"}}}

    _, events, _, _ = drive([
        design_reply(told),
        plan_reply("Pad it 150 mm"), code_reply("pad()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN).data["acceptance"] == ["envelope → length: 150 mm (assumed)"]


def test_a_step_says_at_the_gate_which_part_of_the_part_it_is_for():
    """
    A reader who cannot tell from the Gate which entry a Step is for cannot tell
    that one cavity has been planned three times, which is what Steps 4, 5 and 6
    of the recorded run were. FCC-94.
    """
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank 100 mm front to rear",
                    "satisfies": [CAVITY_DEPTH]},
                   {"do": "Cut the central opening, both faces",
                    "satisfies": [CAVITY_POCKET]}),
        code_reply("pad()", "cut()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN).data["steps"] == [
        "Pad the blank 100 mm front to rear (satisfies: envelope → depth front to rear)",
        "Cut the central opening, both faces (satisfies: features → central-opening → "
        "position → the front face)",
    ]


def test_a_plan_that_leaves_a_read_entry_unconsumed_is_planned_again():
    """
    The headline defect: the revised Plan dropped the 100 mm front to rear and
    no rule, prompt or judgement noticed. A set difference over addresses
    notices, and the run says which entry went missing. FCC-94.
    """
    result, events, llm, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Cut the central opening", "satisfies": [CAVITY_POCKET]}),
        plan_reply({"do": "Pad the blank 100 mm front to rear",
                    "satisfies": [CAVITY_DEPTH, CAVITY_POCKET]}),
        code_reply("pad()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None, "a Plan that dropped the depth reached the user as final"
    assert CAVITY_DEPTH in revised.data["reason"]

    told = json.loads(llm.calls[2][-1]["content"])["what_went_wrong_last_time"]
    assert CAVITY_DEPTH in told
    assert CAVITY_POCKET not in told, "an entry that was cited was reported as missing"


def test_a_plan_that_leaves_nothing_unconsumed_is_never_replanned():
    """Coverage, not exclusivity: one Step may satisfy several entries."""
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank and cut the opening",
                    "satisfies": [CAVITY_DEPTH, CAVITY_POCKET]}),
        code_reply("build()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN_REVISED) is None


def test_a_plan_citing_the_whole_line_it_was_handed_has_cited_the_entry():
    """
    `what_the_plan_must_satisfy` gives the address and the value on one line,
    and a Plan copying that line means the entry the line is. Refusing it would
    make the check fail on the one Plan that did exactly as it was told, and
    every run would replan to a block. FCC-94.
    """
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank and cut the opening",
                    "satisfies": [CAVITY_DEPTH + ": 100 mm (read)",
                                  CAVITY_POCKET + ": 20 mm (read)"]}),
        code_reply("build()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN_REVISED) is None


def test_a_step_carries_the_address_it_cites_and_not_the_paragraph_behind_it():
    """
    What the Gate shows is prose, and a citation pasted whole drags the
    description's own paragraphs into it: Step 1 of the Model Mania run reached
    the user as 3553 characters, of which 1000 were the Step and the rest were
    twenty-two entries repeated with their values — values Execute is handed
    separately, addressed, in `what_each_entry_says`. The address says which
    part of the part the Step is for, which is what the citation is in the prose
    for; the flag stays, because an assumed value is the one thing the Gate must
    not let past unread. FCC-127.
    """
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank and cut the opening",
                    "satisfies": [CAVITY_DEPTH + ": 100 mm (read)",
                                  CAVITY_POCKET + ": 20 mm (read)"]}),
        code_reply("build()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    step = events.first(PLAN).data["steps"][0]

    assert step == ("Pad the blank and cut the opening (satisfies: %s (read), %s (read))"
                    % (CAVITY_DEPTH, CAVITY_POCKET))
    assert "100 mm" not in step and "20 mm" not in step

def test_a_suspected_citation_keeps_its_flag_through_the_trim():
    """
    `_in_the_prose` trims a citation to its address and flag (FCC-127); a
    `suspected` row must survive that trim the same way `read`, `derived`
    and `assumed` already do, or a Plan citing a carried-forward suspicion
    in a later Step loses the one thing that told the Gate it was never
    confirmed (FCC-258 AC3).
    """
    assert _in_the_prose("thread \u2192 pitch: 1.25 mm (suspected)") == (
        "thread \u2192 pitch (suspected)")


def test_one_entry_may_feed_several_steps():
    """
    An overall height feeds both the base solid and a through hole's depth, and
    citing it twice is right. What may not happen is an entry feeding no Step.
    FCC-94.
    """
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank 100 mm front to rear",
                    "satisfies": [CAVITY_DEPTH]},
                   {"do": "Cut the opening 100 mm deep, front to rear",
                    "satisfies": [CAVITY_DEPTH, CAVITY_POCKET]}),
        code_reply("pad()", "cut()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN_REVISED) is None


def test_no_step_has_to_build_a_feature_the_later_page_replaced():
    """
    The Model Mania sheet's second page replaces the horizontal mounting ear
    with an arm at 45°. With both on the description as `read`, the coverage
    check required a Step for the ear's bore as well: the revision that dropped
    it was handed back for leaving an entry unplanned, and a whole revision went
    on a hole the drawing had deleted. FCC-127.
    """
    superseded = {
        "features": [
            {"id": "left-ear", "kind": "horizontal mounting ear",
             "superseded_by": "angled-arm",
             "position": {"the left end face": read("15 mm")}},
            {"id": "angled-arm", "kind": "mounting arm at 45°",
             "position": {"the ring axis": read("73 mm")}},
        ],
    }

    result, events, _, _ = drive([
        design_reply(superseded),
        plan_reply({"do": "Pad the arm at 45°",
                    "satisfies": ["features → angled-arm → position → the ring axis"]}),
        code_reply("arm()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok
    assert events.first(PLAN_REVISED) is None
    assert events.first(PLAN).data["acceptance"] == [
        "features → angled-arm → position → the ring axis: 73 mm (read)"]


def test_an_assumed_entry_is_not_something_a_plan_can_be_refused_over():
    """
    A Plan cannot be refused for failing to build a number nobody supplied. The
    entry is still checked; it is not a coverage duty. FCC-94.
    """
    told = {"envelope": {"length": {"value": "150 mm", "flag": "assumed"}}}

    result, events, _, _ = drive([
        design_reply(told),
        plan_reply("Pad it"), code_reply("pad()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok
    assert events.first(PLAN_REVISED) is None







def test_a_revision_that_plans_only_the_unbuilt_steps_passes_the_coverage_check():
    """
    The check measures the Plan against the description, and after a handback
    the question is narrower: what is left is the description less what the
    applied Steps already satisfy. Without the subtraction the recorded run was
    told sixteen entries were unplanned, every one of them standing in the
    Document, and spent a second revision on a Plan that restated all eight
    Steps — four of which Execute then applied nothing for. FCC-148.
    """
    result, events, llm, tools = drive(_four_steps_applied_then_a_handback(),
                                       attachments=[RENDER_PNG], budgets=Budgets(revisions=5))

    assert result.ok
    assert len(_turns_of(llm, "plan")) == 2, "the revision was replanned for coverage"
    assert tools.applied_codes() == ["part1()", "part2()", "part3()", "part4()", "bore()"]
    reasons = [str(event.data.get("reason")) for event in events.of(PLAN_REVISED)]
    assert len(reasons) == 1 and "the bore misses the web as planned" in reasons[0]


def test_an_entry_an_applied_step_satisfied_is_not_reported_as_unplanned():
    """
    The four entries the Document already answers for never reach Plan as a
    complaint. FCC-148.
    """
    _, _, llm, _ = drive(_four_steps_applied_then_a_handback(),
                         attachments=[RENDER_PNG], budgets=Budgets(revisions=5))

    complaints = [turn.get("what_went_wrong_last_time", "")
                  for turn in _turns_of(llm, "plan")]

    assert any(complaints), "the revision was not told what went wrong"
    assert not [entry for entry in BUILT
                if any(entry in complaint for complaint in complaints)]


def test_an_entry_no_step_ever_landed_for_is_still_unplanned():
    """
    The subtraction is of what was applied, not of what was planned: a revision
    that drops an entry no Step in the Document answers for is still refused.
    FCC-148.
    """
    result, events, _, _ = drive([
        design_reply(THE_BRACKET),
        plan_reply(*[{"do": "Pad part %d" % (number + 1), "satisfies": [entry]}
                     for number, entry in enumerate(BUILT)],
                   {"do": "Bore the central hole", "satisfies": list(LEFT_TO_BUILD)}),
        json.dumps({"declare": {"steps": [{"step": number, "code": "part%d()" % number,
                               "label": "Part %d" % number} for number in range(1, 4)],
                    "unbuildable": "the fourth pad has nothing to stand on"}}),
        plan_reply({"do": "Bore the central hole", "satisfies": list(LEFT_TO_BUILD)}),
        plan_reply({"do": "Pad part 4", "satisfies": [BUILT[3]]},
                   {"do": "Bore the central hole", "satisfies": list(LEFT_TO_BUILD)}),
        code_reply("part4()", "bore()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(revisions=5))

    assert result.ok
    reasons = [event.data.get("reason") for event in events.of(PLAN_REVISED)]
    assert any(BUILT[3] in str(reason) for reason in reasons), \
        "an entry nothing in the Document builds passed the coverage check"


def test_a_plan_that_never_learns_to_cite_stops_the_run_and_says_why():
    """
    The coverage re-plan spends the same shared ceiling as every other cause, so
    a prompt regression stops the run with a reason instead of looping — and
    FCC-22's separate accounting names the right cause. FCC-94.
    """
    result, _, _, tools = drive(
        [design_reply(THE_CAVITY)] + [plan_reply("Cut it")] * 5,
        attachments=[RENDER_PNG], budgets=Budgets(revisions=2),
    )

    assert result.blocked
    assert "left part of the description unplanned" in result.reason
    assert "Review rejected" not in result.reason
    assert tools.applied_codes() == [], "a Plan that dropped an entry built something"


def test_a_step_whose_sum_does_not_come_out_never_reaches_the_gate():
    """
    The recorded run's Plan assumed an 80.5 mm station for a 45° arm "to place
    the left extreme at the 0 mm tangent plane". It places it at 8.58, and the
    only thing in the run that multiplied by the cosine was Execute — after the
    Gate, after the user had approved it, and at the cost of a revision each
    time. The sum is worked out where the number is still cheap. FCC-127.
    """
    result, events, _, tools = drive([
        plan_reply("Pad the arm to an assumed 80.5 mm station, so the left extreme "
                   "is at 65.5 - 80.5 * cos(45°) = 0 mm"),
        plan_reply("Pad the arm to an assumed 92.63 mm station, so the left extreme "
                   "is at 65.5 - 92.63 * cos(45°) = 0 mm"),
        code_reply("arm()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None
    assert "and it is 8.58" in revised.data["reason"]
    assert revised.data["because"] == "the Plan's own arithmetic did not come out"
    assert len(events.of(GATE)) == 2, "the wrong sum was put to the user"
    assert tools.applied_codes() == ["arm()"]


def test_a_plan_whose_arithmetic_never_comes_out_stops_the_run():
    """
    The same shared ceiling as every other cause, so a Plan that keeps writing
    sums that do not come out stops with a reason instead of looping. FCC-127.
    """
    result, _, _, tools = drive(
        [plan_reply("Pad it to 65.5 - 80.5 * cos(45°) = 0 mm")] * 5,
        budgets=Budgets(revisions=2),
    )

    assert result.blocked
    assert "arithmetic did not come out" in result.reason
    assert tools.applied_codes() == []


def test_a_run_with_no_attachment_still_plans_and_still_shows_an_acceptance():
    """
    With no drawing the description holds only what the user's answers supplied,
    and where it holds nothing at all an empty Acceptance means no Assertion
    phase rather than a phase that checks nothing — the behaviour from before
    there was a list. FCC-94.
    """
    told = {"envelope": {"length": {"value": "150 mm", "flag": "assumed"}}}

    result, events, _, tools = drive(
        [design_reply(told, question=MISSING), design_reply(),
         plan_reply("Pad it 150 mm", satisfies=everything(told)), code_reply("pad()"),
         suspect_reply(), assertion_reply("assert True"), verdict_reply("pass")],
        attachments=[RENDER_PNG], ask=lambda _: "150 mm long")

    assert result.ok
    assert events.first(PLAN).data["acceptance"] == ["envelope → length: 150 mm (assumed)"]
    assert any(name == "check_assertions" for name, _ in tools.calls)




def test_the_entries_that_did_not_hold_come_out_on_the_result():
    """
    A recorded run finished eight Steps, failed six of its acceptance entries,
    and stopped. Nothing carried that out of the run: the choice the user was
    offered was made in the panel and never reached the transcript, and the next
    drawing was planned against the same Document as though the part were sound.
    The second run then rebuilt on top of six entries that were already wrong.
    """
    result, _, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass")] * 5,
        budgets=Budgets(revisions=1),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(SIX_FAILED)] * 5,
    )

    assert result.unmet == ["envelope \u2192 across the flats: 13.0 mm (read)",
                            "envelope \u2192 height: 6.5 mm (read)"]


def test_a_result_the_user_took_as_it_is_still_says_what_is_wrong_with_it():
    """
    ACCEPT ends the run as a pass. It does not make the part right, and the
    Document the next Intent starts from still has both entries wrong in it.
    """
    from core.loop import ACCEPT

    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(SIX_FAILED)],
        decide=lambda failure: ACCEPT,
    )

    assert result.ok, "the user took it, so the run passed"
    assert result.unmet == ["envelope \u2192 across the flats: 13.0 mm (read)",
                            "envelope \u2192 height: 6.5 mm (read)"]


def test_a_run_whose_checks_passed_leaves_nothing_outstanding():
    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"))

    assert result.ok
    assert result.unmet == []


def test_a_run_stopped_partway_still_reports_what_was_wrong():
    """
    Every way out of the loop carries it, not only the tidy one — a run that
    ran out of revisions is exactly the run whose Document nobody fixed.
    """
    result, _, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS)] * 5,
        budgets=Budgets(revisions=1),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(SIX_FAILED)] * 5,
    )

    assert result.blocked
    assert result.unmet


def test_the_assertion_event_names_the_entries_and_not_only_the_traceback():
    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(SIX_FAILED)],
        decide=lambda failure: "accept",
    )

    assert events.first(ASSERTION).data["unmet"] == [
        "envelope \u2192 across the flats: 13.0 mm (read)",
        "envelope \u2192 height: 6.5 mm (read)"]






def test_a_mixed_result_reports_both_what_held_and_what_did_not():
    """
    The change this ticket makes: a run that measured some entries true and
    some false reports both, read off the worker's own `held` and `failed`
    rather than the failure branch's old hardcoded `confirmed=[]`.
    """
    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD,
                assertion_mixed([ACCEPTANCE[0]], [ACCEPTANCE[1]], MIXED_TRACEBACK)],
        decide=lambda failure: "accept",
    )

    data = events.first(ASSERTION).data
    assert data["confirmed"] == [ACCEPTANCE[0]]
    assert data["unmet"] == [ACCEPTANCE[1]]
    assert sorted(data["confirmed"] + data["unmet"] + data["uncovered"]) == sorted(ACCEPTANCE)


def test_a_passing_runs_confirmed_is_measured_not_inferred():
    """
    A script that measured fewer entries than the acceptance list has, without
    declaring the rest `uncovered`, yields a `confirmed` shorter than the
    list — the entry it never touched is silently missing from both
    `confirmed` and `unmet`, rather than inferred as held.
    """
    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, {"ok": True, "held": [ACCEPTANCE[0]]}],
    )

    data = events.first(ASSERTION).data
    assert data["confirmed"] == [ACCEPTANCE[0]]
    assert ACCEPTANCE[1] not in data["confirmed"]
    assert ACCEPTANCE[1] not in (data.get("unmet") or [])


def test_a_check_that_measured_but_never_defined_held_still_reports_confirmed_empty():
    """
    A script written to the shape before this ticket — a single `failed` list
    and no `held` — has nothing this phase can call `held`, so `confirmed`
    falls back to today's `[]`. `unmet` is still read straight off the
    worker's own `failed` rather than parsed from the traceback, which is
    how it differs from `_unmet_in(ASSERT_FAILED)`'s wording of the same
    entry.
    """
    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD,
                {"ok": False, "traceback": ASSERT_FAILED, "failed": [ACCEPTANCE[0]]}],
        decide=lambda failure: "accept",
    )

    data = events.first(ASSERTION).data
    assert data["confirmed"] == []
    assert data["unmet"] == [ACCEPTANCE[0]]


def test_a_document_that_cannot_recompute_leaves_the_whole_list_outstanding():
    """Nothing was measured, so nothing can be named, and nothing is assumed right."""
    broken = ("Traceback (most recent call last):\n"
              "RuntimeError: recompute left these objects in error:\n"
              "Pocket (PartDesign::Pocket): Links go out of the allowed scope\n")

    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(broken)],
        decide=lambda failure: "accept",
    )

    assert result.unmet == ACCEPTANCE


def test_what_the_user_decided_about_a_failed_result_is_on_the_stream():
    decided = []

    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(SIX_FAILED)],
        decide=lambda failure: decided.append(failure) or "accept",
    )

    assert decided, "the user was never asked"
    question = events.of(QUESTION)[-1]
    answer = events.of(ANSWER)[-1]
    assert "what to do about it is yours to say" in question.data["text"]
    assert "did not hold" in question.data["text"]
    assert answer.data["text"] == "You took the result as it is."
    assert answer.data["by"] == "user"


def test_starting_over_is_recorded_as_the_licence_it_is():
    from core.loop import START_OVER

    _, events, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass")] * 5,
        budgets=Budgets(revisions=1),
        checks=[SELF_INTERSECTION_HELD, assertion_failed(SIX_FAILED)] * 5,
        decide=lambda failure: START_OVER,
    )

    assert "delete what was built" in events.of(ANSWER)[-1].data["text"]


def test_nothing_is_asked_when_the_run_applied_nothing_to_lose():
    asked = []

    a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), code_reply("make()"),
        code_reply("make()"),
        applies=[failed("one"), failed("two"), failed("three")],
        checks=[assertion_failed(SIX_FAILED)],
        decide=lambda failure: asked.append(failure) or "accept",
    )

    assert asked == []




def test_a_run_that_inherits_a_failure_says_so_before_it_reads_the_drawing():
    from core.events import OUTSTANDING

    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        outstanding=STILL_WRONG)

    assert events.first(OUTSTANDING).data["entries"] == STILL_WRONG
    assert [event.kind for event in events][:2] == [INTENT, OUTSTANDING], \
        "what is already wrong turned up after the run had started planning"


def test_the_first_plan_of_an_inheriting_run_is_told_what_is_wrong():
    _, _, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        outstanding=STILL_WRONG)

    to_plan = json.dumps(llm.calls[1])

    assert "acceptance checks did not pass" in to_plan
    assert "13.0 mm" in to_plan
    assert "Do not plan around it" in to_plan


def test_an_inherited_failure_is_checked_again_at_the_end_of_the_new_run():
    """
    The second drawing's acceptance list describes the part it wants and knows
    nothing of what the run before it got wrong. Adding the entries is what
    makes the checks an answer about the Document rather than about the changes.
    """
    inherited = ["features \u2192 bore-left \u2192 position: 25 mm (read)"]

    _, _, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        outstanding=inherited)

    checked = json.loads(llm.calls[4][-1]["content"])

    assert checked["acceptance"] == ACCEPTANCE + inherited


def test_an_inherited_entry_the_new_run_already_lists_is_not_listed_twice():
    _, _, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        outstanding=[ACCEPTANCE[0]])

    assert json.loads(llm.calls[4][-1]["content"])["acceptance"] == ACCEPTANCE


def test_an_inherited_failure_that_now_passes_is_no_longer_outstanding():
    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"),
        outstanding=STILL_WRONG)

    assert result.ok
    assert result.unmet == [], "a passing check did not clear what it inherited"


def test_a_run_that_inherits_nothing_says_nothing_about_it():
    """ A look costs a whole provider turn: Execute replies with a question instead of code and has to be asked again. A recorded run asked what the same scratch object had eight times and what PartDesign::Pocket has five, across the regenerations of one Plan, and every one of those answers had already been given. So what the run has been told is handed back to Execute before it can ask, and an answer it asks for anyway comes out of what it was told."""
    from core.events import OUTSTANDING

    _, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass"))

    assert events.first(OUTSTANDING) is None
    assert "acceptance checks did not pass" not in json.dumps(llm.calls[1])




def test_a_provider_that_keeps_cutting_the_stream_is_given_up_on():
    """Three, because a gateway cutting every one of them is down rather than unlucky."""
    result, _, llm, _ = drive([_cut_off(), _cut_off(), _cut_off(), plan_reply("Only")])

    assert not result.ok
    assert len(llm.calls) == 4, "design, then Plan three times"
