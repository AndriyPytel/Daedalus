"""
Level 1 — the Plan -> Execute -> Review loop: review verdicts, budgets, refusals/retries, the event stream and cost.

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
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, BUDGET_EXHAUSTED, CALL_STARTED, COMPLETED,
    COST, ERROR, GATE, INTENT, PART, PLAN, PLAN_REVISED, DESIGN, PREFLIGHT_FAILED, QUESTION,
    REVIEW_SUSPECTED, REVIEW_VERDICT, STEP_STARTED, STOPPED, THOUGHT, TOOL_CALL,
    TOOL_RESULT, UNBUILT, WRITING, EventStream, replay,
)
from core.design import design
from core.design import part_file
from core.sheets import sheet_reader
from core.freecad_apply import TRAPS
from core.llm import REASONING, TEXT, Chunk, ProviderRefused, ScriptedLLMClient, StreamCutOff
from core.design.design import (
    UNDER, carried_by_no_source, decided_for_you, distilled, each_disagreement_once,
    entries, entry_at, measured_from_what_is_gone, merge, pointing,
    read_off_the_wrong_view, render, said_at,
)
from core.loop.budgets import Budgets, revisions_ceiling
from core.loop.prompt import _PHASE_VOCABULARY, _language_sections, _streamed, prompt
from core.loop.run import run
import core.loop.prompt as loop_prompt_module
from core.loop.prompt import PROMPTS
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness
from tests.loop_helpers import (
    RENDER_PNG,
    read,
    settled,
    A_CUBE,
    A_PART,
    plan_reply,
    everything,
    design_reply,
    code_reply,
    assertion_reply,
    obsolete_reply,
    question_reply,
    look_reply,
    verdict_reply,
    suspect_reply,
    FakeTools,
    failed,
    scripted,
    drive,
    _system_messages,
    _turns_of,
    a_refusal,
    _cut_off,
    on_a_subscription,
    a_clean_run,
    CLEAN,
    GEAR,
    MISSING,
    GEAR_AS_TOLD,
    SETTLED,
    A_MATERIAL_CARD,
    THROUGH_OR_NOT,
    OFF_THE_SECTION,
)


def test_review_is_no_longer_told_the_bounding_box():
    """
    `overall_size` is a union of `shape.BoundBox` — the axis-aligned envelope,
    with the flaw that makes it not a measurement: a nut turned 17° reports
    14.626 × 14.355 × 6.500 and not one of those numbers is on the drawing.
    Its place is taken by the acceptance list. FCC-75.
    """
    _, _, llm, tools = drive([
        plan_reply("Pad the base"), code_reply("pad()"), suspect_reply(), verdict_reply("pass"),
    ])

    review_request = llm.calls[-1][1]["content"][0]["text"].split("\n\n")[0]

    assert "overall_size_mm" not in json.loads(review_request)
    assert not any(name == "overall_size" for name, _ in tools.calls)


def test_plan_is_told_to_cite_and_told_it_does_not_write_the_list():
    """
    Plan gets smaller: it owns the path and nothing else. What replaces the copy
    about writing a list is the copy about citing one — the identity of a
    feature is settled before Plan is called, so the same cavity read off three
    views is one Step and Plan is told outright it may not decide that again.
    FCC-75, FCC-94.

    Part.LineSegment alone still appears in the traps, where it is a fact about
    this build; what goes is Plan being asked to write it as a parameter.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    plan_system = " ".join(_system_messages(llm)[1].split())

    assert "what_the_plan_must_satisfy" in plan_system
    assert "Every entry marked `(read)` must be cited by at least one Step" in plan_system
    assert "Coverage, not exclusivity" in plan_system
    assert "it is not yours to decide again" in plan_system
    assert "You do not write the acceptance list" in plan_system

    for cancelled in ("Every Step carries its **parameters**",
                      '"parameters": {"type": "PartDesign::Pad"',
                      '{"type": "Part.LineSegment"', '"properties": {"Length": 10}',
                      "twenty is the ceiling"):
        assert cancelled not in plan_system, \
            "plan.md still carries copy that has been removed: " + cancelled


def test_the_named_parameters_mechanism_is_gone_from_the_code():
    """
    Not merely unused — absent. A comparator left importable is a comparator
    the next ticket wires back in. FCC-75.
    """
    with pytest.raises(ImportError):
        from core.kernel.kernel_worker import wrong_parameters    # noqa: F401

    from core.freecad_apply import build_registry
    described = {tool["name"]: tool for tool in build_registry(pipeline=object()).describe()}
    assert "parameters" not in described["apply_step"]["parameters"]["properties"]


def test_review_is_given_four_views_and_execute_cannot_ask_for_one():
    """
    One view is not enough: the mirrored gear of FCC-54 reads only from above,
    and in isometry the tooth flank is foreshortened, which is exactly why
    Review passed it. Execute may not ask for a view either — a picture on
    request would be a loop inside a phase. FCC-63.
    """
    views = [RENDER_PNG] * 4
    _, _, llm, tools = drive(
        [plan_reply("Add a box"),
         code_reply("doc.addObject('Part::Box', 'Box')"),
         suspect_reply(), verdict_reply("pass")],
        views=views,
    )

    assert [name for name, _ in tools.calls].count("review_views") == 1

    assert llm.calls[4][0]["content"].startswith(prompt("review"))

    review_content = llm.calls[4][1]["content"]
    assert sum(1 for part in review_content if part["type"] == "image_url") == 4
    assert "the same result seen from" in review_content[0]["text"]
    assert "not several" in review_content[0]["text"]

    assert "review_views" not in llm.calls[2][1]["content"]


def test_the_same_four_views_reach_both_review_calls():
    """
    `_review_views` is driven once per run and handed to both `review_suspect`
    and the gating `review` — a picture is not something either phase gets to
    ask for a second time (FCC-257, FCC-258).
    """
    views = [RENDER_PNG] * 4
    _, _, llm, tools = drive(
        [plan_reply("Add a box"),
         code_reply("doc.addObject('Part::Box', 'Box')"),
         suspect_reply(), verdict_reply("pass")],
        views=views,
    )

    assert [name for name, _ in tools.calls].count("review_views") == 1

    suspect_content = llm.calls[3][1]["content"]
    review_content = llm.calls[4][1]["content"]
    assert sum(1 for part in suspect_content if part["type"] == "image_url") == 4
    assert sum(1 for part in review_content if part["type"] == "image_url") == 4
    assert ([part["image_url"] for part in suspect_content if part["type"] == "image_url"]
            == [part["image_url"] for part in review_content if part["type"] == "image_url"])


