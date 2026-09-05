"""
Level 1 — the Plan -> Execute -> Review loop: phase prompt wording, traps, build-order doctrine and step/gap mechanics.

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
from core import design, loop, part_file, sheet_reader
from core.freecad_tools import TRAPS
from core.llm import REASONING, TEXT, Chunk, ProviderRefused, ScriptedLLMClient, StreamCutOff
from core.design import (
    UNDER, carried_by_no_source, decided_for_you, distilled, each_disagreement_once,
    entries, entry_at, measured_from_what_is_gone, merge, pointing,
    read_off_the_wrong_view, render, said_at,
)
from core.loop import (
    PROMPTS, Budgets, _PHASE_VOCABULARY, _language_sections, prompt,
    revisions_ceiling, run,
)
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness
from tests.loop_helpers import (
    RENDER_PNG,
    read,
    settled,
    A_CUBE,
    A_DATUM,
    A_PART,
    plan_reply,
    everything,
    design_reply,
    _first_pass_turns,
    code_reply,
    assertion_reply,
    look_reply,
    verdict_reply,
    suspect_reply,
    failed,
    scripted,
    drive,
    _system_messages,
    _asked_of_design,
    THE_PIPE_CLAMP,
    THE_2023_SHEET,
    _a_run_that_reads_the_sheet_every_way,
)


@pytest.mark.parametrize("turn, phase",
                         [(0, "design"), (1, "plan"), (2, "execute"), (3, "review_suspect"),
                          (4, "assertion"), (5, "review")])
def test_each_phase_sends_the_prompt_from_its_config_file(turn, phase):
    """
    The file reached the provider — not that the loader returned a string. The
    prompts are config, so this reads the config. FCC-3.

    Driven with an attachment, because Design is a turn only where there is
    something to read (FCC-86); every later phase shifts by one with it.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
        phase_body = handle.read().strip()
    sections = _language_sections()
    vocabulary = "\n\n".join(sections[heading] for heading in _PHASE_VOCABULARY[phase])
    expected = ("%s\n\n%s" % (phase_body, vocabulary)).replace(
        "{{traps}}", "\n".join("- " + trap for trap in TRAPS))

    assert _system_messages(llm)[turn] == expected


def test_the_traps_land_in_the_prompts_that_ask_for_them():
    """Review judges the outcome, not the API, and its file asks for no traps."""
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, plan_system, execute_system, suspect_system, review_system = _system_messages(llm)

    assert "{{traps}}" not in plan_system
    for text in (plan_system, execute_system):
        assert all(trap in text for trap in TRAPS)
    assert TRAPS[0] not in suspect_system
    assert TRAPS[0] not in review_system


def test_the_sketch_construction_rule_reaches_plan_and_execute():
    """
    The method rule is prompt copy, not a trap — it is how to build a Sketch,
    not knowledge about this FreeCAD build. It has to reach both phases that
    author geometry, and Review, which only judges the outcome, does not
    need it. FCC-20.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, plan_system, execute_system, suspect_system, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    for text in (plan_system, execute_system):
        assert "computed coordinates" in text or "coordinates you compute" in text
        assert "must genuinely be driven" in text
    assert "same centre" in plan_system
    assert "same centre vector" in execute_system
    assert "never invent a constraint type" in execute_system
    assert "must genuinely be driven" not in suspect_system
    assert "must genuinely be driven" not in review_system


def test_design_is_told_the_outer_contour_is_a_feature():
    """
    Prompt copy, not a trap: it is how a part is described, not knowledge about
    a FreeCAD build. The recorded run
    (`logs/sessions/57af6c09950047bf9f0582cd398d2a66.jsonl`, mm25-p1-1.pdf) put
    "capsule-shaped exterior perimeter" inside a fillet's edge selection, where
    nobody builds from it, and the envelope's three sizes were padded into a
    box that passed every check.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "The outside of the part is a feature, not three numbers" in design_system
    assert "obround" in design_system and "pad-outer" in design_system
    assert "A shape word inside another feature's prose is not the shape being recorded" \
        in design_system


