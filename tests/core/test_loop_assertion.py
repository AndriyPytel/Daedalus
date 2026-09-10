"""
Level 1 — the Plan -> Execute -> Review loop: envelope/self-intersection checks, citation trims and reading of the sheet.

Split out of the single `test_py` god-file so `--dist
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
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness
from tests.loop_helpers import (
    _a_real_png,
    _a_real_pdf,
    RENDER_PNG,
    StubKernel,
    read,
    settled,
    A_CUBE,
    A_DATUM,
    A_PART,
    plan_reply,
    everything,
    acceptance_of,
    design_reply,
    _first_pass_turns,
    code_reply,
    assertion_reply,
    verdict_reply,
    suspect_reply,
    FakeTools,
    failed,
    scripted,
    drive,
    _turns_of,
    needs_drawing,
    MODEL_MANIA,
    THE_2023_CLAMP,
    _the_clamp_as_it_was_read,
    ANSWERED_BEFORE_IT_DISTILLED,
    NOBODY_PRINTED_A_TOLERANCE,
    _under_the_heading,
    _reaches,
    A_BORE_AND_THE_EVIDENCE_FOR_IT,
    A_CLAMP_ENVELOPE,
    THE_CLAMP_OUTLINE,
    _both_planes_in_one_step,
    _one_plane_each,
    THE_CLAMP_READ_OFF_ONE_VIEW,
    THE_CLAMP_READ_PROPERLY,
    _pictures_shown,
    _said_to_design,
    _asked_of_design,
    THE_PIPE_CLAMP,
    _a_run_that_reads_the_sheet_every_way,
    A_BOX_FROM_WORDS,
    _a_run_with_no_drawing,
)


def test_a_second_design_pass_over_the_recorded_reading_adds_no_entry():
    """
    A re-read is where the list tripled: every check runs again over the grown
    model and writes its findings again, in whatever words the pass used. The
    reading grows and what stands out of it does not, because an address is
    what makes two findings one and prose is what a pass varies.
    """
    read_once = merge({}, THE_2023_CLAMP)
    read_again = merge(read_once, json.loads(json.dumps(THE_2023_CLAMP)))

    assert len(read_once["conflicts"]) > len(THE_2023_CLAMP["conflicts"]) == 33
    assert each_disagreement_once(read_again)["conflicts"] == \
        each_disagreement_once(read_once)["conflicts"]







def test_a_gap_the_run_answered_is_not_a_gap_on_the_document():
    """
    The recorded run of the 2023 pipe clamp put three gaps to the drawing and
    the drawing answered two: the profile of the arm cutout, and the width of
    the ring split off the 1 mm and 2 mm callouts either side of it. Both value
    and gap then stood on the description, so the Gate showed a person a value
    beside a note saying nobody had supplied one (FCC-153).
    """
    document, logged, _ = _the_clamp_as_it_was_read()
    answered = [gap for gap in logged["not_defined"]
                if said_at(document, ANSWERED_BEFORE_IT_DISTILLED.get(gap["what"], ""))]

    assert len(answered) == 2
    assert [gap for gap in document["not_defined"] if gap in answered] == []


def test_a_gap_nobody_answered_reaches_the_gate_with_the_views_it_was_looked_in():
    """
    The third of them nobody ever answered: neither page prints a general
    tolerance. It survives whole, views and all, because the views it was
    looked in are what let a user point at the one that was missed.
    """
    document, _, _ = _the_clamp_as_it_was_read()

    assert document["not_defined"] == [NOBODY_PRINTED_A_TOLERANCE]


def test_the_evidence_keeps_every_gap_the_reading_held():
    """
    Retiring is not deleting. What nobody had supplied while Design was still
    reading is part of the reading, and the log beside the document is where
    the whole of it stays (ADR-0019).
    """
    document, logged, _ = _the_clamp_as_it_was_read()

    assert logged["not_defined"] == merge({}, THE_2023_CLAMP)["not_defined"]
    assert len(logged["not_defined"]) == len(document["not_defined"]) + 2


def test_the_gaps_the_gate_stands_over_are_the_gaps_plan_is_handed():
    """
    One list, because there is one document: the Gate stands over the
    description the Plan was made from, and `what_the_part_is` is that same
    rendering of it, so a gap cannot be open in one and closed in the other.
    Neither of them is what nobody had supplied when the sheet was first read —
    that is the reading, and it rides to the transcript whole.
    """
    _, events, llm, _ = drive([
        design_reply(THE_2023_CLAMP),
        plan_reply("Build the clamp", satisfies=acceptance_of(THE_2023_CLAMP)),
        code_reply("clamp()"),
        suspect_reply(),
        assertion_reply("assert True, 'the clamp'"),
        verdict_reply("pass"),
    ], budgets=Budgets(rereads=0), intent="Build the 2023 pipe clamp")

    document = part_file.read(events.last(PART).data["path"])
    handed = _turns_of(llm, "plan")[0]["what_the_part_is"]

    assert render(document) == handed
    assert _under_the_heading(handed, "Not defined") == [
        "  - general dimensional tolerances",
        "    looked in: notes, page 1, title block, page 1"]




def test_nothing_past_the_reading_opens_the_evidence():
    """
    Read by nobody is the whole point of writing it down: a phase that reaches
    back into the evidence is planning from what was distilled away, and the
    distillation would be a rendering nobody is held to. Checked by reading the
    source, the way every other architectural claim in this project is.
    """
    import core.loop.describe as loop_describe_module
    loop_dir = os.path.dirname(loop_describe_module.__file__)
    all_source = ""
    for filename in os.listdir(loop_dir):
        if filename.endswith(".py"):
            with open(os.path.join(loop_dir, filename), encoding="utf-8") as f:
                all_source += f.read() + "\n"
    written = ast.parse(all_source)

    functions = [node for node in ast.walk(written)
                 if isinstance(node, ast.FunctionDef)]
    assert {function.name for function in functions
            if _reaches(function, "logged") or _reaches(function, "evidence")} \
        == {"_written_down"}
    assert {function.name for function in functions
            if any(isinstance(node, ast.Name) and node.id == "_written_down"
                   for node in ast.walk(function))} == {"_describe_the_part"}




def test_a_plan_citing_the_document_it_was_handed_leaves_nothing_unplanned():
    """
    The description and the Acceptance still come off one model, so a Step that
    cites a line of the document it was shown is a Step that covers the entry
    that line is (ADR-0020). A second Plan turn here would be the coverage check
    reading a different list from the one Plan was handed.
    """
    result, _, llm, tools = drive([
        design_reply(A_BORE_AND_THE_EVIDENCE_FOR_IT),
        plan_reply("Bore the ring",
                   satisfies=acceptance_of(A_BORE_AND_THE_EVIDENCE_FOR_IT)),
        code_reply("bore()"),
        suspect_reply(),
        assertion_reply("assert True, 'the bore'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok, result.reason
    assert tools.applied_codes() == ["bore()"]
    assert len(_turns_of(llm, "plan")) == 1, \
        "the Plan was sent back over an entry it had cited"




def test_the_three_overall_sizes_are_checked_before_anybody_writes_a_check():
    from core.loop.assertion import the_envelope_measured

    code = the_envelope_measured(A_CLAMP_ENVELOPE)

    assert "optimalBoundingBox" in code
    assert "[30.0, 30.0, 108.0]" in code
    assert "these did not hold: the envelope" in code


def test_an_envelope_of_anything_but_three_plain_sizes_is_not_measured_here():
    from core.loop.assertion import the_envelope_measured

    assert the_envelope_measured(A_CLAMP_ENVELOPE[:2]) == ""
    assert the_envelope_measured(
        ["envelope → length: as long as the arm reaches (assumed)"]) == ""


def test_self_intersection_check_removes_its_temp_file_even_when_export_fails():
    """
    FCC-256: the `.brep` round-trip opens one temp file per solid
    (`core/py:SELF_INTERSECTION_CHECK`); a solid whose export raises
    must not leave it behind. Run once, over the whole Document, is proven
    behaviourally by `test_two_separately_valid_solids_that_overlap_each_other_still_hold`
    in `tests/test_self_intersection_level2.py` (real FreeCAD, two solids,
    one call), not by reading the check's own source here.
    """
    import glob

    from core.loop.assertion import SELF_INTERSECTION_CHECK

    class ExplodingShape:
        Solids = [object()]

        def isNull(self):
            return False

        def exportBrep(self, path):
            open(path, "w").close()
            raise RuntimeError("boom")

    class FakeObject:
        Name = "Boom"
        Shape = ExplodingShape()

    class FakeDoc:
        Objects = [FakeObject()]

    pattern = os.path.join(tempfile.gettempdir(), "*.brep")
    before = set(glob.glob(pattern))
    with pytest.raises(RuntimeError):
        exec(SELF_INTERSECTION_CHECK, {"doc": FakeDoc()})
    after = set(glob.glob(pattern))

    assert after - before == set(), \
        "the temp file must be removed even when exportBrep raises"


def test_a_self_intersecting_document_is_caught_before_a_provider_writes_checks():
    """A Document with a self-intersecting solid fails before Review ever sees it."""
    _, events, llm, tools = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG],
        checks=[{"ok": False, "traceback": "AssertionError: these did not hold: "
                                           "Twist self-intersects (BOPAlgo_SelfIntersect)"}],
        decide=lambda why: "accept")

    ran = [args["code"] for name, args in tools.calls if name == "check_assertions"]
    assert "BOPAlgo_SelfIntersect" in ran[0], "self-intersection was not measured first"
    assert events.first(ASSERTION).data["unmet"] == \
        ["Twist self-intersects (BOPAlgo_SelfIntersect)"]
    assert events.first(ASSERTION).data["confirmed"] == [], \
        "the self-intersection check measures nothing to confirm"
    assert not any(call[0]["content"].startswith(prompt("assertion")) for call in llm.calls), \
        "a provider was asked to write checks for a Document already known to self-intersect"


def test_the_envelope_check_runs_against_the_document_and_can_fail_it():
    """A part of the wrong overall size fails before a provider is asked."""
    A_PART_WITH_AN_ENVELOPE = {
        "envelope": {"length between the ends": {"value": "108 mm", "flag": "read"},
                     "depth front to rear": {"value": "30 mm", "flag": "read"},
                     "height of the right lug": {"value": "30 mm", "flag": "read"}},
    }
    _, events, llm, tools = drive([
        design_reply(A_PART_WITH_AN_ENVELOPE),
        plan_reply("Pad the outline", satisfies=everything(A_PART_WITH_AN_ENVELOPE)),
        code_reply("pad()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG],
        checks=[{"ok": False, "traceback": "AssertionError: these did not hold: "
                                           "the envelope. ... measures 108.00 x 30.00 "
                                           "x 50.00 mm"}],
        decide=lambda why: "accept")

    ran = [args["code"] for name, args in tools.calls if name == "check_assertions"]
    assert "optimalBoundingBox" in ran[0], "the envelope was not measured first"
    assert events.first(ASSERTION).data["unmet"], "nothing was reported unmet"
    assert events.first(ASSERTION).data["confirmed"] == [], \
        "the envelope check measures nothing to confirm"
    assert not any(call[0]["content"].startswith(prompt("assertion")) for call in llm.calls), \
        "a provider was asked to write checks for a part already known to be wrong"


def test_a_step_that_only_passed_on_the_retry_is_named_to_the_checks():
    """
    The ring split of the recorded run was shortened until the Body stayed in
    one piece and the shortened one is what the Document kept: passing the
    second time says the workaround was legal, not that it is still the feature
    the drawing asked for.
    """
    A_SPLIT = {"features": [{"id": "ring-split", "kind": "2 mm split",
                             "width": {"value": "2 mm", "flag": "read"}}]}

    _, _, llm, _ = drive([
        design_reply(A_SPLIT),
        plan_reply("Pocket the split", satisfies=everything(A_SPLIT)),
        code_reply("split_through()"),
        code_reply("split_shortened()"),
        suspect_reply(),
        assertion_reply("assert True, 'the split'"),
        verdict_reply("pass"),
    ], applies=[failed("this Step left a Body in more than one piece"), None],
        attachments=[RENDER_PNG])

    touched = _turns_of(llm, "assertion")[0]["entries_a_retry_touched"]
    assert "features → ring-split → width" in touched


def test_a_step_that_landed_first_time_names_nothing_to_the_checks():
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert "entries_a_retry_touched" not in _turns_of(llm, "assertion")[0]


def test_execute_is_handed_every_entry_against_the_value_it_resolves_to():
    """
    A Step cites an address; the value it needs is then a lookup. Without it
    the run that named this wrote `arm_z = sum(...) / len(arm_lines)` for a
    hole the description places on the longitudinal centre plane.
    """
    _, _, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Sketch the profile", satisfies=everything(A_PART)),
        code_reply("sketch()"),
        suspect_reply(),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    addressed = _turns_of(llm, "execute")[0]["what_each_entry_says"]
    assert addressed, "Execute was given the prose and no addresses"
    assert all(": " not in address for address in addressed)
    assert any(value for value in addressed.values())








def test_a_step_citing_two_planes_is_sent_back_before_it_is_ever_built():
    """
    Step 1 of the run that named this cited the outer profile in the elevation
    and the R12.5 lug in the plan, and drew both in the elevation: the flat end
    face at 108 became a round nose and the lug came out 25 mm tall where its
    own Step says 30.
    """
    _, _, llm, tools = drive([
        design_reply(THE_CLAMP_OUTLINE),
        _both_planes_in_one_step(),
        _one_plane_each(),
        code_reply("profile()", "lug()"),
        suspect_reply(),
        assertion_reply("assert True, 'the outline'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    told = [turn.get("what_went_wrong_last_time", "")
            for turn in _turns_of(llm, "plan")]
    assert any("2 different values of `plane`" in said for said in told), told
    assert any("the plane of the length and the depth" in said for said in told)
    assert tools.applied_codes()[:2] == ["profile()", "lug()"]


def test_a_plan_that_keeps_mixing_them_is_not_sent_back_for_ever():
    drive([design_reply(THE_CLAMP_OUTLINE)] + [_both_planes_in_one_step()] * 4
          + [code_reply("profile()"), suspect_reply(), assertion_reply("assert True, 'the outline'"), verdict_reply("pass")],
          attachments=[RENDER_PNG], budgets=Budgets(revisions=2))


def test_a_step_whose_citations_agree_is_planned_once():
    one_plane = {"envelope": {"length": {"value": "108 mm", "flag": "read"}},
                 "features": [THE_CLAMP_OUTLINE["features"][0]]}

    _, _, llm, tools = drive([
        design_reply(one_plane),
        plan_reply("Pad the outer profile", satisfies=everything(one_plane)),
        code_reply("profile()"),
        suspect_reply(),
        assertion_reply("assert True, 'the outline'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert len(_turns_of(llm, "plan")) == 1
    assert tools.applied_codes() == ["profile()"]


def test_what_is_printed_on_the_sheet_is_written_by_the_reader_not_the_model():
    """
    230 exact entries came back from one run as 22 sentences, and `off_by` —
    the arithmetic that catches 108 read as 100 — went with them.
    """
    from core.design import part_file

    _, events, _, _ = drive([
        design_reply(dict(A_CUBE, printed_on_the_sheet=["Page 1: some numbers, "
                                                        "two locations"])),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[MODEL_MANIA])

    written = events.last(PART).data["path"]
    swept = part_file.read(part_file.evidence(written))["printed_on_the_sheet"]
    assert {entry["callout"] for entry in swept} != {"Page 1: some numbers, two locations"}
    assert any(entry.get("off_by") is not None for entry in swept), \
        "what the sheet measures of its own numbers did not survive"




def test_two_holes_that_disagree_and_nothing_to_settle_them_reach_the_gate():
    """
    The 2023 clamp. Both were built along the ring axis, and the screw that
    exists to pinch the split came out parallel to it — so the run grew a stop,
    and the stop killed runs that had read the sheet correctly.

    Neither now. There is no measurement here saying which of the two is
    misread — nothing was traced round anywhere — so the disagreement is what
    it is: a conflict, in the words of both, settled by nobody, riding to the
    Gate where a person reads it. ADR-0019's sentence about a model that picks
    between two disagreeing readings stands (ADR-0022).
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
    conflicts = json.dumps(events.last(DESIGN).data["model"]["conflicts"])
    assert "the two axes cross" in conflicts




