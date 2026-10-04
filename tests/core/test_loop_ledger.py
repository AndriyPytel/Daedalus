"""
Level 1 — the Plan -> Execute -> Review loop: the failure ledger, standing approvals, material assignment and the preflight worker.

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
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, CALL_STARTED, COST, ERROR,
    GATE, INTENT, PART, PLAN, PLAN_REVISED, DESIGN, PREFLIGHT_FAILED, QUESTION,
    REVIEW_SUSPECTED, REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL,
    TOOL_RESULT, UNBUILT, WRITING, EventStream, replay,
)

from core.design import part_file
from core.sheets import sheet_reader
from core.freecad_apply import TRAPS
from core.llm import REASONING, TEXT, Chunk, ProviderRefused, ScriptedLLMClient, StreamCutOff
from core.design.core import (
    UNDER,
    entry_at,
    said_at,
)
from core.design.merge import merge
from core.design.orientation_checks import (
    pointing,
    read_off_the_wrong_view,
)
from core.design.report import (
    distilled,
    each_disagreement_once,
    render,
)
from core.design.gaps import (
    carried_by_no_source,
    decided_for_you,
    entries,
    measured_from_what_is_gone,
)
from core.loop.budgets import Budgets, revisions_ceiling
from core.loop.prompt import _PHASE_VOCABULARY, _language_sections, prompt
from core.loop.run import run
from core.loop.design import design as loop_design, _the_groups
from core.loop.describe import _the_geometry_disagrees
from core.loop import execute as loop_execute_module
from core import kernel
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness
from tests.loop_helpers import (
    RENDER_PNG,
    read,
    settled,
    A_CUBE,
    A_DATUM,
    plan_reply,
    everything,
    design_reply,
    _first_pass_turns,
    code_reply,
    retried_code_reply,
    assertion_reply,
    obsolete_reply,
    look_reply,
    verdict_reply,
    suspect_reply,
    FakeTools,
    failed,
    drive,
    _system_messages,
    _turns_of,
    _execute_attempts,
    scripted,
    StubKernel,
    needs_drawing,
    MODEL_MANIA,
    THE_CLAMP_READ_OFF_ONE_VIEW,
    _pictures_shown,
    _said_to_design,
    A_NUT,
    planning_the_nut,
    CHECKS,
    assertion_failed,
    SELF_INTERSECTION_HELD,
    ASSERT_FAILED,
    NO_MATERIAL,
    IN_PIECES,
    NO_SUCH_PROPERTY,
    FILLET_RADIUS,
    gate_that,
    driven_through,
    _THE_AXIS_PUT_RIGHT,
    _a_sheet_that_contradicts_itself,
)


def test_looks_per_turn_and_its_bounded_loop_are_gone():
    """
    The Look budget goes away because reading the Document is ordinary code
    inside a Step now that the kernel namespace is open — `looks_per_turn`
    and the turn loop that spent it must not come back under this or any
    other name.
    """
    assert not hasattr(Budgets(), "looks_per_turn")
    loop_dir = os.path.dirname(loop_execute_module.__file__)
    source = ""
    for filename in os.listdir(loop_dir):
        if filename.endswith(".py"):
            with open(os.path.join(loop_dir, filename), encoding="utf-8") as f:
                source += f.read()
    assert "looks_per_turn:" not in source, "the field is back on Budgets"
    assert "your looks are spent" not in source.lower(), \
        "the bounded look loop's last-turn message is back"


def test_the_carried_looks_are_bounded():
    """An unbounded list would grow the request for the whole length of a run."""
    from core.loop.execute import Looks

    looks = Looks()
    for number in range(Looks.CARRIED + 4):
        looks.remember("describe_type", {"type_id": "T%d" % number}, {"ok": number})

    carried = looks.still_true()

    assert len(carried) == Looks.CARRIED
    assert carried[-1]["arguments"] == {"type_id": "T%d" % (Looks.CARRIED + 3)}, \
        "the oldest looks went, not the newest"


def test_execute_is_handed_the_material_cards_without_asking():
    """
    A fact about the build, true from the first Step to the last, handed over
    before Execute can spend a turn asking for it.
    """
    _, _, llm, tools = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert "materials" in [name for name, _ in tools.calls], \
        "nobody took the cards before Execute was asked"

    carried = _turns_of(llm, "execute")[0]["what_you_looked_at"]
    cards = [look for look in carried if look["tool"] == "materials"]
    assert cards, "the Execute request carried no material cards"
    assert cards[0]["result"]["materials"][0]["uuid"] == "b9b3a6ff-1c8e"


def test_disk_cache_hit_is_remembered():
    """
    A describe_type answer read off disk must land in still_true() the same
    as one just fetched, or the next Step pays for the same question again.
    """
    from core.loop.execute import Looks

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "type_cache.json")
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump({"1.0:{\"type_id\": \"PartDesign::Hole\"}":
                       {"properties": ["DepthType"]}}, handle)

        looks = Looks(cache_path=cache_path, freecad_version="1.0")
        arguments = {"type_id": "PartDesign::Hole"}

        first = looks.answer("describe_type", arguments)
        assert first == {"properties": ["DepthType"]}

        carried = [look for look in looks.still_true() if look["tool"] == "describe_type"]
        assert carried, "a disk hit never landed in still_true()"
        assert carried[0]["arguments"] == arguments


def test_what_this_build_has_prefetches_the_disk_type_cache():
    """
    `type_cache.json` already answers every describe_type this build asked
    before; those answers belong in the first request, not behind a fresh
    look at the same type.
    """
    from core.loop.execute import _what_this_build_has

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "type_cache.json")
        version = loop_execute_module._freecad_version()
        key = "%s:%s" % (version, json.dumps({"type_id": "PartDesign::Hole"}, sort_keys=True))
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump({key: {"properties": ["DepthType"]}}, handle)

        old_path = loop_execute_module.TYPE_CACHE_PATH
        loop_execute_module.TYPE_CACHE_PATH = cache_path
        try:
            looks = loop_execute_module._what_this_build_has(ToolRegistry())
        finally:
            loop_execute_module.TYPE_CACHE_PATH = old_path

        carried = [look for look in looks.still_true() if look["tool"] == "describe_type"]
        assert carried, "the disk cache never made it into the initial Looks"
        assert carried[0]["result"] == {"properties": ["DepthType"]}


def test_materials_answer_ignores_the_filter_it_was_never_asked_with():
    """
    `materials` is prefetched once, unfiltered; the model asks it filtered by
    name, and the filtered arguments must still resolve to that same answer.
    """
    from core.loop.execute import Looks

    looks = Looks()
    unfiltered = {"materials": [{"uuid": "b9b3a6ff-1c8e", "name": "steel"}]}
    looks.remember("materials", {}, unfiltered)

    assert looks.answer("materials", {"name": "steel"}) == unfiltered
    assert looks.answer("materials", {"name": "carbon"}) == unfiltered


def test_a_plan_that_ends_in_a_material_assignment_uses_a_uuid_from_this_build():
    result, _, llm, tools = drive([
        plan_reply("Pad the profile", "Assign the material"),
        code_reply("pad()", "obj.ShapeMaterial = uuid('b9b3a6ff-1c8e')"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok, result.reason
    carried = _turns_of(llm, "execute")[0]["what_you_looked_at"]
    known = [card["uuid"] for look in carried if look["tool"] == "materials"
             for card in look["result"]["materials"]]
    assigned = tools.applied_codes()[-1]

    assert any(uuid in assigned for uuid in known), \
        "the material Step was applied with a uuid this build does not have"








def test_two_different_failures_in_a_row_are_progress_and_are_left_alone():
    """
    The first thing was fixed and a second surfaced. Telling a run that is
    getting somewhere to change tack would be wrong.
    """
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"), code_reply("third()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), failed(IN_PIECES)])

    third = _execute_attempts(llm)[1]

    assert "you_have_failed_this_the_same_way_twice" not in third


def test_one_failure_says_nothing_about_repeating_yourself():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL)])

    assert "you_have_failed_this_the_same_way_twice" not in _execute_attempts(llm)[0]


def test_the_same_failure_twice_is_told_not_to_write_a_third_variation():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        retried_code_reply("first()", "second()", "third()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), failed(NO_MATERIAL), None])

    retried = [turn for turn in _turns_of(llm, "execute")
              if "you_have_failed_this_the_same_way_twice" in turn]
    said = retried[0]["you_have_failed_this_the_same_way_twice"]

    assert "failed 2 times with the same failure" in said
    assert "Do not write a third variation" in said
    assert "Change the *direction* this time" in said
    assert "Reversed" in said and "ThroughAll" in said
    assert "SideType" in said and "Midplane" not in said, \
        "the advice names the property FreeCAD warns on every write to"
    assert "not the size of the cut" in said


def test_the_directive_matches_the_failure_and_not_some_other_one():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        retried_code_reply("first()", "second()", "third()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(IN_PIECES), failed(IN_PIECES), None])

    retried = [turn for turn in _turns_of(llm, "execute")
              if "you_have_failed_this_the_same_way_twice" in turn]
    said = retried[0]["you_have_failed_this_the_same_way_twice"]

    assert "do not meet" in said
    assert "share their endpoints" in said
    assert "Reversed" not in said, "the wrong failure's advice"





def test_a_failure_nothing_recognises_is_not_given_invented_advice():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"), code_reply("third()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed("ZeroDivisionError: division by zero"),
                failed("ZeroDivisionError: division by zero")])

    assert "you_have_failed_this_the_same_way_twice" not in _execute_attempts(llm)[1]





def test_a_step_that_never_failed_any_other_way_tells_plan_to_change_the_step():
    """
    Rewording it sends Execute back at the same wall, which is what the
    recorded run did: three identical failures, a replan, and three more.
    The failures are rewrites in one kernel session now, not
    separate Execute calls, before Plan hears about any of it. `zero()` is a
    fourth, free attempt ahead of the three charged ones -- a declared
    Step's first refusal costs nothing against `attempts_per_step`, the same
    as a scratch turn's.
    """
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        retried_code_reply("zero()", "one()", "two()", "three()"),
        plan_reply("Cut the hooks, differently"),
        code_reply("four()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL)] * 4, budgets=Budgets(attempts_per_step=3))

    to_plan = json.dumps(_turns_of(llm, "plan")[1])

    assert "failed the same way" in to_plan
    assert "Rewording it will send Execute back at the same wall" in to_plan
    assert "Change what the Step asks for" in to_plan


def test_a_step_that_failed_variously_does_not_tell_plan_to_change_the_step():
    """ Sixty-nine of a hundred and thirty-five minutes of one recorded session were spent waiting at a Plan Gate, against six seconds of FreeCAD. Four of those waits were revisions of a Plan the user had already read and approved: a Step Execute could not build, checks that did not hold, part of the description nothing planned. A standing approval is the user saying "get on with it" — and it covers the Plan and only the Plan."""
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        retried_code_reply("zero()", "one()", "two()", "three()"),
        plan_reply("Cut the hooks, differently"),
        code_reply("four()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), failed(NO_MATERIAL), failed(IN_PIECES), failed(NO_MATERIAL)],
       budgets=Budgets(attempts_per_step=3))

    assert "Rewording it" not in json.dumps(_turns_of(llm, "plan")[1])


def test_a_step_that_lands_within_its_budget_leaves_no_mark_for_the_next_step():
    """
    Step 1's first refusal is free and Step 1 lands on the retry, so it never
    reaches `refused[number] >= attempts_per_step` -- the only place a Step
    is recorded to `Ledger`. Step 2's own Execute prompt must not carry a
    lesson from a Step that recovered on its own.
    """
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks", "Fillet the edges"),
        retried_code_reply("bad()", "good()", step=1),
        code_reply("edge()", first=2),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), None, None])

    for system in _system_messages(llm):
        assert "cut moved no material because the extrusion ran the wrong direction" \
            not in system, "a Step that landed within budget left a mark on a later prompt"


def test_ledger_dedupes_the_same_classified_failure():
    from core.ledger import Ledger
    from core.loop.execute import _FAILED_THIS_WAY

    ledger = Ledger(_FAILED_THIS_WAY)
    ledger.record("Cut the hooks", NO_MATERIAL)
    ledger.record("Cut the hooks", NO_MATERIAL)

    rendered = ledger.render()
    assert rendered.count("Cut the hooks") == 1
    assert "(x2)" in rendered


def test_ledger_keeps_a_trailing_gap_and_an_interior_gap_as_two_lines():
    """
    A model that ran dry and a model that skipped a Step it could write are
    different failures, so they must not dedupe into the one
    `_UNCLASSIFIED_LABEL` line this table had no entry for before them.
    """
    from core.ledger import Ledger
    from core.loop.execute import _FAILED_THIS_WAY

    ledger = Ledger(_FAILED_THIS_WAY)
    ledger.record("Cut the hooks", "You replied with no code for Step 4, and nothing "
                                   "after it either — you ran out of room before "
                                   "finishing this reply.")
    ledger.record("Cut the hooks", "You replied with no code for Step 2, but wrote "
                                   "code for a Step after it — you skipped a Step it "
                                   "could have written.")

    rendered = ledger.render()
    assert rendered.count("Cut the hooks") == 2
    assert "ran out of room and left the Plan's tail with no code" in rendered
    assert "skipped a Step it could have written while writing later ones" in rendered


def test_ledger_caps_by_lines_dropping_the_oldest():
    """
    `classify()` can return at most `len(phrase_table) + 1` distinct labels
    (`core/ledger.py`), so no realistic run's own attempts can exceed
    `max_lines` through the public API — `_enforce_caps` trims the line count
    defensively, against a classifier that returned more labels than its own
    table has, and this constructs exactly that to test it.
    """
    from core.ledger import Ledger

    ledger = Ledger(())
    ledger._entries["first"] = ["Cut the hooks", 1]
    ledger._entries["second"] = ["Fillet the edges", 1]
    ledger._entries["third"] = ["Pocket the boss", 1]
    assert ledger.max_lines == 1

    ledger._enforce_caps()

    assert len(ledger.render().split("\n")) == 1
    assert "Cut the hooks" not in ledger.render(), "the oldest entry was not the one dropped"
    assert "Fillet the edges" not in ledger.render(), "the oldest entry was not the one dropped"
    assert "Pocket the boss" in ledger.render()


def test_ledger_caps_by_characters_dropping_the_oldest():
    """
    `max_lines` is already saturated at the vocabulary's own size — the one
    thing that still grows a render past `max_chars` is the "(xN)" a
    classification already at maximum length grows once it recurs enough
    times. A run that fails every known way once, at maximum Step length,
    then keeps failing its most familiar way is exactly that: the least
    useful entry (the oldest, the first one seen) is what has to go so the
    entry the run is *still* hitting stays legible.
    """
    from core.ledger import Ledger
    from core.loop.execute import _FAILED_THIS_WAY

    ledger = Ledger(_FAILED_THIS_WAY)
    long_step = "A" * 60
    for phrase, _, __ in _FAILED_THIS_WAY:
        ledger.record(long_step, "Traceback:\n%s\n" % phrase)
    ledger.record(long_step, "ZeroDivisionError: nope")
    first_seen = next(iter(ledger._entries))
    assert len(ledger.render()) == ledger.max_chars

    for _ in range(30):
        ledger.record(long_step, "Traceback:\n%s\n" % _FAILED_THIS_WAY[-1][0])

    assert len(ledger.render()) <= ledger.max_chars
    assert first_seen not in ledger._entries, "the oldest entry was not the one dropped"
    assert "(x31)" in ledger.render()


def test_ledger_caps_are_derived_from_the_phrase_table_not_invented():
    from core.ledger import Ledger, _UNCLASSIFIED_LABEL
    from core.loop.execute import _FAILED_THIS_WAY

    ledger = Ledger(_FAILED_THIS_WAY)

    assert ledger.max_lines == len(_FAILED_THIS_WAY) + 1

    labels = sorted((label for _, __, label in _FAILED_THIS_WAY), key=len, reverse=True)
    worst_labels = (labels + [_UNCLASSIFIED_LABEL])[:ledger.max_lines]
    worst_step = "x" * 60
    expected = sum(len("- %s: %s" % (worst_step, label)) for label in worst_labels) \
        + (len(worst_labels) - 1)
    assert ledger.max_chars == expected


def test_ledger_lines_never_contain_code_or_a_traceback():
    from core.ledger import Ledger
    from core.loop.execute import _FAILED_THIS_WAY

    ledger = Ledger(_FAILED_THIS_WAY)
    ledger.record("Pocket the boss", NO_MATERIAL)

    rendered = ledger.render()
    assert "Traceback" not in rendered
    assert "PocketHook" not in rendered, "the traceback's own detail leaked into the ledger"
    assert "def " not in rendered and "()" not in rendered


def test_an_unclassified_failure_is_still_represented_not_dropped():
    from core.ledger import Ledger, _UNCLASSIFIED_LABEL
    from core.loop.execute import _FAILED_THIS_WAY

    ledger = Ledger(_FAILED_THIS_WAY)
    ledger.record("Do something odd", "ZeroDivisionError: division by zero")

    rendered = ledger.render()
    assert "Do something odd" in rendered
    assert _UNCLASSIFIED_LABEL in rendered


def test_the_ledger_survives_a_step_landing_and_reaches_the_next_steps_prompt():
    """
    Step 1 fails once and lands; Step 2's retry — after a *different* failure
    of its own — still carries what Step 1 failed at, because `attempts`
    resets on land (`core/loop.py:3626`) and the ledger must not.
    """
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks", "Fillet the edges"),
        code_reply("first_bad()", "second()"),
        code_reply("first_good()", "second()"),
        code_reply("second_again()", first=2),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), None, failed("boom")])

    step_2_retry = _system_messages(llm)[8]
    assert "Cut the hooks" in step_2_retry
    assert "moved no material" in step_2_retry


def test_the_ledger_reaches_plans_request_on_replan():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("one()"), code_reply("two()"), code_reply("three()"),
        plan_reply("Cut the hooks, differently"),
        code_reply("four()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL)] * 3, budgets=Budgets(attempts_per_step=3))

    replan_request = _turns_of(llm, "plan")[1]
    assert "already_failed_this_way" in replan_request
    assert "Cut the hooks" in replan_request["already_failed_this_way"]
    assert "def " not in replan_request["already_failed_this_way"]


def test_a_lint_rejection_fires_the_same_way_twice():
    from core.lint import LintResult

    rejected = LintResult(["The code does not parse: invalid syntax (line 3)"]).report()
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        retried_code_reply("first()", "second()", "third()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(rejected), failed(rejected), None])

    retried = [turn for turn in _turns_of(llm, "execute")
              if "you_have_failed_this_the_same_way_twice" in turn]
    said = retried[0]["you_have_failed_this_the_same_way_twice"]
    assert "not valid Python" in said


def test_changed_nothing_fires_the_same_way_twice():
    from core.freecad_apply import _CHANGED_NOTHING

    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        retried_code_reply("first()", "second()", "third()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed(_CHANGED_NOTHING), failed(_CHANGED_NOTHING), None])

    retried = [turn for turn in _turns_of(llm, "execute")
              if "you_have_failed_this_the_same_way_twice" in turn]
    said = retried[0]["you_have_failed_this_the_same_way_twice"]
    assert "Document is unchanged" in said









def test_a_standing_approval_is_not_asked_again_when_the_plan_is_revised():
    from core.loop.gate import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("The Document already has it"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 1, "the revised Plan asked again: %d Gates" % len(gate.asked)


def test_without_a_standing_approval_every_plan_still_reaches_the_gate():
    """This is opt-in, and the default is what it always was."""
    gate = gate_that()

    result, _, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("The Document already has it"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 2


def test_a_standing_approval_is_on_the_stream_as_an_approval_not_as_a_silence():
    from core.loop.gate import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    _, events, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("Already done"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    gates = events.of(GATE)

    assert [g.data.get("decision") for g in gates] == [None, "approve", None, "approve"]
    assert gates[0].data["asked"] is True
    assert gates[1].data["standing"] is True
    assert gates[2].data["asked"] is False, \
        "a Gate nobody was asked at is not distinguishable from one they were"
    assert gates[3].data["standing"] is True


def test_an_ordinary_approval_does_not_stand():
    from core.loop.gate import APPROVE, Approval

    gate = gate_that(Approval(APPROVE), Approval(APPROVE))

    _, events, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("Already done"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert all(g.data.get("standing") in (None, False) for g in events.of(GATE))


def test_a_standing_amendment_is_not_a_standing_approval():
    """
    Only an approval can stand. An amendment is the user changing the Plan, and
    the Plan they get back is one they have not read.
    """
    from core.loop.gate import AMEND, APPROVE, Approval

    gate = gate_that(Approval(AMEND, "make it 20mm", standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Build it 10mm"),
        plan_reply("Build it 20mm"),
        code_reply("build()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 2, "an amendment stood in for an approval"


def test_a_standing_approval_still_lets_the_user_be_asked_about_the_result():
    """
    It covers the Plan. What to do about a result that did not pass is not the
    Plan, and neither is deleting the user's own work.
    """
    from core.loop.gate import ACCEPT, APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))
    decided = []

    result, _, _, _ = driven_through(
        gate,
        [design_reply(A_NUT), planning_the_nut("Make it"), code_reply("make()"),
         suspect_reply(), assertion_reply(CHECKS), verdict_reply("pass")],
        checks=[SELF_INTERSECTION_HELD, assertion_failed(ASSERT_FAILED)],
        attachments=[RENDER_PNG],
        decide=lambda failure: decided.append(failure) or ACCEPT,
    )

    assert result.ok
    assert decided, "a standing approval swallowed the question about the result"


def test_a_standing_approval_does_not_cover_an_assumption_the_user_has_not_read():
    """
    The value nobody supplied is the one thing a standing
    approval cannot stand over: it is remade from nothing on every revision,
    and the Step's prose is the only place it is visible before the geometry.

    The recorded run had an undimensioned cutout approved as a rectangle, come
    back as a semicircular R15 and then as a circular R30 that swallowed the
    ring the clamp exists to close, with the user shown only the rectangle.
    """
    from core.loop.gate import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, events, _, _ = driven_through(gate, [
        plan_reply("Cut an assumed rectangular pocket 30 wide"),
        obsolete_reply("The Document already has it"),
        plan_reply("Cut an assumed circular R30 pocket"),
        code_reply("cut()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 2, "a new assumption went past a standing approval"
    assert [g.data.get("assumed") for g in events.of(GATE)] == [None, None, 1, None], \
        "the stream does not say why the Gate was asked at again"


def test_a_standing_approval_still_covers_a_revision_that_assumes_nothing_new():
    """
    What stands is approval of the path, not any assumed value: a revision
    that only rewrites how the part is built asks nobody.
    """
    from core.loop.gate import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Cut an assumed rectangular pocket 30 wide"),
        obsolete_reply("The Document already has it"),
        plan_reply("Cut an assumed rectangular pocket 30 wide"),
        code_reply("cut()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 1, "a Plan assuming nothing new was put to the user again"


def test_a_plan_that_assumes_nothing_is_covered_by_a_standing_approval():
    """The ordinary revision — no invented value in it at all — is untouched."""
    from core.loop.gate import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Pad the profile 30 mm"),
        obsolete_reply("The Document already has it"),
        plan_reply("Pocket the bore through all"),
        code_reply("cut()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 1


def test_a_standing_approval_does_not_survive_into_another_run():
    """It is one run's patience, not a setting."""
    from core.loop.gate import APPROVE, Approval

    driven_through(gate_that(Approval(APPROVE, standing=True)), [
        plan_reply("Build it"), code_reply("build()"), suspect_reply(), verdict_reply("pass"),
    ])

    gate = gate_that()
    driven_through(gate, [
        plan_reply("Build it"), code_reply("build()"), suspect_reply(), verdict_reply("pass"),
    ])

    assert len(gate.asked) == 1