def test_assertion_is_told_what_a_fillet_does_to_the_surface_it_replaced():
    """
    The same run's checks read `MajorRadius == 20` on bore mouths that were
    correctly filleted R2 — a torus of major radius 22 — reported four entries
    as unmet and spent a Plan revision on geometry that was already right.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    assertion_system = " ".join(_system_messages(llm)[4].split())

    assert "A fillet's own surface is not the surface it was cut into" in assertion_system
    assert "minor radius" in assertion_system
    assert "nominal ± the fillet radius" in assertion_system
    assert "Three distances are not a shape" in assertion_system


def test_execute_is_no_longer_asked_to_assert_inside_a_step():
    """
    FCC-54 asked Execute to assert an invariant of its own derivation, because
    at the time there was nowhere else to check a result. There is now: Plan
    writes the Acceptance list and the Assertion phase measures the finished
    run (ADR-0017). What the rule actually produced was ten to twenty
    preconditions per Step — `len(bodies) == 1`, `base.TypeId == 'PartDesign::Pad'`
    — and a precondition that does not hold is an `AssertionError` out of the
    transaction, so it spends one of the Step's three attempts. FCC-83.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, _, execute_system, _, _ = (" ".join(text.split()) for text in _system_messages(llm))

    for cancelled in ("invariant of its own", "assert it in the Step's code",
                      "thinner as the radius grows"):
        assert cancelled not in execute_system, \
            "execute.md still asks for an assert inside a Step: " + cancelled

    assert "Let a Step fail" in execute_system


def test_the_build_order_doctrine_names_base_additions_subtractions_then_dressup():
    """
    FCC-205: nowhere named the order a part is planned in, nor why it matters.
    A boolean — a Pad added to the solid, a Pocket or a Hole cut out of it, a
    Shell hollowing it — does not add to the Shape the Steps before it built,
    it replaces it, so a reference Execute worked out against the geometry
    before the boolean runs is a reference into a Shape already gone. Fillets
    and chamfers are planned last because that is the only order in which the
    edges their Step names are still the edges the boolean before them built,
    and a material card is last of all because nothing depends on it.

    Where a Step stands in the order is Plan's and not Execute's: a recorded
    run handed a fillet Step back asking for the round to be ordered before
    the rib, which trades one Step now for every Step after it.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, plan_system, execute_system, _, _ = (" ".join(text.split()) for text in _system_messages(llm))

    assert "base, then addition, then subtraction" in plan_system
    assert "fillet or a chamfer only once every one of those stands" in plan_system
    assert "boolean" in plan_system and "replaces it" in plan_system
    assert "it is the last Step of any Plan" in plan_system
    assert "A dress-up Step is planned against the solid the Steps before it leave" \
        in plan_system
    assert "A round is planned early only where a later Step must inherit it" in plan_system

    assert "computed off `Shape.Faces`" in execute_system
    assert "before the boolean that invalidates it, never after" in execute_system
    assert "`unbuildable` reports the geometry, not a rewrite of the Plan" in execute_system
    assert "where a Step stands in the order is not" in execute_system


def test_the_reading_rules_moved_to_the_design_phase_whole():
    """
    FCC-59's rules were live in plan.md and did not hold, so FCC-86 moved who
    reads and when rather than rewriting them a fourth time. They moved again
    with the file when Reading became Design, and they move as they are: what
    is asserted is that every one of them survived, that Plan no longer carries
    any of it, and that Review is still told none of it. FCC-59, FCC-86,
    FCC-93.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system, plan_system, _, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    for rule in ("A description is not a description of the *picture*",
                 "Design records; it does not invent",
                 "cannot both be true",
                 "an operation the drawing does not show",
                 "Read every plane the drawing uses",
                 "perpendicular"):
        assert rule in design_system, "the Design phase lost: " + rule
        assert rule not in plan_system, "plan.md still carries the Design rule: " + rule
        assert rule not in review_system


def test_a_value_carries_a_named_datum_and_a_read_or_assumed_flag():
    """
    Dimensions were read and positions were not: a magnitude arrived without
    what it was measured from, and a position with no named datum degrades into
    "somewhere sensible", which defaults to centred. Prose is what allows it —
    a sentence carries a number without carrying its datum. FCC-87 demanded the
    three fields in copy; they are now the shape of the model itself, keyed by
    the datum the value is measured from. FCC-87, FCC-93.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system, _, _, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "Every value carries its datum and its flag" in design_system
    assert 'A value is `{"value": ..., "flag": ...}`' in design_system
    assert "named the way the drawing names it" in design_system
    assert "A magnitude without its datum is the defect this phase exists for" \
        in design_system
    assert "Every value carries its datum and its flag" not in review_system


def test_a_value_the_model_settled_says_who_settled_it_and_on_what():
    """
    After decide-then-show, a user's answer and the model's own choice would be
    the same record on the part — which is the disease ADR-0019 was written
    against. So `assumed` carries `because`, the way `derived` carries `from`.
    ADR-0022.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM), plan_reply("Add a box"), code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "An `assumed` value carries `because` beside it" in design_system
    assert "who settled it and on what" in design_system
    assert "`assumed` without `because` is invalid" in design_system
    assert "settle it yourself" in design_system