def test_a_reading_whose_directions_agree_carries_on():
    result, _, _, tools = drive([
        design_reply(THE_CLAMP_READ_PROPERLY),
        plan_reply("Pocket the hole", satisfies=everything(THE_CLAMP_READ_PROPERLY)),
        code_reply("pocket()"),
        suspect_reply(),
        assertion_reply("assert True, 'the hole'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok
    assert tools.applied_codes() == ["pocket()"]


def test_a_run_with_nothing_to_read_writes_no_reading():
    """A run without a drawing describes no part, and a file saying so is a
    file to mistake for a reading later."""
    _, events, _, _ = drive([plan_reply("Only"), code_reply("box()"),
                             suspect_reply(), verdict_reply("pass")])

    assert not events.of(PART)










@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_sheet_is_shown_as_its_views_and_not_as_one_page():
    """
    The whole point of FCC-102. Sent as one page this sheet arrives at the
    provider's 143 DPI cap with 14 px of ink in a dimension digit; sent as the
    layout plus one picture per view it arrives at 279 DPI. A single picture
    reaching Design means the sheet went out at the resolution that misreads.
    """
    needs_drawing(MODEL_MANIA)
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    shown = _pictures_shown(llm)
    assert len(shown) > 1, "the sheet went out whole, which is what FCC-102 is about"
    assert not [b for b in llm.calls[0][-1]["content"] if b.get("type") == "file"], \
        "the PDF also went out as an opaque file block, at the provider's own DPI"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_design_is_told_what_the_sheet_prints_rather_than_asked_to_find_it():
    """
    The text layer is the draughtsman's own strings and cannot be misread, so
    the sweep is handed over instead of being compiled off a picture. `150` and
    `100` are on this sheet; the run that named the defect read one of them as
    something else.
    """
    needs_drawing(MODEL_MANIA)
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    said = _said_to_design(llm)
    assert "150" in said and "100" in said
    assert "\u00d8" in said, "nothing warned that the diameter sign is not in the layer"






@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_re_read_over_a_named_callout_is_shown_only_the_views_it_is_printed_in():
    """
    A_DATUM leaves enough of the pipe clamp's own callouts uncited that
    `_every_callout_accounted_for` sends Design back to the sheet — and the
    missing callouts are printed in one view apiece, so that call is the
    overview plus that view rather than a whole page's own worth of crops, the
    same narrowing a page-sized first-pass call gets, minus the printed list
    `_first_pass` keeps and a re-read does not (`_the_evidence`).
    """
    _, _, llm, _ = drive([
        *_first_pass_turns(A_DATUM, [THE_PIPE_CLAMP]), design_reply(A_DATUM),
        plan_reply("Only", satisfies=everything(A_DATUM)),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass"),
    ], attachments=[THE_PIPE_CLAMP])

    calls = [call for call in llm.calls if call[0]["content"].startswith(prompt("design"))]
    re_read = calls[-1][-1]["content"][:-1]
    characters = sum(len(block["text"]) for block in re_read if block.get("type") == "text")
    pictures = [block for block in re_read if block.get("type") == "image_url"]

    assert (len(pictures), characters) == (3, 396)
    assert len(pictures) < 27 and characters < 43923, \
        "the re-read carried the whole sheet rather than the views in play"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_callout_the_description_never_used_goes_back_to_design_naming_it():
    """
    The check `printed_on_the_sheet` was written for. A description that drops
    the Ø40 is not caught by counting the model's own sweep against itself —
    the callout is missing from both sides. Held against the file it is caught,
    and the question that comes back says the number.
    """
    needs_drawing(MODEL_MANIA)
    thin = {"envelope": {"length": read("150 mm")}}
    llm = ScriptedLLMClient([*_first_pass_turns(thin, [MODEL_MANIA]), design_reply({}),
                             plan_reply("Only"), code_reply("box()"),
                             suspect_reply(), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    design_calls = [call for call in llm.calls if call[0]["content"].startswith(prompt("design"))]
    asked = json.loads(design_calls[-1][-1]["content"][-1]["text"])
    assert "the_question" in asked, "Design was not sent back to the sheet at all"
    assert "40" in asked["the_question"]


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_looking_again_stops_when_it_stops_finding_anything():
    """
    A re-read is a provider call. Design answering with nothing, or with the
    same nothing twice, is a sheet that does not carry the value — and the run
    carries on with what it had rather than spending the budget proving it.
    """
    result, _, llm, _ = drive([*_first_pass_turns(A_DATUM, [MODEL_MANIA]), design_reply({}),
                               plan_reply("Only"), code_reply("box()"),
                               suspect_reply(), verdict_reply("pass")],
                              attachments=[MODEL_MANIA])

    assert result.ok, "the run did not finish: the re-look kept going"
    design_turns = [call for call in llm.calls if call[0]["content"].startswith(prompt("design"))]
    assert len(design_turns) == 3, \
        "the sheet was asked again after it had already answered with nothing"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_the_second_look_is_bounded_by_the_re_read_ceiling():
    """
    Design finding one more callout every time and never all of them would
    otherwise loop for as long as the sheet has numbers on it.
    """
    llm = ScriptedLLMClient(
        _first_pass_turns({"envelope": {"length": read("150 mm")}}, [MODEL_MANIA])
        + [design_reply({"envelope": {"width %d" % at: read("%d mm" % at)}})
           for at in (40, 35, 30, 20, 10)]
        + [plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry,
        budgets=Budgets(rereads=2), attachments=[MODEL_MANIA])

    design_turns = [call for call in llm.calls if call[0]["content"].startswith(prompt("design"))]
    assert len(design_turns) <= 4, "the re-look ran past its ceiling"







@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_every_design_turn_of_a_run_is_told_what_the_run_was_asked_for():
    """
    Eight call sites, one run, and the Intent on every one of them. It is the
    scope of the run — what is expected on the output — so a turn that reads
    the sheet without it reads for nothing in particular.
    """
    intent = "Build the pipe clamp on this sheet."

    _, _, llm, _ = _a_run_that_reads_the_sheet_every_way(MODEL_MANIA, intent)

    turns = _asked_of_design(llm)
    assert len(turns) == 8, "the run did not drive all eight Design call sites"
    assert [turn for turn in turns if turn.get("intent") == intent] == turns




@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_replayed_session_says_what_was_asked_before_it_says_what_was_read(tmp_path):
    """
    Read back off the log rather than off the live stream, because the session
    opened tomorrow is the one that has to be judged, and it is judged against
    what was asked.
    """
    intent = "Build the pipe clamp on this sheet."
    path = str(tmp_path / "session.jsonl")
    events = EventStream()
    events.log_to(path)
    llm = ScriptedLLMClient([design_reply(A_CUBE), plan_reply("Only",
                                                             satisfies=everything(A_CUBE)),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run(intent, llm=llm, tools=FakeTools().registry, events=events,
        attachments=[MODEL_MANIA])

    replayed, truncated = replay(path)
    kinds = [event.kind for event in replayed]

    assert not truncated
    assert kinds[0] == INTENT, "the run said something before it said what it was for"
    assert kinds.count(INTENT) == 1
    assert kinds.index(INTENT) < kinds.index(DESIGN)
    assert replayed[0].data["text"] == intent


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_the_reading_on_disk_says_what_it_was_read_for_and_not_only_what_off():
    """
    A reading opened tomorrow is the requirement the run was held to, and what
    was asked is half of reading it.
    """
    intent = "Build the pipe clamp on this sheet."

    _, events, _, _ = drive([design_reply(A_CUBE),
                             plan_reply("Only", satisfies=everything(A_CUBE)),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                            intent=intent, attachments=[MODEL_MANIA])

    written = events.last(PART).data["path"]

    assert part_file.asked_for(written) == intent
    assert part_file.provenance(written)["sheet"] == os.path.basename(MODEL_MANIA)


def test_a_pdf_that_cannot_be_rendered_here_still_goes_out_as_a_file_block():
    """
    The fallback, and the reason PyMuPDF is a package the addon wants rather
    than one it needs. A PDF this cannot open — a stub, a truncated file, no
    PyMuPDF at all — takes exactly the path it took before FCC-102: the `file`
    content block LiteLLM carries on /chat/completions, at whatever resolution
    the provider chooses, with the model saying so if it cannot read it (FCC-89).
    """
    drawing = _a_real_pdf()
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             *code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this bracket", llm=llm, tools=FakeTools().registry, attachments=[drawing])

    content = llm.calls[0][-1]["content"]
    files = [block for block in content if block.get("type") == "file"]

    assert len(files) == 1, "the PDF was not sent, or was sent as a picture"
    assert files[0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert not [block for block in content if block.get("type") == "image_url"]


def test_a_jpeg_reaches_the_provider_labelled_as_a_jpeg():
    """
    `data:image/png;base64` was stamped on every attachment, so a JPEG or a
    WebP was already going out labelled as a PNG. FCC-89.
    """
    photo = _a_real_png(suffix=".jpg")
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[photo])

    picture = [b for b in llm.calls[0][-1]["content"] if b.get("type") == "image_url"][0]
    assert picture["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_the_render_still_goes_to_review_as_a_png():
    """
    The same helper carries Review's four views and the Worker's render, which
    are PNGs we wrote ourselves. That path must come out of this unchanged.
    """
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                         views=[RENDER_PNG])

    picture = [b for b in llm.calls[-1][-1]["content"] if b.get("type") == "image_url"][0]
    assert picture["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_pdf_rides_into_review_beside_the_render():
    """
    Whatever carries a PDF to the phase that reads it carries it to Review as
    well, or the judgement is handed a drawing it cannot open — or a block the
    provider rejects, which turns a finished run into a Refusal at the last
    step. FCC-89.
    """
    drawing = _a_real_pdf()
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             *code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    result = run("Build this", llm=llm, tools=FakeTools(views=[RENDER_PNG]).registry,
                 attachments=[drawing], kernel=StubKernel())

    assert result.ok
    content = llm.calls[-1][-1]["content"]
    assert len([b for b in content if b.get("type") == "file"]) == 1
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_several_attachments_all_reach_the_design_phase():
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")])
    second = _a_real_png()

    run("From both angles", llm=llm, tools=FakeTools().registry,
        attachments=[RENDER_PNG, second])

    content = llm.calls[0][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 2


def test_an_intent_with_no_attachments_sends_what_it_always_did():
    """Attaching nothing shows nobody a picture, and Plan's request keeps its shape."""
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    shown = llm.calls[0][-1]["content"]
    to_plan = llm.calls[1][-1]["content"]

    assert [block["type"] for block in shown] == ["text"]
    assert isinstance(to_plan, str)
    assert json.loads(to_plan)["intent"] == "Make a 10mm box"


def test_an_attachment_that_has_been_moved_is_dropped_not_fatal():
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             *code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    result = run("Build this", llm=llm, tools=FakeTools().registry,
                 attachments=["/nowhere/gone.png", RENDER_PNG], kernel=StubKernel())

    assert result.ok
    content = llm.calls[0][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_a_run_that_had_an_attachment_says_so_on_the_stream():
    """
    Nothing recorded an attachment at all: the chips came off the bar and the
    per-Session log could not say the run had one, so a replayed transcript
    could not either. FCC-88.
    """
    _, events, _, _ = drive([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                            attachments=[RENDER_PNG])

    attached = events.first(ATTACHED)
    assert attached.data["files"] == [{"name": os.path.basename(RENDER_PNG),
                                       "path": RENDER_PNG}]
    assert [event.kind for event in events][:2] == [INTENT, ATTACHED]


def test_the_attachment_is_recorded_even_where_the_file_has_gone():
    """
    A clipboard paste is a temp file the panel deletes when the run ends, so
    the record has to survive its file — the name is in the event. FCC-88.
    """
    _, events, _, _ = drive([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                            attachments=["/nowhere/gone.png", RENDER_PNG])

    assert [f["name"] for f in events.first(ATTACHED).data["files"]] == \
        ["gone.png", os.path.basename(RENDER_PNG)]


def test_an_intent_with_no_attachment_adds_no_event():
    _, events, _, _ = drive([plan_reply("Only"), code_reply("box()"),
                             suspect_reply(), verdict_reply("pass")])

    assert events.first(ATTACHED) is None


def test_the_description_is_an_event_of_its_own_standing_before_the_plan():
    """
    It used to be a field on the Plan event, because Plan produced it. A phase
    of its own emits its own event, and a replayed transcript then shows it in
    the order it was actually produced. It carries the model, so the Session log
    holds the structure and not somebody's rendering of it. FCC-47, FCC-86.
    """
    _, events, _, _ = drive([design_reply(A_PART),
                             plan_reply("Only", satisfies=everything(A_PART)),
                             code_reply("box()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass")],
                            attachments=[RENDER_PNG])

    kinds = [event.kind for event in events]
    assert events.first(DESIGN).data["model"] == A_PART
    assert kinds.index(DESIGN) < kinds.index(PLAN)
    assert "reading" not in events.first(PLAN).data






def test_with_nothing_attached_the_part_is_described_from_the_words_alone():
    """
    Design runs whether or not there is a drawing, and the Gate then shows a
    description and the criteria the finished part has to satisfy rather than
    nothing. FCC-153.
    """
    _, events, llm, _ = _a_run_with_no_drawing()

    described = events.first(DESIGN).data["model"]
    acceptance = events.first(PLAN).data["acceptance"]
    assert described == A_BOX_FROM_WORDS
    assert acceptance == acceptance_of(A_BOX_FROM_WORDS)
    assert len(acceptance) == len(entries(described))
    assert json.loads(llm.calls[1][-1]["content"])["what_the_part_is"]


def test_a_reading_with_no_sheet_to_name_it_after_is_still_written_down():
    """
    What the run is planned, checked and built from is the file, drawing or no
    drawing, so a description written from words is written down like any
    other — named after what it was read off, there being no sheet.
    """
    _, events, _, _ = _a_run_with_no_drawing()

    written = events.last(PART).data["path"]
    assert len(events.of(PART)) == 1
    assert os.path.dirname(written) == part_file.WHERE
    assert os.path.basename(written).startswith(part_file.WITHOUT_A_SHEET)
    assert part_file.read(written) == events.first(DESIGN).data["model"]
    assert "sheet" not in part_file.provenance(written)


def test_a_question_nobody_answered_is_settled_by_design_and_shown_at_the_gate():
    """
    Design asks about what the part cannot be described without, so nobody
    answering cannot leave it out: the model settles every value it asked
    about, says in `because` what off, and the Gate is handed each of them
    (ADR-0022).
    """
    asked = []

    _, events, _, _ = drive(
        [design_reply(A_DATUM, question="How long, how wide and how tall is the box?"),
         design_reply(A_BOX_FROM_WORDS), plan_reply("Pad a 10 mm box"),
         code_reply("box()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass")],
        ask=lambda question: asked.append(question) or None)

    described = events.first(DESIGN).data["model"]
    settled_by_design = entries(described)
    decided = decided_for_you(described)
    assert asked == ["How long, how wide and how tall is the box?"]
    assert settled_by_design == entries(A_BOX_FROM_WORDS)
    assert all(entry["flag"] == "assumed" for entry in settled_by_design)
    assert all(entry_at(described, entry["about"])["because"]
               for entry in settled_by_design)
    assert [entry["about"] for entry in settled_by_design] == \
        [line.split(":")[0] for line in decided]
    assert events.first(PLAN).data["decided"] == decided


def test_the_checks_measure_a_part_described_from_words_alone():
    """
    A description built from the Intent has entries, so there is an Acceptance
    list for the Assertion phase to write checks against — as many criteria
    measured as the description has entries.
    """
    result, events, _, tools = _a_run_with_no_drawing()

    assert result.ok
    assert events.first(ASSERTION).data["confirmed"] == acceptance_of(A_BOX_FROM_WORDS)
    assert len(events.first(ASSERTION).data["confirmed"]) == len(entries(A_BOX_FROM_WORDS))
    assert any(name == "check_assertions" for name, _ in tools.calls)


def test_design_asks_the_user_itself_before_any_plan_exists():
    """
    The recorded run closed its first description with "Exact coordinate
    dimensions for the Ø35 hole … not defined" and then let Plan draft Steps
    around the gap, so the question reached the user out of a Plan that had
    already committed to a shape. Design owns the question: it asks for what it
    sees empty in its own model, before Plan, and the answer is merged in.
    FCC-93.
    """
    asked = []

    _, events, llm, _ = drive([
        design_reply(A_PART, question="Where are the Ø40 and Ø35 hole axes?"),
        design_reply({"features": [{"id": "hole-35", "kind": "Ø35 hole",
                                    "position": {"the left end face":
                                                 {"value": "25 mm", "flag": "assumed"}}}]}),
        plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], ask=lambda q: asked.append(q) or "25 mm from the left end")

    assert asked == ["Where are the Ø40 and Ø35 hole axes?"]
    kinds = [event.kind for event in events]
    assert kinds.index(QUESTION) < kinds.index(DESIGN) < kinds.index(PLAN), \
        "the question reached the user after a Plan had been drafted"

    shown = json.loads(llm.calls[2][-1]["content"])["what_the_part_is"]
    assert "the left end face: 25 mm (assumed)" in shown
    assert "the left end face: 15 mm (read)" in shown


def test_the_description_is_carried_into_execute_as_text():
    """
    Execute stays on text deliberately — it is called per Step and again per
    look, so the picture would multiply across dozens of calls.
    """
    _, _, llm, _ = drive([design_reply(A_PART),
                          plan_reply("Only", satisfies=everything(A_PART)),
                          code_reply("box()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass")],
                         attachments=[RENDER_PNG])

    asked = llm.calls[2][-1]["content"]
    assert isinstance(asked, str), "Execute was sent a picture, not text"
    assert json.loads(asked)["what_the_part_is"] == render(distilled(A_PART))


def test_the_description_reaches_plan_under_one_key():
    """
    One key, one source. Two were how the 100 mm was lost: the description and
    the answers arrived separately and Plan preferred the newer. FCC-93.
    """
    _, _, llm, _ = drive([design_reply(A_PART),
                          plan_reply("Only", satisfies=everything(A_PART)),
                          code_reply("box()"), suspect_reply(), assertion_reply("assert True"), verdict_reply("pass")],
                         attachments=[RENDER_PNG])

    request = json.loads(llm.calls[1][-1]["content"])
    assert request["what_the_part_is"] == render(distilled(A_PART))
    assert "how_the_drawing_was_read" not in request
    assert "you_asked_and_were_answered" not in request


def test_execute_is_told_nothing_extra_when_nothing_was_described():
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    assert "what_the_part_is" not in json.loads(llm.calls[1][-1]["content"])


def test_a_revision_plans_from_the_same_description_and_does_not_read_again():
    """
    The description depends on neither the Snapshot nor the feedback, so a
    second call at the same picture is a provider call spent on nothing. FCC-86.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("v1", satisfies=everything(A_PART)), code_reply("v1()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("fail", "not right"),
        plan_reply("v2", satisfies=everything(A_PART)), code_reply("v2()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert len(events.of(DESIGN)) == 1
    replan = json.loads(llm.calls[7][-1]["content"])
    assert replan["what_the_part_is"] == render(distilled(A_PART))
    assert replan["what_went_wrong_last_time"] == "not right"


def test_review_is_shown_the_drawing_beside_the_render():
    """
    Review compared a render against a sentence: the picture never reached it,
    so "does this resemble what I was shown" was not a question it could ask.
    """
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             *code_reply("box()"), suspect_reply(), verdict_reply("pass")])

    run("Build this bracket", llm=llm, tools=FakeTools(views=[RENDER_PNG]).registry,
        attachments=[RENDER_PNG], kernel=StubKernel())

    content = llm.calls[-1][-1]["content"]
    images = [block for block in content if block.get("type") == "image_url"]

    assert len(images) == 2, "Review got the render or the drawing, not both"
    assert all(i["image_url"]["url"].startswith("data:image/png;base64,") for i in images)
    assert RENDER_PNG not in json.dumps(content), "a path was sent instead of the bytes"


def test_review_gets_only_the_render_when_nothing_was_attached():
    """The no-attachment request keeps the shape it has always had."""
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                         views=[RENDER_PNG])

    content = llm.calls[-1][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_an_attachment_that_has_been_moved_does_not_break_review():
    llm = scripted([design_reply(), plan_reply("Only"), code_reply("box()"),
                     suspect_reply(), verdict_reply("pass")])

    result = run("Build this", llm=llm, tools=FakeTools(views=[RENDER_PNG]).registry,
                 attachments=["/nowhere/gone.png"], kernel=StubKernel())

    assert result.ok
    content = llm.calls[-1][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_review_never_sees_the_plan_the_code_or_executes_reasoning():
    _, _, llm, _ = drive([
        plan_reply("Create a 10mm box using Part::Box"),
        code_reply("doc.addObject('Part::Box', 'Box')",
                   thinking="I will use the Part workbench primitive here."),
        suspect_reply(), verdict_reply("pass"),
    ])

    asked = json.dumps(llm.calls[-1])
    assert "Create a 10mm box using Part::Box" not in asked, "Review saw the Plan"
    assert "doc.addObject" not in asked, "Review saw the code"
    assert "Part workbench primitive" not in asked, "Review saw Execute's reasoning"


def test_a_failed_verdict_returns_to_plan_and_never_to_execute():
    """`review_suspect` added a call ahead of it; Plan carrying the verdict is
    still the next Plan call after it, and Execute the one after that."""
    _, events, llm, _ = drive([
        plan_reply("Make it"),
        code_reply("make()"),
        suspect_reply(), verdict_reply("fail", "The box is 20mm, not 10mm."),
        plan_reply("Set Length to 10"),
        code_reply("fix()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    to_plan = json.dumps(llm.calls[6])
    to_execute = json.dumps(llm.calls[7])

    assert "The box is 20mm, not 10mm." in to_plan
    assert "The box is 20mm, not 10mm." not in to_execute, "the verdict reached Execute"
    assert events.first(PLAN_REVISED).data["steps"] == ["Set Length to 10"]


def test_the_verdict_is_surfaced_either_way():
    _, events, _, _ = drive([
        plan_reply("Only"),
        code_reply("box()"),
        suspect_reply(), verdict_reply("pass", "That is a 10mm box."),
    ])

    verdict = events.first(REVIEW_VERDICT)
    assert verdict.data["verdict"] == "pass"
    assert verdict.data["reason"] == "That is a 10mm box."


def test_three_attempts_per_step_then_blocked_with_what_was_tried():
    """`revisions=0`: the handback path has its own tests, this one is the budget."""
    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            code_reply("try2()"),
            code_reply("try3()"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
        budgets=Budgets(revisions=0),
    )

    assert not result.ok
    assert tools.applied_codes() == ["try1()", "try2()", "try3()"]
    blocked = events.first(BUDGET_EXHAUSTED)
    assert blocked is not None
    assert len(blocked.data["tried"]) == 3
    assert [t["traceback"] for t in blocked.data["tried"]] == ["one", "two", "three"]


def test_the_acceptance_list_rides_with_the_plan_it_belongs_to():
    """
    One list for the whole Plan, not one entry per Step: what is checked is the
    finished Document, and the check runs once the run is over. It reaches the
    Plan Gate through the event, because a criterion the user cannot see is a
    criterion they cannot correct. FCC-75.
    """
    _, events, _, _ = drive([
        design_reply(A_PART),
        plan_reply("Sketch the profile", "Pad it 10 mm", satisfies=everything(A_PART)),
        code_reply("sketch()", "pad()"),
        suspect_reply(), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    plan = events.first(PLAN)
    assert plan.data["steps"][1] == "Pad it 10 mm"
    assert plan.data["acceptance"] == acceptance_of(A_PART)


def test_a_run_with_nothing_described_still_plans_and_shows_an_empty_acceptance():
    """
    An empty list is not fatal and never was: the run goes on and Review judges
    alone, as it did before there was a list at all. Making it fatal would kill
    runs over a prompt regression, and the one fatal answer stays "no Steps".
    Derived rather than written, it is now the shape of a run with no drawing
    and no answers — which still plans. FCC-75, FCC-94.
    """
    result, events, _, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert result.ok
    assert events.first(PLAN).data["acceptance"] == []


def test_no_step_carries_named_parameters_any_more():
    """
    The whole mechanism, gone at every layer it crossed: the Plan event, the
    Execute request and the apply call. `type` plus real FreeCAD property names
    *is* Plan writing code, which is the level FCC-74 named as the root — the
    field kept demanding it. FCC-75.
    """
    _, events, llm, tools = drive([
        plan_reply({"do": "Pad the base",
                    "parameters": {"type": "PartDesign::Pad", "properties": {"Length": 10}}}),
        code_reply("pad()"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert events.first(PLAN).data["steps"] == ["Pad the base"]
    assert "parameters" not in events.first(PLAN).data
    assert "the_operation_this_step_names" not in llm.calls[1][1]["content"]
    assert all("parameters" not in args
               for name, args in tools.calls if name == "apply_step")