def test_view_names_names_every_review_view_including_the_section():
    """
    FCC-261: `_VIEW_NAMES` labels Review's own prose off `len(pictures)`, so a
    `REVIEW_VIEWS` that grew a fifth entry and left this short of it would
    mislabel the section capture rather than fail loudly.
    """
    from core.freecad_apply import REVIEW_VIEWS
    from core.loop.execute import _VIEW_NAMES

    assert len(_VIEW_NAMES) == len(REVIEW_VIEWS)


def test_a_run_with_no_views_still_reaches_a_verdict():
    """
    No views still degrades Review to the Description alone, and a registry with
    no `review_views` at all — an older one, a test one — is the same case as
    a headless one. FCC-63.
    """
    result, _, llm, _ = drive(
        [plan_reply("Add a box"),
         code_reply("doc.addObject('Part::Box', 'Box')"),
         suspect_reply(), verdict_reply("pass")],
        applies=[{"status": "applied", "created": ["Box"], "description": {}}],
        views=[],
    )

    assert result.ok
    assert "No render was produced" in llm.calls[3][1]["content"][0]["text"]


def test_a_step_that_cannot_be_built_returns_to_plan():
    """
    Execute has one escape today and it is narrow: `obsolete` means the Step no
    longer makes sense. "I cannot build this" was not handled at all — the run
    died with whatever had already been applied left in the Document. FCC-61.
    """
    result, events, _, tools = drive(
        [
            plan_reply("Impossible"),
            code_reply("nope1()"), code_reply("nope2()"), code_reply("nope3()"),
            plan_reply("Possible"),
            code_reply("yes()"),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
    )

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None
    assert revised.data["steps"] == ["Possible"]
    assert "could not be built" in revised.data["reason"]
    assert "three" in revised.data["reason"]


def test_the_replan_after_a_handback_reads_the_document_as_it_is():
    """
    The Steps already applied are not repeated: the revision is planned from a
    fresh Snapshot, the same path `obsolete` already takes. FCC-61.
    """
    _, _, _, tools = drive(
        [
            plan_reply("First", "Impossible"),
            code_reply("first()", "nope1()"),
            code_reply("nope2()", first=2), code_reply("nope3()", first=2),
            plan_reply("Possible"),
            code_reply("yes()"),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[None, failed("one"), failed("two"), failed("three")],
    )

    last_failure = max(index for index, (name, args) in enumerate(tools.calls)
                       if name == "apply_step" and args["code"] == "nope3()")
    after = [name for name, _ in tools.calls[last_failure:]]
    assert "snapshot" in after, "the revision was planned without re-reading the Document"
    assert tools.applied_codes() == [
        "first()", "nope1()", "nope2()", "nope3()", "yes()"]


def test_a_step_handed_back_twice_with_nothing_applied_stops_the_run():
    """
    Rewriting a Step that has already come back once, with nothing having landed
    in between, is the loop going nowhere: what Execute refuses is not how the
    Step is worded. The Model Mania run handed Step 1 of 8 back over a left arm
    that could not both be 73 mm long and reach the tangent plane the drawing
    gives, was replanned, handed the same Step back again, and finished the
    whole budget with an empty Document. FCC-127.
    """
    result, events, _, tools = drive(
        [
            plan_reply("Pad a profile that contradicts itself"),
            json.dumps({"declare": {"unbuildable": "the arm cannot reach the tangent plane"}}),
            plan_reply("Pad it with the arm assumed 80.5 mm long"),
            json.dumps({"declare": {"unbuildable": "80.5 mm does not reach it either"}}),
        ],
        budgets=Budgets(revisions=5),
    )

    assert result.blocked
    assert "80.5 mm does not reach it either" in result.reason
    assert "the description it was planned from" in result.reason
    assert tools.applied_codes() == []
    assert len(events.of(PLAN_REVISED)) == 1, "the loop kept rewriting a Step going nowhere"


def test_a_step_handed_back_after_another_one_landed_is_still_replanned():
    """
    Progress is progress: a handback that follows an applied Step is a Plan
    being carried the rest of the way, not a loop. FCC-127.
    """
    result, _, _, tools = drive(
        [
            plan_reply("First", "Impossible"),
            json.dumps({"declare": {"steps": [{"step": 1, "code": "first()", "label": "First"}],
                        "unbuildable": "the second cuts nothing"}}),
            plan_reply("Second", "Impossible again"),
            json.dumps({"declare": {"steps": [{"step": 1, "code": "second()", "label": "Second"}],
                        "unbuildable": "still cuts nothing"}}),
            plan_reply("Third"),
            code_reply("third()"),
            suspect_reply(), verdict_reply("pass"),
        ],
        budgets=Budgets(revisions=5),
    )

    assert result.ok
    assert tools.applied_codes() == ["first()", "second()", "third()"]


def test_a_run_out_of_revisions_stops_and_names_handbacks_not_amendments():
    """
    FCC-22's rule survives the new cause: whichever branch spends the last
    unit is not blamed for the others. A run stopped by handbacks does not
    report amendments. FCC-61.
    """
    result, _, _, _ = drive(
        [
            plan_reply("Impossible"),
            code_reply("nope1()"),
            plan_reply("Impossible again"),
            code_reply("nope2()"),
        ],
        applies=[failed("one"), failed("two")],
        budgets=Budgets(attempts_per_step=1, revisions=1),
    )

    assert result.blocked
    assert "could not build a Step 1 time" in result.reason
    assert "amended" not in result.reason
    assert "Review rejected" not in result.reason


def test_the_revisions_ceiling_is_computed_from_the_plans_length():
    """
    A four-Step Plan and a forty-Step Plan cannot have the same allowance —
    a long Plan has more places to hand back from, and every handback spends
    one. The configured value is the floor. FCC-61.
    """
    assert revisions_ceiling(Budgets(), ["s"] * 4) == 3
    assert revisions_ceiling(Budgets(), ["s"] * 6) == 3
    assert revisions_ceiling(Budgets(), ["s"] * 14) == 3
    assert revisions_ceiling(Budgets(), ["s"] * 15) == 3
    assert revisions_ceiling(Budgets(), ["s"] * 40) == 8
    assert revisions_ceiling(Budgets(revisions=10), ["s"] * 4) == 10


def test_the_attempt_budget_is_per_step_not_per_run():
    result, _, _, tools = drive(
        [
            plan_reply("First", "Second"),
            code_reply("a1()", "b1()"),
            code_reply("a2()", "b1()"),
            code_reply("b2()", first=2),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("a"), None, failed("b"), None],
    )

    assert result.ok
    assert tools.applied_codes() == ["a1()", "a2()", "b1()", "b2()"]


def test_two_review_cycles_per_plan_then_blocked():
    result, events, _, _ = drive([
        plan_reply("v1"), code_reply("v1()"), suspect_reply(), verdict_reply("fail", "not right"),
        plan_reply("v2"), code_reply("v2()"), suspect_reply(), verdict_reply("fail", "still not right"),
        plan_reply("v3"), code_reply("v3()"), suspect_reply(), verdict_reply("fail", "no better"),
    ])

    assert not result.ok
    assert len(events.of(REVIEW_VERDICT)) == 3
    blocked = events.first(BLOCKED)
    assert "review" in blocked.data["reason"].lower()
    assert "no better" in json.dumps(blocked.data["tried"])


def test_repeated_obsolete_declarations_cannot_loop_forever():
    result, events, _, _ = drive([
        plan_reply("a"), obsolete_reply("stale 1"),
        plan_reply("b"), obsolete_reply("stale 2"),
        plan_reply("c"), obsolete_reply("stale 3"),
        plan_reply("d"), obsolete_reply("stale 4"),
    ])

    assert not result.ok
    assert events.first(BUDGET_EXHAUSTED) is not None


def test_budgets_are_configurable():
    result, _, _, tools = drive(
        [plan_reply("Only"), code_reply("once()")],
        applies=[failed("no")],
        budgets=Budgets(attempts_per_step=1),
    )

    assert not result.ok
    assert tools.applied_codes() == ["once()"]


def test_blocked_waits_for_the_user_rather_than_failing_silently():
    result, events, _, _ = drive(
        [plan_reply("Only"), code_reply("x()")],
        applies=[failed("no")],
        budgets=Budgets(attempts_per_step=1, revisions=0),
    )

    assert result.blocked
    assert not result.ok
    assert result.awaiting_user
    assert events[-1].kind == BUDGET_EXHAUSTED




def test_a_refused_execute_ends_blocked_with_the_provider_s_reason():
    result, events, _, _ = drive([
        plan_reply("First", "Second"), code_reply("first()"), a_refusal(),
    ])

    assert result.blocked and result.awaiting_user
    assert "quota" in result.reason and "26s" in result.reason
    assert events[-1].kind == BLOCKED
    assert events.of(ERROR) == [], "a refusal was reported as a crash"


def test_the_steps_already_applied_are_still_counted_when_the_provider_refuses():
    result, _, _, tools = drive([
        plan_reply("First", "Second"), code_reply("first()"), a_refusal(),
    ])

    assert result.steps_applied == 1
    assert tools.applied_codes() == ["first()"]


def test_the_provider_s_verbatim_words_reach_the_session_log():
    _, events, _, _ = drive([
        plan_reply("Only"), a_refusal(),
    ])

    assert "RESOURCE_EXHAUSTED" in events.first(BLOCKED).data["provider_said"]
    assert "RESOURCE_EXHAUSTED" in events.as_log()


def test_half_a_step_is_never_applied_when_the_stream_dies_partway():
    result, _, _, tools = drive([
        plan_reply("Only"),
        [Chunk(TEXT, '{"code": "doc.addObject('), a_refusal()],
    ])

    assert result.blocked
    assert tools.applied_codes() == [], "a half-streamed Step reached the Document"


def test_what_was_tried_before_the_refusal_is_still_reported():
    result, _, _, _ = drive(
        [plan_reply("Only"), code_reply("bad()"), a_refusal()],
        applies=[failed("boom")],
    )

    assert [t["traceback"] for t in result.tried] == ["boom"]


def test_a_refusal_during_plan_ends_the_same_way_as_one_during_execute():
    result, events, _, _ = drive([a_refusal()])

    assert result.blocked
    assert "quota" in result.reason
    assert events[-1].kind == BLOCKED


def test_a_genuinely_malformed_reply_still_says_it_was_malformed():
    """Correction turns are bounded by the turn budget, not a hand-written limit."""
    malformed = ["not JSON"] * 50
    result, events, _, _ = drive([plan_reply("Only")] + malformed,
                                 budgets=Budgets(revisions=2))

    assert not result.blocked
    assert not result.ok
    assert "did not reply with JSON" in result.reason
    assert events.first(ERROR) is not None


def test_a_reply_that_is_all_reasoning_and_no_answer_is_not_called_malformed():
    """
    Kimi-k3 spent a whole design turn thinking and stopped without answering.
    "did not reply with JSON. It said: " with nothing after it told the user
    nothing about what went wrong (FCC-108).
    """
    reasoning_only = [{"reasoning": "thinking..."} for _ in range(50)]
    result, events, _, _ = drive(reasoning_only, attachments=[RENDER_PNG])

    assert "could not parse a JSON reply" not in result.reason
    assert "reasoning" in result.reason
    assert "output tokens" not in result.reason, "a cap it never reached is the wrong advice"
    assert events.first(ERROR) is not None




def test_a_stream_the_provider_cut_off_is_sent_again_rather_than_failing():
    """
    The opencode gateway closes a long stream at random — four of seven,
    measured against it with raw HTTP and no client in the call
    (anomalyco/opencode#44148). The same call sent again usually lands, which
    is what opencode's own client does with it, so a run that gives up on the
    first one throws away a phase for nothing.
    """
    result, _, llm, _ = drive([_cut_off(), plan_reply("Only"),
                               code_reply("box()"), suspect_reply(), verdict_reply("pass", "Good.")])

    assert result.ok
    assert len(llm.calls) == 6, "design, Plan twice, Execute, review_suspect, Review"





def test_a_cut_off_stream_backs_off_before_the_retry(monkeypatch):
    """
    A gateway cutting a stream is a random drop, not the model: three
    instant retries in the same overloaded moment have the same odds of
    landing in it again. A short, growing pause between attempts gives the
    gateway a chance to clear before the next one goes out.
    """
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(loop_prompt_module, "_one_call", Mock(side_effect=[
        StreamCutOff("cut once"), StreamCutOff("cut twice"), ("body text", "", "")]))

    body, thought, reasoning = _streamed(None, EventStream(), "Plan", "system", "user")

    assert body == "body text"
    assert sleeps == [1, 2], "a growing pause between the two failed attempts"


def test_the_event_stream_of_a_clean_run():
    """
    Execute's reasoning stands before the first Step starts rather than
    inside it: it is reasoning about the whole Plan, and it is spent before
    any Step runs (FCC-95). What a Step still has of its own is
    `step_started`, the apply and `applied`. The self-intersection check
    (FCC-256) runs after `applied` and asks nobody, so it carries its own
    `tool_call`/`tool_result` pair with no `call_started` around it, even
    with nothing in the acceptance list to check.
    """
    _, events, _, _ = drive([
        plan_reply("Only", thinking="A box is a Part::Box."),
        code_reply("box()", thinking="addObject then set Length."),
        suspect_reply(), verdict_reply("pass", "Good.", thinking="The render shows a cube."),
    ])

    assert [e.kind for e in events] == [
        INTENT,
        CALL_STARTED, WRITING, COST,
        CALL_STARTED, THOUGHT, WRITING, COST, PLAN, GATE, GATE,
        CALL_STARTED, THOUGHT, WRITING, COST, STEP_STARTED, TOOL_CALL, TOOL_RESULT, APPLIED,
        CALL_STARTED, WRITING, COST, REVIEW_SUSPECTED,
        TOOL_CALL, TOOL_RESULT,
        CALL_STARTED, THOUGHT, WRITING, COST, REVIEW_VERDICT,
        COMPLETED,
    ]


def test_the_event_stream_of_a_clean_run_with_acceptance():
    """
    The same run with something to check: the Assertion phase stands between
    the last Step and the verdict, and its code rides on the Tool call the way
    a Step's does. FCC-76.
    """
    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass", "Good."),
    ], attachments=[RENDER_PNG])

    assert [e.kind for e in events] == [
        INTENT, ATTACHED,
        CALL_STARTED, WRITING, COST, DESIGN, PART,
        CALL_STARTED, WRITING, COST, PLAN, GATE, GATE,
        CALL_STARTED, WRITING, COST, STEP_STARTED, TOOL_CALL, TOOL_RESULT, APPLIED,
        CALL_STARTED, WRITING, COST, REVIEW_SUSPECTED,
        TOOL_CALL, TOOL_RESULT,
        CALL_STARTED, WRITING, COST, TOOL_CALL, TOOL_RESULT, ASSERTION,
        CALL_STARTED, WRITING, COST, REVIEW_VERDICT,
        COMPLETED,
    ]