def test_the_nonmetric_defaults_are_named_and_carry_no_number():
    """
    FCC-206: `Default assumptions` in the text-to-cad doctrine lists both
    non-metric conventions — units, base plane, up axis, a body being a
    closed positive volume — and numeric ones — wall thickness, fillet
    radius, clearance. Only the first kind describes the coordinate system
    every reading sits in rather than a value of the part, so only the first
    kind belongs in design.md; a numeric default there would be exactly the
    "nobody supplied it" FCC-114 already named, dressed up as a convention.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM), plan_reply("Add a box"), code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    for default in ("millimetres", "base plane", "+Z", "closed", "positive volume"):
        assert default in design_system, "a non-metric default is missing: " + default

    section = design_system[design_system.index("What needs no reading at all"):]
    section = section[:section.index("## Every value carries its datum and its flag")
                       if "## Every value carries its datum and its flag" in section
                       else len(section)]
    assert not re.search(r"\d", section), \
        "a numeric default reached design.md: " + section


def test_a_nonmetric_default_carries_no_flag_and_reaches_no_acceptance_entry():
    """
    FCC-206: units, the base plane, the up axis and a body being a closed
    positive volume are conventions of the model, not values read off
    anything, so a description that never names them still carries none of
    them as `assumed` — `entries()` and `decided_for_you()`, which is what
    the Acceptance list and the Plan Gate's `Decided for you` section are
    built from, see nothing about either one on A_PART, which never names
    them.
    """
    for entry in entries(A_PART):
        for word in ("unit", "millimetre", "base plane", "axis default", "+Z",
                     "closed volume", "positive volume"):
            assert word not in entry["about"].lower() and word not in entry["says"].lower()

    for line in decided_for_you(A_PART):
        for word in ("unit", "millimetre", "base plane", "axis default", "+Z",
                     "closed volume", "positive volume"):
            assert word not in line.lower()


def test_a_curve_is_not_read_until_its_centre_is_placed():
    """
    On the Model Mania 2025 Phase 1 sheet, `(R30)` places the central pocket's
    side walls and its leader starts at the end-bore axis, which is where the
    arc centre is. The first pass wrote the radius, filed the centre under "cannot
    be read", and Plan then put the centres at ±10 from the middle: the same
    R30, the same 10 mm web beside it, and the wall curving out instead of in.
    A radius with no centre is two shapes, and no number on the sheet chooses
    between them. FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "A curve is not read until its centre is placed" in design_system
    assert "every arc, fillet and round gets **two** values" in design_system
    assert "from the centre of the arc to the arc" in design_system
    assert "it is `read`, never `assumed`" in design_system


def test_the_views_decide_what_no_dimension_arrow_says():
    """
    The run declared the pocket directions "not defined" while SECTION A-A
    shows them outright — hatched where there is material. Which way an arc
    bows, which side is material, whether a cut goes in or a boss stands out:
    none of it is dimensioned on any drawing, and all of it is on the sheet.
    "Not dimensioned" is not "not defined". FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "A value with no dimension arrow on it is not an undefined value" \
        in design_system
    assert "hatched where there is material" in design_system
    assert "Where two readings satisfy the same numbers, say which one the view shows" \
        in design_system
    assert "name the views you looked in" in design_system


def test_every_callout_printed_on_the_sheet_has_to_be_accounted_for():
    """
    "It often says it cannot see something, and the PDF has everything on it."
    A description written from what the model happened to notice has no way to tell
    that it missed a callout; one written from a sweep of the sheet does, by
    counting. FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "First, sweep the sheet" in design_system
    assert "list **every callout printed on it**" in design_system
    assert "account for every entry on it" in design_system
    assert "illegible" in design_system


