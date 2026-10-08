"""Loop level -- a fillet, chamfer or draft planned before a Pocket, Hole, pattern or Thickness is refused as a free patch, and a repeat ends as unmet."""
import json
import os
import re

from core.events import PLAN_REVISED
from core.loop.budgets import Budgets
from core.loop.plan import _fillet_before_a_cut
from core.prompts import ROOT, read_prompt

from tests.loop_helpers import (RENDER_PNG, _grown, assertion_reply, code_reply, design_reply, drive, plan_reply,
                                read, suspect_reply, verdict_reply)

with open(os.path.join(os.path.dirname(__file__), "plan_125_steps.json"), encoding="utf-8") as recorded:
    PLAN_125 = json.load(recorded)

SPLIT = ("Fillet R1 the outer edge of the blank, 1 edge, before the thread cut divides "
         "that edge into 3 pieces")
SHELL = ("Fillet R5 the inside corners of the housing, planned before the Thickness so the "
         "wall inherits the R5")
LATER = ["Pad Base", "Fillet R5 the top edge", "Pocket Slot", "Thickness the solid 3 mm", "Hole d10"]
ADDRESS = "envelope \u2192 length"


def _refused(steps, ops=None):
    return [early["at"] for early in _fillet_before_a_cut(steps, ops or [None] * len(steps))]


def test_the_recorded_plan_125_is_refused_at_its_step_4():
    """Fixture 125 cited the notch and the thinning, named no piece count and no inside corners, and blocked Steps 5-12."""
    assert _refused(PLAN_125) == [4]


def test_a_fillet_after_every_cut_is_not_refused():
    assert _refused(["Pad Base", "Pocket Slot", "Hole d10", "Fillet R5 the top edge"]) == []


def test_a_fillet_naming_the_later_cut_and_the_pieces_is_not_refused():
    assert _refused(["Pad Blank", SPLIT, "Pocket ThreadCut"]) == []


def test_a_fillet_naming_the_cut_but_not_the_pieces_is_refused():
    assert _refused(["Pad Blank", "Fillet R1 the outer edge before the thread cut", "Pocket ThreadCut"]) == [2]


def test_a_fillet_the_thickness_inherits_on_the_inside_corners_is_not_refused():
    assert _refused(["Pad Body", SHELL, "Thickness the solid 3 mm"]) == []


def test_the_shell_reason_does_not_excuse_a_pocket_behind_it():
    assert _refused(["Pad Body", SHELL, "Pocket Slot", "Thickness the solid 3 mm"]) == [2]


def test_a_chamfer_is_held_to_the_same_rule():
    assert _refused(["Pad Body", "Chamfer 2 mm the top edge", "Hole d10"]) == [2]


def test_a_draft_before_the_rounds_on_its_face_is_not_refused():
    assert _refused(["Pad Body", "Draft 3 degrees the side faces", "Pocket Slot",
                     "Fillet R2 the drafted face edges"]) == []


def test_the_op_a_step_declares_wins_over_its_first_word():
    assert _refused(["Pad Body", "Round the top edge", "Sketch hole"], [None, "fillet", "hole"]) == [2]


def test_a_pattern_counts_as_a_later_cut():
    assert _refused(["Pad Body", "Fillet R2 the top edge", "LinearPattern the boss"]) == [2]


def _drive(*plans, budgets=None):
    return drive([_design(), *plans, code_reply(*("a()" for _ in range(6))),
                  suspect_reply(), verdict_reply("pass")],
                 attachments=[RENDER_PNG], budgets=budgets or Budgets(mechanical_revisions=0))


def test_the_complaint_is_a_free_patch_that_names_the_offending_step():
    result, events, llm, _ = _drive(plan_reply(*LATER, satisfies=[ADDRESS]),
                                    plan_reply("Pad Base", "Pocket Slot", "Thickness the solid 3 mm",
                                               "Hole d10", "Fillet R5 the top edge", satisfies=[ADDRESS]))

    assert result.ok, result.reason
    revised = events.of(PLAN_REVISED)
    assert len(revised) == 1 and revised[0].data["revision"] == 0
    told = json.loads(llm.calls[2][-1]["content"])["what_went_wrong_last_time"]
    assert "Fillet R5 the top edge" in told and "Pad Base" not in told
    assert "every other Step unchanged" in told


def test_the_same_complaint_twice_ends_as_unmet_and_the_run_builds():
    never = plan_reply(*LATER, satisfies=[ADDRESS])
    result, events, _, tools = _drive(never, never, never, never)

    assert result.ok, result.reason
    assert len(events.of(PLAN_REVISED)) == 1
    assert any("Fillet R5 the top edge" in entry for entry in result.unmet)
    assert tools.applied_codes()


def _design():
    return design_reply({"envelope": {"length": read("80 mm")}})


def _prompts():
    return sorted(name for _, _, names in os.walk(ROOT) for name in names if name.endswith(".md"))


def _flat(name):
    return re.sub(r"\s+", " ", read_prompt(name))


def _paragraphs_on_rounds():
    for name in _prompts():
        for paragraph in re.split(r"\n\s*\n", read_prompt(name)):
            flat = re.sub(r"\s+", " ", paragraph)
            if re.search(r"\b(?:round|fillet|chamfer)", flat, re.I):
                yield name, flat


def test_no_prompt_states_the_unguarded_shell_exception_or_a_single_exception():
    """The shell exception had no guard and the split exception called itself the one."""
    for name, flat in _paragraphs_on_rounds():
        assert "rounded before it is thinned" not in flat, name
        assert "one exception" not in flat, (name, flat)


def test_the_early_round_conditions_are_stated_in_one_prompt_only():
    stating = sorted({name for name, flat in _paragraphs_on_rounds()
                      if "names the later feature and the number of pieces" in flat})
    assert stating == ["plan.md"]


def test_every_statement_that_a_round_is_planned_early_carries_its_condition():
    for name, flat in _paragraphs_on_rounds():
        for sentence in re.findall(r"[^.]*\b(?:round|fillet|chamfer)\w* (?:is|are) (?:planned|built) (?:early|before)[^.]*\.", flat):
            assert re.search(r"\bonly\b|\bnever\b|two cases", sentence), (name, sentence)