def test_the_event_stream_of_a_retried_run():
    """The self-intersection check (FCC-256) still runs after `applied`, unconditionally."""
    _, events, _, _ = drive(
        [plan_reply("Only"), code_reply("bad()"), code_reply("good()"), suspect_reply(), verdict_reply("pass")],
        applies=[failed("boom"), None],
    )

    assert [e.kind for e in events] == [
        INTENT,
        CALL_STARTED, WRITING, COST,
        CALL_STARTED, WRITING, COST, PLAN, GATE, GATE,
        CALL_STARTED, WRITING, COST, STEP_STARTED,
        TOOL_CALL, TOOL_RESULT, PREFLIGHT_FAILED,
        CALL_STARTED, WRITING, COST, TOOL_CALL, TOOL_RESULT, APPLIED,
        CALL_STARTED, WRITING, COST, REVIEW_SUSPECTED,
        TOOL_CALL, TOOL_RESULT,
        CALL_STARTED, WRITING, COST, REVIEW_VERDICT,
        COMPLETED,
    ]


def test_the_event_stream_of_a_blocked_run():
    _, events, _, _ = drive(
        [plan_reply("Only"), code_reply("x()")],
        applies=[failed("boom")],
        budgets=Budgets(attempts_per_step=1, revisions=0),
    )

    assert [e.kind for e in events] == [
        INTENT,
        CALL_STARTED, WRITING, COST,
        CALL_STARTED, WRITING, COST, PLAN, GATE, GATE,
        CALL_STARTED, WRITING, COST, STEP_STARTED, TOOL_CALL, TOOL_RESULT,
        PREFLIGHT_FAILED, BUDGET_EXHAUSTED,
    ]