def test_a_fraction_of_a_named_overall_is_a_value_on_equal_terms_with_millimetres():
    """
    A picture with no dimensions on it has no millimetres, only proportions.
    Millimetres-only sends it to `question`, which is the behaviour that burned
    the run in FCC-85: the model asked about the medium rather than a value and
    the answer came back nobody's. One assumed overall is a wrong scale, which
    one number at the Plan Gate corrects; a dozen assumed dimensions is a wrong
    shape, which nothing corrects. FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "a fraction of a named overall" in design_system
    assert "a fraction is a full record, not a fallback" in design_system
    assert "Never stop to ask for dimensions" in design_system
    assert "Exactly one assumption in the whole description" in design_system


def test_the_datum_and_the_flag_reach_the_checks_without_anyone_rewriting_them():
    """
    FCC-87 asked Plan, in copy, to carry the datum and the flag into every
    Acceptance entry, and the hole `34.5` and `50.5` came through all the same:
    a rule in a prompt is a rule a phase can miss. There is no rewrite left to
    miss it. Plan is told it does not write the list, and Assertion is told what
    the entries it is handed are. FCC-87, FCC-94.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    _, plan_system, _, _, assertion_system, _ = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "You do not write the acceptance list" in plan_system
    assert "there is no list for you to write" in plan_system
    assert "The list is not somebody's paraphrase of the drawing" in assertion_system
    assert "carrying the datum it is measured from and how it was arrived at" \
        in assertion_system


def test_the_part_travels_as_a_structure_and_reaches_the_gate_laid_out():
    """
    The structure is the point: it is what the merge can refuse to overwrite,
    and what the Plan Gate can show as datums, an envelope and features rather
    than as paragraphs. It rides the stream as the model and reaches Plan as the
    document distilled out of it, laid out the same way. FCC-87, FCC-93.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Add a box", satisfies=everything(A_PART)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(),
        assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(DESIGN).data["model"] == A_PART, "the structure was flattened away"

    shown = json.loads(llm.calls[1][-1]["content"])["what_the_part_is"]
    assert shown == render(distilled(A_PART)), "Plan was shown something else again"
    for heading in ("Datums", "Envelope", "Features"):
        assert heading in shown
    assert "the left end face: 15 mm (read)" in shown
    assert "{" not in shown, "Plan was handed a JSON dump"


def test_plan_is_handed_a_finished_description_and_not_the_picture():
    """
    Far too much of the split is invisible in the background otherwise: Plan is
    told plainly that this is a description somebody else produced, that what it
    calls not defined stays not defined, and that nothing arrives beside it to
    be weighed against it. FCC-86, FCC-93.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    plan_system = " ".join(_system_messages(llm)[1].split())

    assert "what_the_part_is" in plan_system
    assert "You do not get the picture itself" in plan_system
    assert "What it lists as not defined stays not defined" in plan_system
    assert "nothing arrives beside it to be weighed against it" in plan_system
    assert "you_asked_and_were_answered" not in plan_system, \
        "plan.md still documents the second source this ticket removed"


def test_a_step_is_an_action_a_user_would_perform_not_a_call():
    """
    A Step written at the level of code is a call, and the same call is easy to
    write twice at two granularities — which is how one run drew the hexagon in
    Step 1 and again in Steps 3-8. The rule that closes it is the level: the
    action a user would perform, one piece of work per Step, no sub-steps and
    no restating another Step's work. FCC-74.

    The operation is still named, in engineering words rather than the FreeCAD
    spelling of its type.

    Which element a reference resolves to stays Execute's (ADR-0005).
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, plan_system, _, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "an action a user would perform" in plan_system
    assert "Execute translates that action into code" in plan_system
    assert "One piece of work is one Step" in plan_system
    assert "no sub-steps of its own" in plan_system
    assert "restates work another Step already carries" in plan_system

    assert "not in the FreeCAD spelling of its type" in plan_system
    assert "never an element" in plan_system
    assert "an action a user would perform" not in review_system


def test_plan_is_told_a_step_that_cannot_fail_alone_takes_the_plan_with_it():
    """
    Step 1 of the Model Mania run was the ring, the lug and an arm at 45° with
    twenty-two entries behind them. The arm's dimensions contradicted the
    overall length, Execute handed the whole Step back, the seven Steps after it
    were waiting on the pad it never made, and the run ended with an empty
    Document. Split, the ring and the lug land. FCC-127.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    plan_system = " ".join(_system_messages(llm)[1].split())

    assert "A Step nothing can fail on its own takes the whole Plan down with it" \
        in plan_system
    assert "is several pieces of work" in plan_system
    assert "Split where the entries split" in plan_system
    assert "never the same work twice" in plan_system