def test_a_rejection_still_stops_a_run_that_had_a_standing_approval_before_it():
    from core.loop.gate import APPROVE, REJECT, Approval

    gate = gate_that(Approval(REJECT))

    result, _, _, tools = driven_through(gate, [plan_reply("Build it")])

    assert result.blocked
    assert tools.applied_codes() == []


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_part_whose_outside_nobody_carries_goes_back_to_the_drawing():
    """
    `silhouette` has raised "no feature carries the outer contour" for as long
    as it has existed and nothing ever acted on it. In the recorded run
    (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`) it stood in
    `conflicts`, the Gate showed the Steps, the user approved, and the Plan
    padded a profile it invented.

    So the drawing is asked before anything is planned, and a Design turn that
    writes the outer profile in settles it — the run goes on and never troubles
    the user with it.
    """
    said_in_passing = {
        "datums": [{"name": "the left end face",
                    "is": "the planar end face at the left"}],
        "envelope": {"length between the end faces": read("150 mm")},
        "features": [{"id": "fillets", "kind": "R2 fillets on all edges shown red",
                      "radius": read("2 mm"),
                      "edge_selection": [{"what": "the capsule-shaped exterior loops",
                                          "value": "each loop", "flag": "read"}]}],
    }
    the_contour = {"features": [
        {"id": "outer-profile",
         "kind": "obround (capsule) outer profile, 150 x 50, ends R25",
         "plane": read("the plane of the length and the height"),
         "position": {"the left end face": read("25 mm")},
         "radius": read("25 mm")}]}

    result, events, llm, _ = drive(
        [design_reply(said_in_passing), design_reply(the_contour),
         plan_reply("Pad the profile",
                    satisfies=everything(said_in_passing, the_contour)),
         code_reply("box()"), suspect_reply(), assertion_reply("assert True, 'the length'"), verdict_reply("pass")],
        attachments=[RENDER_PNG])

    assert result.ok, result.reason
    asked = json.loads(llm.calls[1][-1]["content"][-1]["text"])
    assert "outer profile" in asked["the_question"], asked.get("the_question")
    assert events.first(QUESTION) is None, \
        "the user was asked about a contour the drawing settled"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_shape_the_drawing_will_not_say_is_settled_and_shown_at_the_gate():
    """
    Neither the box the block replaced nor the block itself. Where the sheet
    will not say what the outer contour is, Design is told to settle it and to
    write down what it settled it off — and the choice stands above the Steps,
    where an earlier defect was that a padded profile was invisible.
    """
    said_in_passing = {
        "envelope": {"length between the end faces": read("150 mm")},
        "features": [{"id": "fillets", "kind": "R2 fillets on all edges shown red",
                      "radius": read("2 mm"),
                      "edge_selection": [{"what": "the rounded end surfaces",
                                          "value": "each loop", "flag": "read"}]}],
    }
    settled = {"features": [
        {"id": "outer-profile", "kind": "obround (capsule) outer profile",
         "plane": {"value": "the plane of the length and the height",
                   "flag": "assumed",
                   "because": "nothing on the sheet carries the contour; it is "
                              "read off the silhouette and the 150 mm overall"},
         "radius": {"value": "25 mm", "flag": "assumed",
                    "because": "half the 50 mm height, from the silhouette"}}]}
    asked = []

    result, events, llm, _ = drive(
        [design_reply(said_in_passing), design_reply({}), design_reply(settled),
         plan_reply("Pad the profile",
                    satisfies=everything(said_in_passing, settled)),
         code_reply("pad()"), suspect_reply(), assertion_reply("assert True, 'the length'"), verdict_reply("pass")],
        attachments=[RENDER_PNG], ask=lambda question: asked.append(question) or "")

    assert result.ok, result.reason
    assert asked == [], "the user was asked for the contour: %s" % asked
    told = json.loads(llm.calls[2][-1]["content"][-1]["text"])["the_question"]
    assert "settle it yourself" in told and "because" in told
    decided = json.dumps(events.first(PLAN).data["decided"])
    assert "read off the silhouette" in decided


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_design_is_given_the_line_work_of_the_views_in_millimetres():
    """
    The text layer told Design what the sheet *says*; a drawing says most of
    what it says in lines. Without them a distance nobody dimensioned — the
    height at which an arm meets a ring, an end flush with a tangent — has no
    evidence behind it at all, and the recorded run put three such values to the
    user and then invented them.

    So the geometry goes out beside the strings, in the part's own millimetres,
    said to be measurable rather than decorative. It rides with `_reconciled`
    now, as `_words_only`'s own list, rather than with a page-sized call: a
    page-sized call is narrowed to its own pictures and the printed-callout
    list, and the drawn line work is sheet-wide, not one page's.
    """
    needs_drawing(MODEL_MANIA)
    llm = ScriptedLLMClient([*_first_pass_turns(A_DATUM, [MODEL_MANIA]),
                             plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    groups = len(_the_groups([MODEL_MANIA]))
    said = _said_to_design(llm, at=groups * Budgets().readings)
    assert "line work" in said, "the views' own geometry never reached Design"
    assert "arc R" in said or "line (" in said, "the line work arrived with no entities"
    assert "derived" in said, \
        "nothing told Design that a value measured off the line work is not a guess"


def test_a_phase_that_answered_and_then_explained_itself_is_still_an_answer():
    """
    The recorded run (`logs/sessions/6fbd31e98fb540818086611841fa9aff.jsonl`)
    died on a correct reply: Design answered `{"design": {}, "question": ""}`
    and then wrote a paragraph explaining that the two strings it was asked
    about are the item numbers of a notes list. The whole reply had to parse or
    none of it did, so the object that was right there was thrown away and the
    run ended with an error the user could do nothing about.
    """
    from core.loop.prompt import _as_json

    said = ('{"design": {}, "question": ""}\n\nThe two unaccounted strings, `3.` and '
            '`4.`, are the item numbers of the NOTES list on page 2.')

    assert _as_json(said, "design") == {"design": {}, "question": ""}


def test_a_reply_with_no_json_in_it_is_still_a_phase_that_did_not_answer():
    """
    Reaching into a reply for an object must not turn prose into an answer: a
    phase that never replied with JSON is a phase that did not do its job, and
    the error quotes what it said instead.
    """
    from core.loop.budgets import PhaseError
    from core.loop.prompt import _as_json

    with pytest.raises(PhaseError) as raised:
        _as_json("I would rather describe this in words.", "design")

    assert "did not reply with JSON" in str(raised.value)


def test_a_phase_that_answered_in_prose_is_asked_once_more_rather_than_ending_the_run():
    """
    Measured on the gateway
    (`logs/sessions/ea390959745a453488138fddcc879635.jsonl`): ten Steps approved
    at the Gate, and Execute replied "I need to check the Hole feature's
    properties before writing the counterbore steps" — which is a look, and a
    look is a key of the object Execute answers with. The model had the answer
    and wrote it as a sentence, and the run died on it.
    """
    result, _, llm, tools = drive([
        plan_reply("Add a box"),
        "I need to check the Hole feature's properties first.",
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok, result.reason
    assert tools.applied_codes() == ["doc.addObject('Part::Box', 'Box')"]


def test_the_re_ask_quotes_what_the_phase_said_and_names_the_shape():
    """
    The retry is a second turn of the same conversation: the model's own
    sentence comes back as its own assistant message, and the nudge that
    follows it is short because the sentence is right there above it, not
    quoted into a fresh copy of the request.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        "I need to check the Hole feature's properties first.",
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    retry = llm.calls[3]

    assert retry[-2]["role"] == "assistant"
    assert "I need to check the Hole feature's properties first." in retry[-2]["content"]
    assert retry[-1]["role"] == "user"
    assert "Reply with exactly one JSON object" in retry[-1]["content"]


def test_the_evidence_is_cache_marked_only_on_the_route_that_honours_it():
    """
    `core/llm.py :: cache_control_supported` names the one route the spike
    measured. Everywhere else the block goes out exactly as it would have
    without this change — no `cache_control` key at all — so a provider that
    was never asked about it is not handed one either.
    """
    from core.events import EventStream

    anthropic = ScriptedLLMClient([design_reply(A_CUBE)])
    anthropic.model = "anthropic/claude-3-7-sonnet-20250219"
    loop_design({"intent": "Build this"}, anthropic, EventStream(), attachments=[RENDER_PNG])

    evidence = anthropic.calls[0][-1]["content"][:-1]
    assert evidence and evidence[-1].get("cache_control") == {"type": "ephemeral"}
    assert all("cache_control" not in block for block in evidence[:-1])

    scripted_client = ScriptedLLMClient([design_reply(A_CUBE)])
    loop_design({"intent": "Build this"}, scripted_client, EventStream(), attachments=[RENDER_PNG])

    other_evidence = scripted_client.calls[0][-1]["content"][:-1]
    assert other_evidence and all("cache_control" not in block for block in other_evidence)


def test_the_evidence_goes_out_before_the_changing_body():
    """
    A stable prefix has to be a prefix: the evidence is byte-identical turn to
    turn and the body is not, so the evidence goes first and the body last —
    reversed, the body's own change would be the first thing that differs and
    nothing after it could be read from a cache either.
    """
    from core.events import EventStream

    llm = ScriptedLLMClient([design_reply(A_CUBE), design_reply(A_CUBE)])
    loop_design({"intent": "Build this"}, llm, EventStream(), attachments=[RENDER_PNG])
    loop_design({"intent": "Build this, revised"}, llm, EventStream(), attachments=[RENDER_PNG])

    for call in llm.calls:
        assert call[-1]["content"][0].get("type") == "image_url"
        assert call[-1]["content"][-1].get("type") == "text"
    assert llm.calls[0][-1]["content"][:-1] == llm.calls[1][-1]["content"][:-1], \
        "the evidence differed between two turns that showed the same sheet"


def test_the_re_ask_of_a_phase_that_is_shown_pictures_keeps_the_pictures():
    """
    Design's request is content blocks, not a string. A re-ask that flattened
    it would take the drawing away in the turn the phase is being asked to read
    it more carefully.
    """
    _, _, llm, _ = drive([
        "Let me describe this part in words instead.",
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert _pictures_shown(llm, at=1), "the re-ask went out without the drawing"


def test_a_revision_is_shown_the_plan_it_is_revising():
    """
    The harness defect behind
    `logs/sessions/9958ccdc8e324a74ade9b08405be465b.jsonl`: the coverage check
    told Plan which entries nothing built, and Plan was asked for a new Plan
    without being shown the one it had written. It answered the complaint alone
    — two Steps for the two named entries — was told the nine Steps it had just
    dropped were now unplanned, wrote those again, dropped the two, and spent
    the whole revision budget without a single Step running.
    """
    part = {"envelope": {"length": read("80 mm"), "width": read("40 mm")}}
    both = everything(part)

    _, _, llm, _ = drive([
        design_reply(part),
        plan_reply("Pad the length", satisfies=both[:1]),
        plan_reply("Pad the length", "Pad the width", satisfies=both),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    revised = _turns_of(llm, "plan")[1]

    assert "the_plan_you_wrote" in revised, \
        "Plan was asked to rewrite a Plan it was not shown"
    assert revised["the_plan_you_wrote"][0].startswith("Pad the length")
    assert "every other Step unchanged" in revised["what_went_wrong_last_time"]


def test_the_first_plan_of_a_run_is_not_a_revision_of_anything():
    """A key that is there on every turn stops meaning anything on the turn it matters."""
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert "the_plan_you_wrote" not in _turns_of(llm, "plan")[0]




def test_a_re_read_that_corrects_the_axis_reaches_the_plan_instead_of_stopping():
    """
    The stop was the only move the loop had. The recorded run
    (`logs/sessions/daa17fb149474317840ef98439cfef6f.jsonl`) died before the
    Plan Gate on four findings that were the check's own fault, with nothing
    between "something is wrong" and "stop". Now the finding goes back to
    Design first, and a re-read that corrects it is a run that carries on.
    """
    result, events, _, tools = drive(
        _THE_AXIS_PUT_RIGHT, attachments=[RENDER_PNG])

    assert result.ok, events.first(BLOCKED) and events.first(BLOCKED).data["reason"]
    assert tools.applied_codes() == ["bore()", "pocket()"]


def test_the_re_read_is_told_what_the_finding_is():
    _, _, llm, _ = drive([
        design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
        design_reply({}),
    ], attachments=[RENDER_PNG])

    asked = json.loads(_said_to_design(llm, at=1))["the_question"]

    assert "round in the view its axis points at" in asked
    assert ("features" + UNDER + "clamp-bore" + UNDER + "axis") in asked


def test_what_the_correction_displaced_is_kept_in_the_reading():
    """A value is never lost — it is superseded, in writing, in the file."""
    _, events, _, _ = drive(_THE_AXIS_PUT_RIGHT, attachments=[RENDER_PNG])

    axis = part_file.read(events.last(PART).data["path"])["features"][1]["axis"]
    assert axis["value"] == "the height"
    assert axis["was"] == "the depth"
    assert axis["because"]


def test_a_re_read_that_settles_nothing_no_longer_stops_the_run():
    """
    The stop is gone. A finding the drawing would not settle and no
    measurement can settle either is a conflict the Gate is shown, not a block
    telling the user to hand-edit `logs/parts/*.yaml`.
    """
    result, events, _, tools = drive([
        design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
        design_reply({}),
        plan_reply("Pad the outer profile",
                   satisfies=everything(THE_CLAMP_READ_OFF_ONE_VIEW)),
        code_reply("pad()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok, result.reason
    assert tools.applied_codes() == ["pad()"]
    assert events.first(BLOCKED) is None




def test_a_sheet_that_contradicts_its_own_tracing_says_so_on_the_part(
        tmp_path, monkeypatch):
    """
    Several separate fixes arriving somewhere. `100` printed across a
    plate the pixels measure at 98.2 is two things that cannot both be true, and
    that is a conflict — not a correction, because which of the two is wrong is
    exactly what this cannot know.
    """
    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)

    found = _the_geometry_disagrees(["a.pdf"], EventStream())

    assert len(found) == 1
    said = str(found[0])
    assert "100" in said and "98.2" in said
    assert "the front view" in said


def test_a_geometric_conflict_names_both_points_it_is_about(tmp_path, monkeypatch):
    """
    The sentence a user reads at the Gate, whole. Two numbers and a view name
    the disagreement and not the thing it disagrees about: a sheet dimensions
    more than one distance in a view, and "100 printed where the pixels put
    98.20" does not say which of them to go and look at. The two points are
    the feet the dimension itself reached the tracing through, and they are
    the only thing here that identifies it.
    """
    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)

    found = _the_geometry_disagrees(["a.pdf"], EventStream())

    assert found[0]["about"] == (
        "the sheet prints 100 between (0.00, 15.00) and (100.00, 15.00) and the "
        "pixels put 98.20 mm between them, in the front view")


def test_a_redundant_constraint_reaches_the_part_in_the_words_of_a_conflict(
        tmp_path, monkeypatch):
    """
    A conflicting *or redundant* constraint index was asked for and the
    sweep carried the first only. A reading the solver is told the same thing
    about twice is as unsettled as one it is told two contradicting things
    about — neither is something a re-read can be asked — so it arrives at the
    Gate in the same words instead of not arriving at all.
    """
    class _AChildThatFoundBoth:
        def start(self):
            pass

        def kill(self):
            pass

        def reading(self, request, timeout=None):
            from core.sheets.sketch_check import Trouble, Verdict, as_response

            return dict(as_response(Verdict(
                view=request["view"],
                conflicts=[Trouble(
                    at=3, printed=50.0,
                    said="the 50 the sheet prints between (0.00, 5.00) and "
                         "(50.00, 5.00)")],
                redundant=[Trouble(
                    at=1, printed=42.0,
                    said="the arc the sheet prints \u00d842 against")])), ok=True)

    sheet = _a_sheet_that_contradicts_itself(tmp_path, wide=100.0, printed=100.0)
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(kernel, "Kernel",
                        lambda *_, **__: _AChildThatFoundBoth())

    found = _the_geometry_disagrees(["a.pdf"], EventStream())

    assert found == [
        {"about": "the 50 the sheet prints between (0.00, 5.00) and (50.00, 5.00),"
                  " in the front view",
         "stands": "the solver cannot satisfy this reading"},
        {"about": "the arc the sheet prints \u00d842 against, in the front view",
         "stands": "the solver cannot satisfy this reading"}]


def test_a_reading_too_far_out_is_past_what_this_check_can_see(
        tmp_path, monkeypatch):
    """
    The ceiling, held in place deliberately. A dimension reaches the geometry
    through the feet of its own extension lines, so a tracing further out than
    `sketch_check.A_FOOT` from the drawing has no face for the foot to name and
    there is nothing to compare.

    Widening the band does not fix that, it breaks it: measured on the four
    sheets, a band of a tenth of the printed value writes 16 distances of which
    3 then disagree by 4.5 to 4.9 mm, and an unambiguity rule instead of a band
    writes 25 of which 13 disagree — including a 150 matched to faces 98.77
    apart. Those sheets agree with themselves, so every one of those is a wrong
    face, not a finding.

    So this is a floor: it proves the tracing, the printed sizes and the
    printed distances agree. A misread digit is caught by the arithmetic that
    needs no correspondence at all — the sheet measuring its own numbers
    (`Printed.measures`), and a number nobody printed (`uncited`).
    """
    sheet = _a_sheet_that_contradicts_itself(tmp_path, wide=100.0, printed=108.0)
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)

    assert _the_geometry_disagrees(["a.pdf"], EventStream()) == []