def test_tool_calls_and_their_results_are_both_visible():
    _, events, _, _ = drive([
        plan_reply("Only"), code_reply("box()", label="Add the box"), suspect_reply(), verdict_reply("pass"),
    ])

    call = events.first(TOOL_CALL)
    result = events.first(TOOL_RESULT)
    assert call.data == {"name": "apply_step", "arguments": {"code": "box()", "label": "Add the box"}}
    assert result.data["name"] == "apply_step"
    assert result.data["result"]["status"] == "applied"


def test_no_step_puts_a_picture_in_the_stream():
    """
    The per-Step viewport screenshot is gone, panel and all (FCC-81). Nobody
    read it: the only images that reach a model go through `_as_image`, and
    the step render was never among them.
    """
    _, events, _, _ = drive([
        plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass"),
    ])

    assert [e for e in events if e.kind == "render"] == []
    assert "render" not in events.first(TOOL_RESULT).data["result"]


def test_reasoning_is_streamed_tagged_with_the_phase_it_came_from():
    _, events, _, _ = drive([
        plan_reply("Only", thinking="planning out loud"),
        code_reply("box()", thinking="coding out loud"),
        suspect_reply(), verdict_reply("pass", thinking="judging out loud"),
    ])

    assert [(e.data["phase"], e.data["text"]) for e in events.of(THOUGHT)] == [
        ("plan", "planning out loud"),
        ("execute", "coding out loud"),
        ("review", "judging out loud"),
    ]