def test_plan_is_told_to_write_the_sum_and_not_only_the_answer():
    """
    The sum is checked before the Gate, so it has to be there to check: a Step
    that states only the answer states nothing anybody can work out. And the
    remedy for a sum that will not come out is a question, not a third number —
    which is what the recorded run tried, twice. FCC-127.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    plan_system = " ".join(_system_messages(llm)[1].split())

    assert "A number you worked out is written as the sum that worked it out" \
        in plan_system
    assert "Write the sum, not only the answer" in plan_system
    assert "Angles are in degrees" in plan_system
    assert "A sum that will not come out is a disagreement between two entries" \
        in plan_system


def test_design_is_told_to_replace_the_feature_the_later_page_supersedes():
    """
    The rule was already there — one part, not one model of two parts — and the
    run kept the Phase 1 ear beside the Phase 2 arm all the same, because the
    merge will not let a filled field change. `superseded_by` is how a phase
    that has already written the ear down says it is gone. FCC-127.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "`superseded_by` on the replaced feature" in design_system
    assert "Nothing of a feature carrying it is a value of the part any more" \
        in design_system
    assert "The datums and the envelope go with it" in design_system
    assert "never for two values of one feature that disagree" in design_system


def test_the_granularity_rules_this_reverses_are_gone_from_plan():
    """
    Asserted as a reversal, because copy is only cancelled when it is absent:
    a newer rule standing beside "three Steps, not one" leaves Plan free to
    obey either. FCC-58's four sentences go the same way its own reversal of
    the rules before it went. FCC-74.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    plan_system = " ".join(_system_messages(llm)[0].split())

    for reversed_rule in ("shortest ordered list", "and no smaller",
                          "should achieve, not the code", "one Step, not three",
                          "one FreeCAD operation with all its numbers",
                          "three Steps, not one",
                          "a distinct piece of geometry within a sketch is a Step of its own",
                          "roughly twenty Steps", "too coarse to be judged or corrected"):
        assert reversed_rule not in plan_system, \
            "plan.md still carries the rule this ticket reverses: " + reversed_rule


def test_plan_names_declared_numbers_and_not_how_a_reference_is_found():
    """
    FCC-66 asked Plan for links in the same prompt that forbids it writing how
    a reference is found (ADR-0005), and every Step that attached a sketch then
    failed Preflight for obeying. FCC-71 took the link back; FCC-75 took the
    whole field, and what is asserted here is the half of FCC-71 that outlives
    it — the link is not asked for anywhere.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, plan_system, _, _, _ = (" ".join(text.split()) for text in _system_messages(llm))

    assert "A link is written as the name you gave" not in plan_system, \
        "plan.md still asks Plan for the link FCC-71 takes back"
    assert '"Profile": "RootProfile"' not in plan_system, \
        "the parameters example still shows a link as a parameter"


def test_the_positive_form_of_the_lint_rules_reaches_execute():
    """
    Lint can only say no. Without the how, the model enumerates workarounds —
    it already answered the one-solid rule with max(solids, key=volume). The
    prompt carries what to build instead; Review is told neither. FCC-60.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, _, execute_system, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "computes its own Shape from its Profile" in execute_system
    assert "addProperty" in execute_system
    assert "Let a Step fail" in execute_system
    assert "bare `except`" in execute_system
    assert "computes its own Shape" not in review_system


def test_a_step_that_selects_a_subset_says_how_many_it_expects():
    """
    An obround pad has no longitudinal edge on its outer surface, so the Step
    that asked for the outline could only ever select the eight pocket
    corners — and it applied to those eight and reported success. Plan states
    the count in the Step's prose, Execute checks the selection against it.
    FCC-151.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])
    _, plan_system, execute_system, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "says how many" in plan_system
    assert "how many" in execute_system
    assert "len(edges) != 12" in execute_system
    assert "how many" not in review_system


def test_the_curve_and_thread_types_are_traps_not_prompt_copy():
    """
    Which types this build offers for a thread and for a free curve is
    knowledge about a specific FreeCAD build, so it lands through TRAPS and
    not in the prompt files. Plan cannot name a feature type it has not been
    told exists. FCC-58.
    """
    threads = [trap for trap in TRAPS if "AdditiveHelix" in trap]
    curves = [trap for trap in TRAPS if "BSplineCurve" in trap]

    assert len(threads) == 1, "the thread types are not a single trap"
    assert "Threaded" in threads[0]
    assert len(curves) == 1, "the curve types are not a single trap"
    assert "involute" in curves[0]
    for phase in ("plan", "execute"):
        with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
            assert "AdditiveHelix" not in handle.read(), \
                "a build-specific type was written into %s.md" % phase


def test_the_tessellated_bound_box_is_a_trap_not_a_number_in_the_prompt():
    """
    That `Shape.BoundBox` answers off the triangulation, and lands inside a
    curved face by the mesh's sagitta, is knowledge about this build — so it
    lands through TRAPS. The prompt says only what to read instead.

    A recorded run placed five sketches at `bb.XMin` of a pad with R25 rounded
    ends and built every bore, hole and pocket 0.18 mm off, in a Document that
    reopens with `XMin` at 0.0.
    """
    boxes = [trap for trap in TRAPS if "optimalBoundingBox" in trap]

    assert len(boxes) == 1, "the BoundBox reading is not a single trap"
    assert "triangulation" in boxes[0]
    assert "Surface.Center" in boxes[0]

    with open(os.path.join(PROMPTS, "execute.md"), encoding="utf-8") as handle:
        execute = " ".join(handle.read().split())
    assert "not off `Shape.BoundBox`" in execute, \
        "Execute no longer says where a coordinate comes from"


def test_the_constraint_spellings_are_a_trap_not_prompt_copy():
    """
    Which constraint types exist is knowledge about a specific FreeCAD build,
    so it lives in TRAPS and lands through the placeholder. FCC-20.
    """
    spellings = [trap for trap in TRAPS if "no 'Concentric'" in trap]

    assert len(spellings) == 1, "the constraint spellings are not a single trap"
    assert "Coincident', a, 3, b, 3" in spellings[0]
    for phase in ("plan", "execute"):
        with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
            assert "Concentric" not in handle.read(), \
                "a build-specific spelling was written into %s.md" % phase


def test_the_in_place_vector_methods_are_a_trap_and_reach_the_checks():
    """
    That `App.Vector.multiply` scales the vector it is called on rather than
    returning a scaled copy is knowledge about this build, so it lands through
    TRAPS — and it has to reach Assertion, which is where it was paid for.

    A recorded run's checks projected with `zdir.multiply(dot(raw, zdir))`,
    which zeroed `zdir` and left `xdir` 51.5 times too long, and reported 28 of
    34 entries unmet on a part that met them.
    """
    vectors = [trap for trap in TRAPS if "in place" in trap and "App.Vector" in trap]

    assert len(vectors) == 1, "the in-place Vector methods are not a single trap"
    assert "multiply" in vectors[0] and "sub" in vectors[0]
    assert "{{traps}}" not in prompt("assertion")
    assert vectors[0] in prompt("assertion"), \
        "the checks are not told which Vector methods mutate"


def test_a_missing_prompt_file_raises_rather_than_sending_an_empty_prompt():
    with pytest.raises(IOError):
        prompt("no_such_phase")


def test_plan_produces_an_ordered_list_of_steps_from_the_intent_and_a_snapshot():
    result, events, llm, tools = drive([
        plan_reply("Create a 10mm box", "Fillet the top edge"),
        code_reply("doc.addObject('Part::Box', 'Box')", "pass"),
        suspect_reply(), verdict_reply("pass", "Looks like a 10mm box."),
    ])

    assert result.ok
    planned = events.first(PLAN).data["steps"]
    assert planned == ["Create a 10mm box", "Fillet the top edge"]


def test_plan_is_given_the_intent_and_the_snapshot():
    _, _, llm, tools = drive([
        plan_reply("Only step"),
        code_reply("pass"),
        suspect_reply(), verdict_reply("pass"),
    ])

    asked = json.dumps(llm.calls[1])
    assert "Make a 10mm box" in asked
    assert "object_count" in asked, "Plan did not see a Snapshot"
    assert ("snapshot", {}) in tools.calls