def test_the_words_a_phase_writes_reach_the_stream_as_it_writes_them():
    """
    A reply forms over as long as the reasoning before it did — a recorded run
    measured 22.4 minutes of its 23.6 minutes of provider time after the last
    reasoning chunk, and 25.6 of 53.7 in another — and until the words are on
    the stream the panel has nothing to show for any of it.

    The phase is on the event because a stream carries several of them and
    what is being written is only legible beside the name of what is writing.
    """
    _, events, _, _ = drive([
        plan_reply("Only"),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    written = "".join(e.data["text"] for e in events.of(WRITING)
                      if e.data["phase"] == "plan")

    assert json.loads(written)["declare"]["steps"] == ["Only"]
    assert {e.data["phase"] for e in events.of(WRITING)} == {
        "design", "plan", "execute", "review_suspect", "review"}


def test_a_phase_is_answered_by_the_client_its_own_profile_names():
    """
    One dial over five phases is one compromise: Design reading a drawing and
    Execute writing the code for a Step it has already been given are not the
    same demand, and a run that raises the dial for the first pays it on every
    call of the second.

    The seam is `for_phase`, asked for at the one place every phase's call
    goes through, and a client that has none answers everything itself — which
    is what `ScriptedLLMClient` and every client written before profiles do.
    """
    class TwoProfiles:
        def __init__(self, thinking, doing):
            self.model = "profiles"
            self.asked = []
            self._thinking, self._doing = thinking, doing

        def for_phase(self, phase):
            self.asked.append(phase)
            return self._thinking if phase in ("design", "plan") else self._doing

    thinking = ScriptedLLMClient([design_reply(), plan_reply("Only")])
    doing = ScriptedLLMClient([code_reply("box()"), suspect_reply(), verdict_reply("pass")])
    llm = TwoProfiles(thinking, doing)
    events = EventStream()

    result = run("Make a box", llm=llm, tools=FakeTools().registry, events=events)

    assert result.ok
    assert llm.asked == ["design", "plan", "execute", "review_suspect", "review"]
    assert not doing.turns and not thinking.turns










def test_a_phase_that_answers_without_thinking_leaves_the_accordion_empty():
    """
    Nothing is said in place of reasoning that did not arrive. The accordion
    `ui/copilot_panel.py :: _render_thought` opens on a phase's first `thought`
    and a model that did not think opens none — which is what every provider
    without a reasoning stream already does, and what this route does at low
    effort. A sentence explaining the ordinary case is one no other provider
    needs.
    """
    _, events = a_clean_run(on_a_subscription(CLEAN))

    assert events.of(THOUGHT) == []


def test_a_run_on_a_provider_that_only_ever_sends_text_still_finishes():
    """
    A reply that is text and nothing else must not read as a phase that
    streamed its reasoning and then stopped without answering.
    """
    result, events = a_clean_run(on_a_subscription(CLEAN))

    assert result.ok
    assert events.last(REVIEW_VERDICT).data["verdict"] == "pass"


def test_every_call_writes_what_it_would_have_cost():
    """
    Nothing in the repo records what a run costs: `usage`, `tokens` and `cost`
    appear in none of `logs/*.log`. The number is what the call would have cost
    on the route that charges for it.
    """
    llm = on_a_subscription(CLEAN)

    _, events = a_clean_run(llm)

    spent = events.of(COST)
    assert len(spent) == len(llm.calls)
    assert spent[0].data["phase"] == "design"
    assert spent[0].data["usd"] == 0.0049
    assert spent[0].data["model"] == "sonnet"
    assert "seconds" in spent[0].data
    assert isinstance(spent[0].data["seconds"], float)


def test_what_a_call_cost_lands_in_the_session_log():
    """`logs/sessions/*.jsonl` is permanent, where `logs/addon_*.log` rotates at ten files."""
    llm = on_a_subscription(CLEAN)

    _, events = a_clean_run(llm)

    written = [line for line in events.as_log().splitlines() if '"kind": "cost"' in line]
    assert len(written) == len(llm.calls)


def test_a_provider_that_says_nothing_about_what_it_spent_writes_cost_with_only_seconds():
    """
    COST is emitted for every call with seconds always present.

    When the provider reports no price (ScriptedLLMClient has no last_cost),
    the usd field is absent.
    """
    _, events, _, _ = drive(CLEAN)

    cost_events = events.of(COST)
    assert len(cost_events) == 5
    for cost in cost_events:
        assert "seconds" in cost.data
        assert isinstance(cost.data["seconds"], float)
        assert "usd" not in cost.data
        assert "phase" in cost.data
        assert "model" in cost.data


def test_a_keyed_provider_that_prices_writes_a_cost_event_through_the_unchanged_seam():
    """
    The proof the header stops reading $0.00 for a keyed call: `_streamed` is
    the one seam every phase's stream is consumed through and this change does
    not touch it — only `LiteLLMClient` gains a `last_cost`. Reuses the
    recording `tests/test_llm.py::test_a_priced_streaming_call_sets_last_cost`
    prices, the one in the suite whose usage survives through to a
    `ModelResponse` LiteLLM can price.
    """
    from litellm.llms.anthropic.chat.handler import ModelResponseIterator

    from core.llm import LiteLLMClient
    from core.loop.prompt import _streamed

    recorded_path = os.path.join(os.path.dirname(__file__), "recorded",
                                 "anthropic_thinking_stream.json")
    with open(recorded_path, encoding="utf-8") as f:
        recorded = json.load(f)
    it = ModelResponseIterator(streaming_response=None, sync_stream=True)
    chunks = [it.chunk_parser(event) for event in recorded]

    llm = LiteLLMClient(model="claude-3-7-sonnet-20250219", api_key="k",
                        completion=lambda **kwargs: iter(chunks))
    events = EventStream()

    _streamed(llm, events, "design", "system", "user")

    cost_events = [e.data for e in events.of(COST)]
    assert len(cost_events) == 1
    assert cost_events[0]["phase"] == "design"
    assert cost_events[0]["usd"] == 0.004521
    assert cost_events[0]["model"] == "claude-3-7-sonnet-20250219"
    assert "seconds" in cost_events[0]
    assert isinstance(cost_events[0]["seconds"], float)


def test_the_stream_can_be_watched_live():
    seen = []
    events = EventStream()
    events.subscribe(lambda event: seen.append(event.kind))

    run("Make a box", llm=ScriptedLLMClient([
        plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass"),
    ]), tools=FakeTools().registry, events=events)

    assert seen == [e.kind for e in events]
    assert seen[0] == INTENT


def test_a_reply_that_is_not_json_is_an_error_the_user_is_told_about():
    prose = ["Still prose"] * 50
    result, events, _, _ = drive(prose, budgets=Budgets(revisions=2))

    assert not result.ok
    error = events.first(ERROR)
    assert error is not None
    assert "json" in error.data["message"].lower()


def test_a_plan_with_no_steps_is_an_error_not_a_silent_success():
    result, events, _, _ = drive([plan_reply()])

    assert not result.ok
    assert events.first(ERROR) is not None


def test_a_provider_failure_is_surfaced_rather_than_swallowed():
    class Exploding:
        def stream(self, messages, **kw):
            raise RuntimeError("429 rate limited")

    events = EventStream()
    result = run("Make a box", llm=Exploding(), tools=FakeTools().registry, events=events)

    assert not result.ok
    assert "429 rate limited" in events.first(ERROR).data["message"]


def test_the_user_can_stop_between_steps():
    """User story 18: never a passenger."""
    stop = {"now": False}
    events = EventStream()
    tools = FakeTools()

    def cancel_after_the_first_step(event):
        if event.kind == APPLIED:
            stop["now"] = True

    events.subscribe(cancel_after_the_first_step)

    result = run("Make a box", llm=scripted([
        plan_reply("First", "Second", "Third"),
        code_reply("first()"),
        code_reply("second()"),
    ]), tools=tools.registry, events=events, cancelled=lambda: stop["now"])

    assert result.blocked
    assert result.awaiting_user
    assert "Stopped by you" in result.reason
    assert tools.applied_codes() == ["first()"], "it kept going after being stopped"


def test_stopping_says_what_is_already_in_the_document():
    stop = {"now": False}
    events = EventStream()
    events.subscribe(lambda e: stop.update(now=True) if e.kind == PLAN else None)

    run("Make a box", llm=scripted([plan_reply("First")]),
        tools=FakeTools().registry, events=events, cancelled=lambda: stop["now"])

    stopped = events.first(STOPPED)
    assert "still in the Document" in stopped.data["reason"]


def test_a_run_that_is_never_stopped_is_unaffected():
    result, _, _, tools = drive([
        plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["box()"]


def test_the_loop_takes_everything_it_needs_as_arguments():
    """A narrower Intent is all a future sub-agent needs."""
    import inspect

    signature = inspect.signature(run)

    assert list(signature.parameters)[0] == "intent"
    for injected in ("llm", "tools", "events", "budgets"):
        assert injected in signature.parameters


def test_two_loops_can_run_over_different_dependencies_without_touching_each_other():
    first = drive([plan_reply("A"), code_reply("a()"), suspect_reply(), verdict_reply("pass")],
                  intent="First intent")
    second = drive([plan_reply("B"), code_reply("b()"), suspect_reply(), verdict_reply("pass")],
                   intent="Second intent")

    assert first[3].applied_codes() == ["a()"]
    assert second[3].applied_codes() == ["b()"]
    assert first[1] is not second[1]


def test_a_run_reports_what_it_did():
    result, _, _, _ = drive([
        plan_reply("First", "Second"),
        code_reply("a()", "b()"),
        suspect_reply(), verdict_reply("pass", "Both done."),
    ])

    assert result.ok
    assert result.steps_applied == 2
    assert result.verdict == "pass"




def test_plan_can_ask_instead_of_planning():
    """
    The in-process half of FCC-25: with a value the part cannot be built
    without missing, Plan answers with a question and no Plan is shown. That it
    may ask is unchanged; who answers it is not — nobody does, and Plan chooses
    and says in the Step what it chose (ADR-0022).
    """
    result, events, _, tools = drive(
        [question_reply(MISSING),
         plan_reply("Sketch the blank, assumed module 2", "Pad it"),
         code_reply("a()", "b()"), suspect_reply(), verdict_reply("pass")],
        intent=GEAR)

    question = events.first(QUESTION)
    assert question is not None and question.data["text"] == MISSING
    kinds = [e.kind for e in events]
    assert kinds.index(QUESTION) < kinds.index(PLAN)
    assert result.ok
    assert tools.applied_codes() == ["a()", "b()"]




def test_the_users_answer_is_written_into_the_description_and_not_handed_to_plan():
    """
    A person's answer — Design's own surviving question, the only one left —
    goes back through Design and lands in the same model by the same merge, so
    what Plan is handed next is the description grown, never an answer standing
    beside it, which is the arrangement that lost the 100 mm. The user supplied
    it, so it is `assumed`. FCC-25, FCC-93, ADR-0022.
    """
    llm = ScriptedLLMClient([
        design_reply({}, question=MISSING),
        design_reply(GEAR_AS_TOLD),
        plan_reply("Sketch a 20-tooth module-2 blank"), code_reply("a()"),
        suspect_reply(), verdict_reply("pass"),
    ])
    tools = FakeTools()

    run(GEAR, llm=llm, tools=tools.registry, attachments=[RENDER_PNG],
        ask=lambda _: "module 2, 20 teeth, 10 thick, ⌀8 bore")

    told = json.loads(llm.calls[1][-1]["content"][-1]["text"])
    assert told["the_question"] == MISSING
    assert told["the_answer_you_were_given"] == "module 2, 20 teeth, 10 thick, ⌀8 bore"

    described = json.loads(llm.calls[2][-1]["content"])
    assert "20 teeth (assumed)" in described["what_the_part_is"]
    assert "you_asked_and_were_answered" not in described


def test_a_run_nobody_is_watching_gets_a_plan_that_chose_for_them():
    result, events, llm, _ = drive(
        [question_reply(MISSING),
         plan_reply("Sketch the blank, assumed module 2 and 20 teeth"), code_reply("a()"),
         suspect_reply(), verdict_reply("pass")],
        intent=GEAR)

    assert result.ok
    told = json.dumps(llm.calls[2])
    assert "Choose sensible values yourself" in told
    assert events.first(ANSWER).data["by"] == "nobody"


def test_with_nobody_to_ask_the_run_does_not_stall():
    """Level 1, Level 2 and headless runs have no user; they must still plan."""
    result, _, llm, _ = drive(
        [question_reply(MISSING), plan_reply("Sketch it, assumed module 2"), code_reply("a()"),
         suspect_reply(), verdict_reply("pass")],
        intent=GEAR)

    assert result.ok
    assert "Choose sensible values yourself" in json.dumps(llm.calls[2])




def test_a_run_nobody_answered_at_all_reaches_the_gate_rather_than_a_block():
    """
    Every question put to the sheet, the sheet saying nothing to any of them,
    and Plan told to choose each time. That is the run FCC-127 was written for:
    the model settles the value and `because` says who settled it, so the Gate
    is where it is read. A count of unanswered questions is not a reason to end
    a run (FCC-133).
    """
    asked = []

    result, events, _, tools = drive([
        design_reply(SETTLED),
        question_reply("q1"), design_reply({}),
        question_reply("q2"), design_reply({}),
        question_reply("q3"),
        plan_reply("Pad the profile", satisfies=everything(SETTLED)),
        code_reply("pad()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(rereads=2),
        ask=lambda q: asked.append(q) or "some answer")

    assert result.ok, result.reason
    assert asked == [], "the user was asked for a value: %s" % asked
    assert events.of(GATE), "the run never reached the Plan Gate"
    assert events.first(PLAN).data["unanswered"] == ["q1", "q2", "q3"]
    assert "half the 50 mm height" in json.dumps(events.first(PLAN).data["decided"]), \
        "the value the model settled reached the Gate without its evidence"
    assert tools.applied_codes() == ["pad()"]




def test_a_question_about_a_material_card_is_answered_from_the_tools():
    """
    The uuid of a material card is a fact about this FreeCAD, not about the
    part: no drawing prints one. A recorded run put it to the sheet three
    times, was told each time that nothing in the views answers it, and ended
    on the third for want of an answer. The Tool that holds it answers it and
    the run carries on (FCC-149).
    """
    result, events, llm, tools = drive([
        design_reply(SETTLED),
        question_reply(A_MATERIAL_CARD),
        look_reply("materials", name="plain carbon steel"),
        plan_reply("Pad the profile", satisfies=everything(SETTLED)),
        code_reply("pad()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok, result.reason
    assert ("materials", {"name": "plain carbon steel"}) in tools.calls, \
        "the question never reached the Tool that answers it: %s" % tools.calls
    design_turns = [call for call in llm.calls if call[0]["content"].startswith(prompt("design"))]
    assert len(design_turns) == 1, "the sheet was asked what no drawing prints"
    assert "b9b3a6ff-1c8e" in json.dumps(_turns_of(llm, "plan")[-1]), \
        "what the Tool said never reached Plan"
    assert events.first(PLAN).data["unanswered"] == [], \
        "an answered question reached the Gate as unanswered"
    assert tools.applied_codes() == ["pad()"]


def test_rereads_are_spent_only_on_questions_put_to_the_drawing():
    """
    One re-read, two questions: the one about the build is a look and costs it
    nothing, so the one about the part still gets the sheet (FCC-149).
    """
    result, events, llm, tools = drive([
        design_reply(SETTLED),
        question_reply(A_MATERIAL_CARD),
        look_reply("materials", name="steel"),
        question_reply("Where is the O8 hole?"),
        design_reply({"datums": [{"name": "the left end face", "is": "the flat face"}]}),
        plan_reply("Pad the profile", satisfies=everything(SETTLED)),
        code_reply("pad()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(rereads=1))

    assert result.ok, result.reason
    design_turns = [call for call in llm.calls if call[0]["content"].startswith(prompt("design"))]
    assert len(design_turns) == 2, \
        "the question about the part was charged for the question about the build"
    assert events.first(PLAN).data["unanswered"] == []
    assert tools.applied_codes() == ["pad()"]


def test_rereads_counts_re_reads_of_the_drawing_and_nothing_else():
    """
    One budget, one job. With one re-read allowed the sheet is asked once, and
    the three questions nobody answered cost nothing further (FCC-133).
    """
    result, events, llm, _ = drive([
        design_reply(SETTLED),
        question_reply("q1"), design_reply({}),
        question_reply("q2"),
        question_reply("q3"),
        plan_reply("Pad the profile", satisfies=everything(SETTLED)),
        code_reply("pad()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(rereads=1))

    assert result.ok, result.reason
    design_turns = [call for call in llm.calls if call[0]["content"].startswith(prompt("design"))]
    assert len(design_turns) == 2, "the sheet was read past its ceiling"
    assert events.first(PLAN).data["unanswered"] == ["q1", "q2", "q3"]




def test_a_question_about_the_drawing_goes_to_the_sheet_before_the_user():
    """
    The run asked the user whether the pockets go through and which edges take
    R2. SECTION A-A shows the depth, hatched, and the R2 edges are drawn in red
    in every view — the user attached the sheet so they would not have to answer
    what it shows. The first pass missed it, which is what a first pass does;
    the route is what has to stop the miss becoming a question. FCC-90.
    """
    asked = []

    result, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply(THROUGH_OR_NOT),
        design_reply(OFF_THE_SECTION),
        plan_reply("Pocket both faces 20 mm",
                   satisfies=everything(A_PART, OFF_THE_SECTION)),
        code_reply("a()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], ask=lambda q: asked.append(q) or "")

    assert result.ok
    assert asked == [], "the user was asked what the drawing answers"
    answered = events.first(ANSWER)
    assert answered.data["by"] == "drawing"
    assert "20 mm deep (read)" in answered.data["text"]

    re_read = llm.calls[2][-1]["content"]
    assert [b for b in re_read if b.get("type") == "image_url"], "it re-read without the sheet"
    asked_again = json.loads(re_read[-1]["text"])
    assert asked_again["the_question"] == THROUGH_OR_NOT
    assert asked_again["the_part_as_you_described_it"] == distilled(A_PART)

    to_plan = json.loads(llm.calls[3][-1]["content"])
    changed = to_plan["what_the_part_is"]
    added = [entry for entry in changed if entry["section"] == "features"]
    assert added == [{"section": "features", "kind": "added", "id": "pocket-front",
                      "now": OFF_THE_SECTION["features"][0]}], \
        "the re-plan was not handed the change-set (FCC-311)"
    assert "you_asked_and_were_answered" not in to_plan


def test_a_value_already_described_is_not_overwritten_by_a_later_pass():
    """
    The run read the left bore axis 20 mm from the left end face and then 25 mm,
    both `read`, and built 25. 20 is still what the Gate shows, and the
    disagreement is visible rather than absent — the merge is enforced by `id`
    in the loop, not only asked for in the prompt. FCC-93.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply("Where is the Ø8 hole?"),
        design_reply({"features": [{"id": "hole-8-left",
                                    "position": {"the left end face": read("25 mm")}}]}),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    grown = events.last(DESIGN).data["model"]
    assert grown["features"][0]["position"]["the left end face"] == read("15 mm")
    assert grown["conflicts"][0]["was_also_said"] == read("25 mm")

    shown = json.loads(llm.calls[3][-1]["content"])["what_the_part_is"]
    conflicts_shown = [entry for entry in shown if entry["section"] == "conflicts"]
    assert conflicts_shown, "the disagreement never reached the Plan Gate (FCC-311)"
    assert conflicts_shown[0]["now"]["stands"] == read("15 mm")
    assert conflicts_shown[0]["now"]["was_also_said"] == read("25 mm")


def test_a_question_the_sheet_does_not_answer_is_settled_by_the_model():
    """
    A tolerance nobody printed is not on the sheet, and nobody is asked for it
    either: Plan chooses and says in the Step what it chose (ADR-0022).

    The sheet saying nothing is on the stream. Without it a stalled run cannot
    be told from one where the drawing was never looked at, which is the first
    thing anybody reading the log asks (FCC-96).
    """
    asked = []

    result, events, _, _ = drive([
        design_reply(A_PART),
        question_reply("What general tolerance applies?"),
        design_reply(),
        plan_reply("Sketch it, assumed ±0.1 mm", satisfies=everything(A_PART)),
        code_reply("a()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG],
        ask=lambda q: asked.append(q) or "±0.1 mm")

    assert result.ok, result.reason
    assert asked == [], "the user was asked for a value: %s" % asked
    assert [answer.data["by"] for answer in events.of(ANSWER)] == ["drawing", "nobody"]
    assert "nothing in the views" in events.first(ANSWER).data["text"]


def test_a_drawing_that_keeps_answering_is_never_a_question_for_anybody():
    """
    Every answer off the sheet is a question that never had to be settled by
    anybody, and it costs a re-read and nothing else. FCC-90, ADR-0022.
    """
    asked = []

    result, events, _, _ = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply({"datums": [{"name": "d1"}]}),
        question_reply("q2"), design_reply({"datums": [{"name": "d2"}]}),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], ask=lambda q: asked.append(q) or "an answer")

    assert result.ok, result.reason
    assert asked == [], "the user was asked what the drawing answered: %s" % asked
    assert [answer.data["by"] for answer in events.of(ANSWER)] == ["drawing", "drawing"]
    assert events.first(PLAN).data["unanswered"] == []


def test_the_re_reads_have_a_ceiling_of_their_own():
    """
    A Plan that keeps asking and a drawing that keeps answering would never
    terminate. Past the ceiling the question is not put to the sheet again, and
    Plan is told to choose (ADR-0022).
    """
    asked = []

    result, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply({"datums": [{"name": "d1"}]}),
        question_reply("q2"),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(rereads=1),
        ask=lambda q: asked.append(q) or "an answer")

    assert result.ok, result.reason
    assert asked == []
    assert [answer.data["by"] for answer in events.of(ANSWER)] == ["drawing", "nobody"], \
        "the second question was put to the sheet past the ceiling"


def test_a_question_raised_after_being_told_to_choose_still_reaches_the_sheet():
    """
    The run that stalled on Model Mania 2025 asked three times. The third
    question was the good one — it had spotted that its own outer profile could
    not be 50 mm tall with 30 mm end rounds — and it never reached the drawing,
    because the loop stopped one line above the re-read. A question raised after
    Plan was told to choose is a *new* obstacle, and the sheet answers it as
    often as any other. FCC-96.
    """
    asked = []
    off_the_sheet = {"datums": [{"name": "the front-to-rear centre plane"}]}

    result, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply(),
        question_reply("q2"), design_reply(off_the_sheet),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], ask=lambda q: asked.append(q) or "")

    assert result.ok, "the second question killed the run: %s" % result.reason
    assert asked == []
    assert [answer.data["by"] for answer in events.of(ANSWER)] \
        == ["drawing", "nobody", "drawing"]
    re_read = llm.calls[4][-1]["content"]
    assert json.loads(re_read[-1]["text"])["the_question"] == "q2"
    assert [b for b in re_read if b.get("type") == "image_url"], "it re-read without the sheet"


def test_the_same_question_twice_is_listed_once_and_does_not_end_the_run():
    """
    Nothing has changed since the last time it was asked, so asking again says
    nothing new — but it is still not a reason to end a run, and the Gate is
    shown the question once rather than once per asking (FCC-133).
    """
    result, events, _, tools = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply(),
        question_reply("q1"), design_reply(),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], ask=lambda _: "")

    assert result.ok, result.reason
    assert events.of(ERROR) == []
    assert events.first(PLAN).data["unanswered"] == ["q1"]
    assert tools.applied_codes() == ["a()"]