def test_the_steps_run_in_the_order_the_plan_gave_them():
    _, events, _, tools = drive([
        plan_reply("First", "Second", "Third"),
        code_reply("first()", "second()", "third()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert tools.applied_codes() == ["first()", "second()", "third()"]
    assert [e.data["description"] for e in events.of(STEP_STARTED)] == ["First", "Second", "Third"]


def test_a_last_turn_with_neither_code_nor_obsolete_fails_the_step_not_the_run():
    """
    A reply with no code, no `obsolete` and no `unbuildable` fails the
    attempt, not the run. FCC-8.

    `revisions=0` keeps this on the terminal path: since FCC-61 a Step that
    cannot be built hands back to Plan, and this test is about the attempt, not
    about the handback. One Step total means every one of these is a trailing
    gap, so the first is free (`trailing_gap_retries`'s default of one) and
    only the three after it are charged against `attempts_per_step` — four
    attempts, not three, and every one of them is scripted for.
    """
    result, events, _, _ = drive(
        [plan_reply("Add a sketch")]
        + [json.dumps({"note": "not sure what to build here"})] * 4,
        budgets=Budgets(attempts_per_step=3, revisions=0),
    )

    assert not result.ok
    assert events.first(ERROR) is None
    blocked = events.first(BLOCKED)
    assert blocked is not None
    assert "Step 1 of 1" in blocked.data["reason"]
    assert len(blocked.data["tried"]) == 4
    assert all("no code for Step 1" in t["traceback"] for t in blocked.data["tried"])
    assert all("ran out of room" in t["traceback"] for t in blocked.data["tried"])


def test_a_step_that_never_writes_its_tail_still_terminates():
    """
    A trailing gap that recurs on every single call of a Step still bounds:
    `trailing_gap_retries` free continuations, then `attempts_per_step`
    charged ones, then a handback — never an unbounded re-ask. Each attempt is
    one provider call, so the arithmetic is exact: 3 free plus 1 charged is 4
    tried, from exactly 4 scripted replies and not a 5th — `ScriptedLLMClient`
    raises if the loop reaches for one.
    """
    result, events, _, _ = drive(
        [plan_reply("Only")] + [json.dumps({"note": "not sure what to build here"})] * 4,
        budgets=Budgets(attempts_per_step=1, revisions=0,
                        trailing_gap_retries=3),
    )

    assert not result.ok
    blocked = events.first(BLOCKED)
    assert blocked is not None
    assert len(blocked.data["tried"]) == 4




@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_every_design_call_of_a_run_carries_a_page_and_not_the_whole_sheet():
    """
    The first pass is one call per page now, not one call over the whole
    sheet (`core/loop.py :: _first_pass`) — the 2023 pipe clamp is two pages,
    so it costs two page-sized calls and one reconciling call that sees no
    pictures at all, rather than the single 27-picture, 43,923 character call
    this used to be. `tests/test_design_payload.py` measures the same three
    calls' totals against the two Model Mania sheets.
    """
    _, _, llm, _ = drive([
        *_first_pass_turns(A_DATUM, [THE_PIPE_CLAMP]), design_reply(A_DATUM),
        plan_reply("Only", satisfies=everything(A_DATUM)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[THE_PIPE_CLAMP])

    calls = [call for call in llm.calls if call[0]["content"] == prompt("design")]
    assert len(calls) == 4, "first pass (2 pages + reconcile) plus the re-read A_DATUM provokes"

    page_calls, reconciling = calls[:2], calls[2]
    for evidence in (call[-1]["content"][:-1] for call in page_calls):
        pictures = [block for block in evidence if block.get("type") == "image_url"]
        assert 0 < len(pictures) < 27, \
            "a page-sized call carried the whole sheet rather than its own page"

    reconciling_evidence = reconciling[-1]["content"][:-1]
    assert not [block for block in reconciling_evidence if block.get("type") == "image_url"], \
        "the reconciling call was shown a picture"
    assert reconciling_evidence, "the reconciling call saw nothing of the sheet at all"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_re_read_of_the_two_page_2023_sheet_still_knows_what_was_asked():
    """
    The sheet the recorded run was made against, and the case the Intent has
    to survive: a revision names one thing to change, and every look that sheet
    provokes — the second page among them — is taken knowing what that is.
    """
    intent = "Revise the clamp: open the \u00d842 bore to \u00d845 and change nothing else."

    _, _, llm, _ = _a_run_that_reads_the_sheet_every_way(THE_2023_SHEET, intent)

    groups = len(loop._the_groups([THE_2023_SHEET]))
    re_reads = _asked_of_design(llm)[groups:]
    assert re_reads, "the sheet provoked no re-read at all"
    assert [turn for turn in re_reads if turn.get("intent") == intent] == re_reads
    assert all("the_part_as_you_described_it" in turn for turn in re_reads), \
        "a re-read arrived without the model it is growing"
