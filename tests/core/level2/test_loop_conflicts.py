"""
Level 1 — the Plan -> Execute -> Review loop: found-vs-traced conflicts, the type cache and the closed-op vocabulary.

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
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, BUDGET_EXHAUSTED, CALL_STARTED, COST, ERROR,
    GATE, INTENT, PART, PLAN, PLAN_REVISED, DESIGN, PREFLIGHT_FAILED, QUESTION,
    REVIEW_SUSPECTED, REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL,
    TOOL_RESULT, UNBUILT, WRITING, EventStream, replay,
)
from core.design import core
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
import core.loop.describe as loop_describe
import core.loop.design as loop_design
import core.loop.prompt as loop_prompt
import core.loop.execute as loop_execute
from core.loop.results import Progress
from core.loop.run import Standing, Part, LastPass
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness
from core import kernel
from core.loop.design import _the_groups, _split_to_budget, _A_GROUP_BUDGET
from core.loop.describe import _round_evidence, _the_directions_are_the_drawings, _the_geometry_disagrees, _a_freecad_of_our_own, _every_number_is_the_sheets, _off_the_sheet, _the_sweep_is_the_sheets, _the_tracing_governs
from core.loop.execute import Looks, TYPE_CACHE_PATH
from core.loop.prompt import _pieces_of
from core.design.core import A_CONFLICT
from tests.loop_helpers import (
    RENDER_PNG,
    read,
    settled,
    A_PART,
    plan_reply,
    everything,
    design_reply,
    code_reply,
    _MultiTurn,
    assertion_reply,
    question_reply,
    verdict_reply,
    suspect_reply,
    FakeTools,
    failed,
    drive,
    _turns_of,
    GEAR,
    MISSING,
    CHECKS,
    _a_sheet_that_contradicts_itself,
    _a_sheet_of_many_outlines,
    _AChildThatCannotComeBack,
    _never_called,
    TRACED_IN_THE_PLAN,
    _THE_BORE_PUT_RIGHT,
    _traced,
    THE_CLAMP_READ_OFF_THE_WRONG_VIEW,
    TRACED_ROUND_IN_THE_UPPER_PLAN,
    THE_VIEW_LOOKED_AT_AGAIN,
    _re_read_as,
    _looked_at_the_view_again,
    A_BRACKET_IN_STEEL,
    THE_LENGTH,
    THE_MATERIAL,
    NO_CARD,
    a_run_that_cannot_assign_the_material,
    THE_LUG_FROM_A_PHOTO,
    THE_SAME_PICTURE_LOOKED_AT_AGAIN,
    CLOSED_OPS,
)


def test_a_sheet_that_agrees_with_itself_adds_nothing(tmp_path, monkeypatch):
    """
    A matching printed distance adds no conflict. The four-sheet evidence test
    holds the real-sheet measurements; this isolates the same no-conflict path
    without depending on a PDF.
    """
    sheet = _a_sheet_that_contradicts_itself(tmp_path, wide=100.0, printed=100.0)
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)

    assert _the_geometry_disagrees(["a.pdf"], EventStream()) == []


def test_without_a_freecad_to_build_in_the_description_is_untouched(
        tmp_path, monkeypatch):
    """
    Degrades the way the rest of the reading does: no disagreement found, and
    the machine having no headless FreeCAD reads its drawing exactly as it did
    before. What it says about the sheet is that nobody could check it.
    """
    def no_headless_freecad(*_, **__):
        raise RuntimeError("No headless FreeCAD found.")

    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(kernel, "Kernel", no_headless_freecad)

    assert [found["kind"] for found
            in _the_geometry_disagrees(["a.pdf"], EventStream())] == \
        [core.A_CHECK_HAZARD]


def test_the_reading_is_never_built_in_the_addon_s_own_process(
        tmp_path, monkeypatch):
    """
    The bug this arrangement exists for: `_describe_the_part` runs on the
    loop's worker thread, and a Document made there takes FreeCAD down with it
    — the window went in the middle of a run and the transcript stopped at the
    event before this check.

    So the outline goes down the pipe, and what comes back is the child's.
    """
    from core.sheets import sketch_check
    asked = []

    class _AChildThatAnswered:
        def start(self):
            pass

        def kill(self):
            asked.append("killed")

        def reading(self, request, timeout=None):
            from core.sheets.sketch_check import Trouble, Verdict, as_response

            asked.append(request)
            return dict(as_response(Verdict(
                view=request["view"],
                disagrees=[Trouble(
                    at=0, printed=100.0,
                    said="the sheet prints 100 between these two faces and the "
                         "pixels put 98.20 mm between them")])), ok=True)

    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(sketch_check, "checked", _never_called)
    monkeypatch.setattr(kernel, "Kernel",
                        lambda *_, **__: _AChildThatAnswered())

    found = _the_geometry_disagrees(["a.pdf"], EventStream())

    assert [entry["do"] for entry in asked if isinstance(entry, dict)] == ["reading"]
    assert asked[-1] == "killed", "the child outlived the sweep"
    assert "98.20" in found[0]["about"]


def test_a_child_that_dies_on_a_reading_costs_the_reading_and_not_the_run(
        tmp_path, monkeypatch):
    """
    Death is the normal outcome here. A verdict nobody could reach
    is no disagreement, the sweep goes on to the next outline, and the run
    carries the outline it could not check as a hazard rather than as a clean
    sheet.
    """
    class _AChildThatDied:
        def start(self):
            pass

        def kill(self):
            pass

        def reading(self, request, timeout=None):
            return None

    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    events = EventStream()
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(kernel, "Kernel",
                        lambda *_, **__: _AChildThatDied())

    assert [found["kind"] for found
            in _the_geometry_disagrees(["a.pdf"], events)] == \
        [core.A_CHECK_HAZARD]
    assert "the front view" in events.last(ERROR).data["message"], \
        "an unchecked reading is indistinguishable from a clean one"






def test_a_child_that_cannot_come_back_leaves_the_rest_unchecked_and_the_run_alive(
        tmp_path, monkeypatch):
    """
    The promise made and not kept: the reading check may cost a
    reading, never the run. A restart that cannot be made is the same outcome
    as a reading nobody could take — unchecked, said out loud, and Design goes
    on to the Plan Gate.
    """
    sheet = _a_sheet_of_many_outlines(tmp_path)
    events = EventStream()
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(kernel, "Kernel",
                        lambda *_, **__: _AChildThatCannotComeBack())

    assert [found["kind"] for found
            in _the_geometry_disagrees(["a.pdf"], events)] == \
        [core.A_CHECK_HAZARD]
    assert "the front view" in events.last(ERROR).data["message"]


def test_an_outline_nobody_could_check_is_a_hazard_on_the_part(tmp_path, monkeypatch):
    """
    A sheet nothing was wrong with and a sheet nobody could check both reach
    the Gate with no disagreement on them, and only one of the two is a
    verdict. Said on the `ERROR` stream it is said where the Gate does not
    look: the Gate reads the part. So it goes on the part, as the kind of entry
    it is — a check that could not be run, which is neither a reading to choose
    between nor a value to supply.
    """
    def no_headless_freecad(*_, **__):
        raise RuntimeError("No headless FreeCAD found.")

    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    events = EventStream()
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(kernel, "Kernel", no_headless_freecad)

    found = _the_geometry_disagrees(["a.pdf"], events)
    reading = each_disagreement_once(merge({"features": []}, {"conflicts": found}))

    assert [conflict["kind"] for conflict in found] == [core.A_CHECK_HAZARD]
    assert "the front view" in found[0]["about"]
    assert "the front view" in events.last(ERROR).data["message"]
    assert [conflict["about"] for conflict in reading["conflicts"]] == \
        [found[0]["about"]]
    assert core.HEADED[core.A_CHECK_HAZARD] in render(reading)


def test_a_twice_placed_conflict_reaches_the_gate_the_way_pointing_does():
    """
    A `twice_placed` finding rides to the Gate through the same conflicts
    section a `pointing` finding already uses -- the Gate reads the part,
    and both land under the same `A_CONFLICT` heading once `merge` has run.
    """
    model = {
        "features": [
            {"id": "boss-centre", "kind": "boss",
             "axis": {"value": "along the depth"},
             "position": {"the left end face": {"value": "60 mm"}}},
            {"id": "boss-right", "kind": "boss",
             "axis": {"value": "along the depth"},
             "position": {"the left end face": {"value": "60 mm"}}},
        ],
    }
    grown = merge({}, model)
    reading = each_disagreement_once(grown)
    identity = [conflict for conflict in grown["conflicts"]
                if "every shared datum agrees" in conflict.get("about", "")]

    assert [conflict["kind"] for conflict in identity] == [A_CONFLICT]
    assert core.HEADED[core.A_CONFLICT] in render(reading)
    assert "boss-centre" in render(reading) and "boss-right" in render(reading)


def test_a_child_that_never_starts_says_what_was_not_checked(tmp_path, monkeypatch):
    """
    `None` is a legitimate answer; silence is not. A worker that never came up
    used to check zero outlines and put nothing on the transcript, which is a
    sheet nobody could check arriving at the Gate looking exactly like a sheet
    nothing was wrong with.
    """
    def no_headless_freecad(*_, **__):
        raise RuntimeError("No headless FreeCAD found.")

    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    events = EventStream()
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(kernel, "Kernel", no_headless_freecad)

    assert [found["kind"] for found
            in _the_geometry_disagrees(["a.pdf"], events)] == \
        [core.A_CHECK_HAZARD]
    assert "the front view" in events.last(ERROR).data["message"]



def test_a_worker_that_could_not_start_names_why_on_the_transcript(monkeypatch):
    """
    `None` from `_a_freecad_of_our_own` is "nothing to start", said the same
    way whether headless FreeCAD is absent (expected) or `Kernel`
    regressed (a real bug) — `except Exception: return None` used to make
    those indistinguishable even on the transcript.
    """
    def broken_worker(*_, **__):
        raise RuntimeError("no headless binary")

    monkeypatch.setattr(kernel, "Kernel", broken_worker)
    events = EventStream()

    assert _a_freecad_of_our_own(events) is None
    errors = events.of(ERROR)
    assert len(errors) == 1
    assert "RuntimeError" in errors[0].data["message"]
    assert "no headless binary" in errors[0].data["message"]


def test_the_whole_reading_check_is_bounded_by_one_budget(tmp_path, monkeypatch):
    """
    A timeout per outline is no bound at all: a sweep can spend one timeout for
    every outline without returning. The budget belongs to the phase, and the
    outlines it does not reach are unchecked in the same words as any other.
    """
    asked = []

    class _AChildThatHangs:
        def start(self):
            pass

        def kill(self):
            pass

        def reading(self, request, timeout=None):
            asked.append(timeout)
            return None

    clock = iter([0.0] + [40.0 * step for step in range(1, 40)])
    sheet = _a_sheet_of_many_outlines(tmp_path)
    events = EventStream()
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(kernel, "Kernel",
                        lambda *_, **__: _AChildThatHangs())

    assert [found["kind"] for found
            in _the_geometry_disagrees(["a.pdf"], events, budget=60.0)] == \
        [core.A_CHECK_HAZARD]

    assert len(asked) < 4, "the budget bounded nothing"
    assert max(asked) <= 60.0, "one outline was given more than the whole budget"
    assert "4 of the outlines" in events.last(ERROR).data["message"]




def test_a_sheet_is_read_once_however_many_phases_ask_for_it(tmp_path, monkeypatch):
    """
    Three phases of `_describe_the_part` read the same attachments — the sweep
    of what is printed, the round evidence, and the geometry check — and each
    of them asks `sheet_reader.read` for the whole sheet again.

    Reading one is not cheap: measured on the four sheets in `tests/modelmania`,
    the three phases cost 17.3 s when each re-renders them and 6.2 s when the
    reader hands back what it already read. The cache is `_read_cached`, keyed
    by what the file is, so this holds the phases to one render and not the
    reader to one call.
    """
    read = []
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    sheet = replace(_a_sheet_that_contradicts_itself(tmp_path),
                    overview=str(path))

    def no_headless_freecad(*_, **__):
        raise RuntimeError("No headless FreeCAD found.")

    sheet_reader._READ_CACHE.clear()
    monkeypatch.setattr(sheet_reader, "_read",
                        lambda where, *_, **__: read.append(where) or sheet)
    monkeypatch.setattr(kernel, "Kernel", no_headless_freecad)

    _the_sweep_is_the_sheets({"features": []}, [str(path)])
    _round_evidence([str(path)])
    _the_geometry_disagrees([str(path)], EventStream())

    assert read == [str(path)]


def test_a_number_on_no_sheet_is_put_to_the_drawing_and_then_left_as_a_conflict(
        monkeypatch):
    """
    The other direction of the sweep: the part only ever grows. Asked
    once, and then recorded — the number may be right and derived from
    something this cannot see, so the Gate is where a person says so.
    """
    monkeypatch.setattr(loop_describe, "_off_the_sheet", lambda attachments, part: [1234.0])
    asked = []

    def design(payload, llm, events, attachments):
        asked.append(payload["the_question"])
        return {}, None

    monkeypatch.setattr(loop_describe, "design", design)

    grown = _every_number_is_the_sheets(
        {"features": []}, "Make the clamp", ["a.pdf"], None, None, Budgets(),
        lambda: False)

    assert asked and "1234" in asked[0]
    assert len(grown["conflicts"]) == 1
    assert "1234" in str(grown["conflicts"][0])


def test_a_number_off_the_sheet_is_signed_as_machine_written(monkeypatch):
    """
    `_every_number_is_the_sheets` never set `kind`, relying on
    `_which_kind_it_is`'s default. It is the second machine check, beside
    `crosswise`, whose entries fell through to that default; both are signed
    here the same way.
    """
    monkeypatch.setattr(loop_describe, "_off_the_sheet", lambda attachments, part: [1234.0])
    monkeypatch.setattr(loop_describe, "design", lambda payload, llm, events, attachments: ({}, None))

    grown = _every_number_is_the_sheets(
        {"features": []}, "Make the clamp", ["a.pdf"], None, None, Budgets(),
        lambda: False)

    assert grown["conflicts"][0]["kind"] == A_CONFLICT
    assert grown["conflicts"][0]["machine_written"] is True


def test_a_number_the_look_accounted_for_is_not_a_conflict(monkeypatch):
    """Where the re-read points at where it came from, there is nothing to record."""
    answers = iter([[1234.0], []])
    monkeypatch.setattr(loop_describe, "_off_the_sheet",
                        lambda attachments, part: next(answers))
    monkeypatch.setattr(loop_describe, "design",
                        lambda payload, llm, events, attachments: ({}, None))

    grown = _every_number_is_the_sheets(
        {"features": []}, "Make the clamp", ["a.pdf"], None, None, Budgets(),
        lambda: False)

    assert "conflicts" not in grown


def test_a_description_whose_numbers_are_all_the_sheets_is_left_alone(monkeypatch):
    monkeypatch.setattr(loop_describe, "_off_the_sheet", lambda attachments, part: [])
    monkeypatch.setattr(loop_describe, "design", lambda *a, **kw: 1 / 0)

    assert _every_number_is_the_sheets(
        {"features": []}, "Make the clamp", ["a.pdf"], None, None, Budgets(),
        lambda: False) == {"features": []}


def test_a_number_printed_on_one_sheet_of_two_is_not_off_the_sheet(monkeypatch):
    """
    `invented` is a sheet-level question and the sheets are read one at a time,
    so with two attachments the sheet that does not print a number reports it
    and the sheet that does says nothing. Unioned, every number printed on one
    of two sheets is on no sheet at all.
    """
    monkeypatch.setattr(loop_describe, "_sheets", lambda attachments: ["the first", "the second"])
    monkeypatch.setattr(sheet_reader, "invented",
                        lambda sheet, part: {"the first": [50.0, 1234.0],
                                             "the second": [1234.0]}[sheet])

    assert _off_the_sheet(["one.pdf", "two.pdf"], {"features": []}) == [1234.0]


def test_with_no_sheet_to_ask_nothing_is_off_the_sheet(monkeypatch):
    """An intersection over no sheets is every number, and no sheet said so."""
    monkeypatch.setattr(loop_describe, "_sheets", lambda attachments: [])

    assert _off_the_sheet(["one.pdf"], {"envelope": {"the length": "50 mm"}}) == []








def test_a_direction_the_sheet_settles_is_corrected_rather_than_stopping_the_run(monkeypatch):
    """
    The 2026-08-13 run stopped here and told the user to correct the axis in
    `logs/parts/*.yaml` and run it again. The view the circle was traced round
    in is the answer, and the loop is holding it.
    """
    _traced(monkeypatch, TRACED_IN_THE_PLAN)

    result, events, _, tools = drive(_THE_BORE_PUT_RIGHT, attachments=[RENDER_PNG])

    assert result.ok, result.reason
    assert tools.applied_codes() == ["bore()"]
    assert events.first(BLOCKED) is None


def test_the_direction_it_displaced_is_beside_it_in_the_reading(monkeypatch):
    """
    A correction is never a deletion: the axis it overruled stays under `was`,
    in the file, where the user reads what was decided for them.
    """
    _traced(monkeypatch, TRACED_IN_THE_PLAN)

    _, events, _, _ = drive(_THE_BORE_PUT_RIGHT, attachments=[RENDER_PNG])

    axis = part_file.read(events.last(PART).data["path"])["features"][0]["axis"]
    assert "the upper plan" in axis["value"]
    assert axis["was"] == "the depth"
    assert axis["because"], "the correction carries no evidence"


def test_what_was_decided_for_the_user_stands_above_the_steps(monkeypatch):
    """
    The Gate is the single point of interaction now, so a decision the model
    made is read there or it is not read at all.
    """
    _traced(monkeypatch, TRACED_IN_THE_PLAN)

    _, events, _, _ = drive(_THE_BORE_PUT_RIGHT, attachments=[RENDER_PNG])

    decided = events.first(PLAN).data["decided"]
    assert any("the upper plan" in line and "the depth" in line for line in decided), \
        "the Gate was not told what was decided, or not what it was decided against"


def test_nothing_is_asked_between_the_attachment_and_the_gate(monkeypatch):
    """
    A user answering "You decide" got a block twice. Nobody is asked for a
    value any more: the drawing is read, the model settles the rest, and the
    Gate shows it.
    """
    _traced(monkeypatch, TRACED_IN_THE_PLAN)
    asked = []

    result, _, _, _ = drive(_THE_BORE_PUT_RIGHT, attachments=[RENDER_PNG],
                            ask=lambda question: asked.append(question) or "")

    assert result.ok, result.reason
    assert asked == [], "the run asked a person: %s" % asked


def test_a_plan_that_asks_for_a_value_is_told_to_choose_and_never_the_user():
    """
    Plan's question used to reach the panel and block the run on a
    `threading.Event`. It reaches the sheet, and past that Design has nothing
    left to settle it with either, so the field stays `not_defined`.
    """
    asked = []

    result, events, _, tools = drive(
        [question_reply(MISSING), design_reply(),
         plan_reply("Sketch the blank, assumed module 2"),
         code_reply("a()"), suspect_reply(), verdict_reply("pass")],
        intent=GEAR, ask=lambda question: asked.append(question) or "module 2, 20 teeth")

    assert result.ok, result.reason
    assert asked == [], "the user was asked for a value: %s" % asked
    assert tools.applied_codes() == ["a()"]
    assert events.first(PLAN).data["unanswered"] == [MISSING]










def test_the_eight_holes_of_the_recorded_run_end_corrected_and_the_run_goes_on(monkeypatch):
    """
    The reply arrived at `axis`, the overrule was written at the position each
    finding is addressed to, and the merge dropped the axis for being filled
    already — so the re-read changed nothing, the loop broke on finding no
    fewer, and the run ended where it ended before.
    """
    assert len(pointing(THE_CLAMP_READ_OFF_THE_WRONG_VIEW)) == 8

    looked = _looked_at_the_view_again(monkeypatch, THE_CLAMP_READ_OFF_THE_WRONG_VIEW,
                                       THE_VIEW_LOOKED_AT_AGAIN)

    assert read_off_the_wrong_view(looked, TRACED_ROUND_IN_THE_UPPER_PLAN) == []
    assert said_at(looked, "features → clamp-bore → axis") == "along the height"


def test_the_correction_keeps_what_it_displaced_and_cites_what_earned_it(monkeypatch):
    """
    The evidence is the measurement off the sheet, not the finding's own
    account of the reading it stands against: a wrong reading does not earn an
    overrule, a traced circle does.
    """
    looked = _looked_at_the_view_again(monkeypatch, THE_CLAMP_READ_OFF_THE_WRONG_VIEW,
                                       THE_VIEW_LOOKED_AT_AGAIN)

    axis = entry_at(looked, "features → right-mounting-hole → axis")

    assert axis["was"] == "perpendicular to the front face, along the depth"
    assert "6.6" in axis["because"] and "the upper plan" in axis["because"]


def test_a_position_a_finding_named_is_left_as_it_was(monkeypatch):
    """
    The overrule is as wide as the evidence. The trace measures a circle in a
    view, which settles a direction and says nothing about how far the hole is
    from the front face.
    """
    looked = _looked_at_the_view_again(monkeypatch, THE_CLAMP_READ_OFF_THE_WRONG_VIEW,
                                       THE_VIEW_LOOKED_AT_AGAIN)

    assert said_at(looked, "features → right-mounting-hole → position → the front face") \
        == "12.5 mm"


def test_the_traced_profile_is_on_the_feature_before_the_plan_is_asked_for():
    """
    The recorded run (`logs/sessions/f8aaa6953cf54651bd57a3f65432d474.jsonl`,
    mm25-p1-1.pdf) reached the Plan with the tracing sitting in `conflicts` and
    nothing on the pocket, declared the Step unbuildable, asked once for the
    conflict to be settled, and then invented a 48.038476 mm wide profile of its
    own. The Plan is handed the traced profile instead, and the Gate is handed
    the disagreement.
    """
    from tests.core.test_design import THE_POCKET_IN_CONFLICT, THE_TRACED_POCKET
    from core.design.gaps import decided_for_you

    settled = _the_tracing_governs(json.loads(json.dumps(THE_POCKET_IN_CONFLICT)))
    at = "features \u2192 front-central-pocket \u2192 profile"

    assert said_at(settled, at) == THE_TRACED_POCKET
    assert "48.038476" not in json.dumps(settled)
    assert len(settled["conflicts"]) == 2
    assert [said for said in decided_for_you(settled) if said.startswith(at)]


def test_a_description_with_nothing_traced_in_conflict_is_left_alone():
    """A conflict the tracer never touched rides to the Gate as it always has."""
    part = {"features": [{"id": "the-arm", "profile": {"value": "40 mm wide",
                                                       "flag": "read"}}],
            "conflicts": [{"about": "the arm profile",
                           "stands": "the front view prints 40 mm",
                           "was_also_said": "SECTION A-A prints 45 mm"}]}

    assert _the_tracing_governs(json.loads(json.dumps(part))) == part









def test_a_run_whose_only_unbuilt_step_changes_no_geometry_reaches_review():
    """
    Nothing was waiting on the material, so nothing is replanned over it: the
    geometry is finished and a finished part is a part to judge.
    """
    result, events, _, tools = a_run_that_cannot_assign_the_material()

    assert not result.blocked, result.reason
    assert result.ok, result.reason
    assert events.first(REVIEW_VERDICT).data["verdict"] == "pass"
    assert events.first(PLAN_REVISED) is None, \
        "the Plan was rewritten over a Step no geometry depends on"
    assert tools.applied_codes() == ["pad()"]


def test_the_entry_the_unbuilt_step_satisfied_comes_back_unmet_with_the_reason():
    """
    A blocked run and a run with an unmet entry are different things. The user
    is told which acceptance entry was not reached and why, and the next run
    over this Document inherits it.
    """
    result, events, llm, _ = a_run_that_cannot_assign_the_material()

    assert result.unmet == [THE_MATERIAL]
    said = events.first(UNBUILT).data
    assert said["entries"] == [THE_MATERIAL]
    assert said["why"] == NO_CARD
    assert said["description"].startswith("Assign plain carbon steel to the Body")
    assert _turns_of(llm, "assertion")[0]["acceptance"] == [THE_LENGTH], \
        "the checks were asked to measure a Step that was never built"

def test_a_rejected_review_carries_the_unbuilt_step_s_entry_on_the_auto_answer(clean_auto_approve):
    """
    An unbuilt Step's acceptance entry is `not_reached`, folded into
    `progress["unmet"]` alongside whatever the checks left `unmet` (always
    empty here, since Assertion only reaches this point having passed).
    On an unattended run this reaches the stream through `decided()`'s
    `unmet` argument, on the `ANSWER` event Review's rejection produces —
    an auto-approved run must show the entry a Step never built as unmet
    the same way a user-approved one does (`result.unmet`, proven above).
    """
    clean_auto_approve.set_auto_approve_plan(True)

    result, events, _, _ = drive([
        design_reply(A_BRACKET_IN_STEEL),
        plan_reply({"do": "Pad the outline 80 mm long", "satisfies": [THE_LENGTH]},
                   {"do": "Assign plain carbon steel to the Body",
                    "satisfies": [THE_MATERIAL]}),
        json.dumps({"code": "pad()", "step": 1, "label": "Pad"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Pad"}],
                    "unbuildable": NO_CARD}}),
        suspect_reply(),
        assertion_reply(CHECKS),
        verdict_reply("fail", reason="the pad reads shallow"),
    ], attachments=[RENDER_PNG], budgets=Budgets(review_cycles=0))

    assert result.blocked
    answer = events.first(ANSWER).data
    assert answer["by"] == "auto"
    assert answer["unmet"] == [THE_MATERIAL]


def test_a_step_that_changes_the_shape_is_still_handed_back_to_plan():
    """
    The other direction of the same rule, and the conservative one: anything
    that is not plainly a property no geometry depends on is geometry, and a
    Step the part depends on still costs a revision rather than an unmet entry.
    """
    result, events, _, tools = drive([
        design_reply(A_BRACKET_IN_STEEL),
        plan_reply({"do": "Pad the outline 80 mm long", "satisfies": [THE_LENGTH]},
                   {"do": "Pocket a recess in the steel face",
                    "satisfies": [THE_MATERIAL]}),
        json.dumps({"declare": {"steps": [{"step": 1, "code": "pad()", "label": "Pad"}],
                    "unbuildable": "the recess cuts nothing"}}),
        plan_reply({"do": "Pad the outline 80 mm long", "satisfies": [THE_LENGTH]},
                   {"do": "Pocket a wider recess", "satisfies": [THE_MATERIAL]}),
        code_reply("pad()", "recess()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok, result.reason
    assert events.first(UNBUILT) is None
    assert events.first(PLAN_REVISED).data["because"] == "Execute could not build a Step"
    assert result.unmet == []
    assert tools.applied_codes() == ["pad()", "recess()"], \
        "Step 1 reads the same in both Plans and had already applied, so the revision " \
        "carries it forward instead of paying to apply it again"





def test_a_conflict_a_photo_cannot_settle_is_flagged_assumed_rather_than_kept_as_read(
        monkeypatch):
    """
    A single photo has nothing for `round_here` to trace, so a re-ask of the
    same picture answers the same way it did the first time and `_corrected_by`
    never earns its overrule. What the session that prompted this
    (`logs/sessions/f5d8007284aa4a57845782918305d6f0.jsonl`) shows is that
    reading standing untouched all the way to Plan and Execute, `flag: read`,
    with the mount built coaxial with the bore on the strength of a guess the
    part itself had already contradicted.
    """
    assert len(pointing(THE_LUG_FROM_A_PHOTO)) == 1

    _traced(monkeypatch, {})
    _re_read_as(monkeypatch, THE_SAME_PICTURE_LOOKED_AT_AGAIN)
    settled = _the_directions_are_the_drawings(
        json.loads(json.dumps(THE_LUG_FROM_A_PHOTO)), "Make it from the photo", [],
        None, EventStream(), Budgets(rereads=2), lambda: False)

    axis = entry_at(settled, "features → boss-front → axis")
    assert axis["flag"] == "assumed"
    assert axis["value"] == "perpendicular to the front face, along the depth"
    assert "either the axis or this position" in axis["because"]


def test_a_conflict_the_tracing_settles_is_left_read_by_the_new_step(monkeypatch):
    """
    The closing step added to `_the_directions_are_the_drawings` only ever
    reaches what the loop above it left standing — a tracing that did settle
    the direction must not be second-guessed back down to `assumed`.
    """
    looked = _looked_at_the_view_again(monkeypatch, THE_CLAMP_READ_OFF_THE_WRONG_VIEW,
                                       THE_VIEW_LOOKED_AT_AGAIN)

    axis = entry_at(looked, "features → clamp-bore → axis")
    assert axis["flag"] == "traced"







def test_mechanical_revisions_do_not_consume_execution_budget():
    """
    UNCOVERED and VALUE_UNDEFINED spend their own ceiling without reducing what
    UNBUILDABLE, OBSOLETE, ASSERTED, REJECTED, and AMENDED have left.

    The recorded run (logs/sessions/247c2aa...) spent 2 of 3 revisions on
    coverage checks before Execute ever tried Step 6, leaving exactly one
    attempt at the geometry. Mechanical checks are visible without a provider
    call, so they must not eat the budget for failures only a real attempt
    can surface.
    """
    step1_then_unbuildable = _MultiTurn()
    step1_then_unbuildable.append(json.dumps({"code": "step1()", "step": 1, "label": "Step"}))
    step1_then_unbuildable.append(json.dumps(
        {"declare": {"unbuildable": "the sketch is overconstrained"}}))

    result, events, _, tools = drive([
        design_reply(A_PART),
        plan_reply("Step 1"),
        plan_reply("Step 1 revised", satisfies=everything(A_PART)),
        step1_then_unbuildable,
        plan_reply("Step 1 without overconstraint", satisfies=everything(A_PART)),
        code_reply("step1_fixed()"),
        suspect_reply(),
        assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(revisions=3, mechanical_revisions=2),
       applies=[failed("the sketch is overconstrained")])

    assert result.ok, "mechanical revisions left room for one execution handback"
    revised = events.of(PLAN_REVISED)
    assert len(revised) == 2, f"Expected 2 revisions, got {len(revised)}"
    assert revised[0].data["because"] == "the Plan left part of the description unplanned"
    assert revised[1].data["because"] == "Execute could not build a Step"
    assert tools.applied_codes() == ["step1()", "step1_fixed()"]


def test_block_reason_names_mechanical_ceiling_when_hit():
    """
    When mechanical budget runs out, block_reason says so clearly.
    """
    result, events, _, _ = drive([
        design_reply(A_PART),
        *[plan_reply({"do": "v%d" % n, "values": ["ghost"]}, satisfies=everything(A_PART))
          for n in (1, 2, 3)],
    ], attachments=[RENDER_PNG], budgets=Budgets(revisions=5, mechanical_revisions=2))

    assert result.blocked
    blocked = events.first(BUDGET_EXHAUSTED)
    reason = blocked.data["reason"]
    assert "mechanical ceiling" in reason,         "block_reason distinguishes mechanical ceiling from execution ceiling"


def test_describe_type_is_cached_to_disk_between_runs(tmp_path, monkeypatch):
    """
    A describe_type call in the second run reads from disk and does not call
    the Tool again. The answer cost 23 seconds in a recorded run, for a fact
    about the build that never changes (logs/sessions/247c2aae...).
    """
    from core.loop.execute import Looks

    cache_path = str(tmp_path / "type_cache.json")

    first_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    first_looks.remember("describe_type", {"type_id": "PartDesign::Pocket"}, 
                        {"properties": ["Length", "Type"]})

    second_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    cached = second_looks.answer("describe_type", {"type_id": "PartDesign::Pocket"})

    assert cached == {"properties": ["Length", "Type"]}, \
        "second run reads type description from disk"


def test_describe_object_is_not_cached_to_disk_between_runs(tmp_path):
    """
    The boundary holds: describe_object describes an object in the user's
    Document, which changes with every Step. It stays a per-run cache only.
    """
    from core.loop.execute import Looks

    cache_path = str(tmp_path / "type_cache.json")

    first_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    first_looks.remember("describe_object", {"name": "Pad001"}, 
                        {"type": "PartDesign::Pad", "length": 10})

    second_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    cached = second_looks.answer("describe_object", {"name": "Pad001"})

    assert cached is None, \
        "describe_object is not cached to disk; the Document changes"


def test_different_freecad_version_does_not_reuse_type_cache(tmp_path):
    """
    A different FreeCAD build may have different properties on the same type,
    so the cache key includes the version.
    """
    from core.loop.execute import Looks

    cache_path = str(tmp_path / "type_cache.json")

    old_looks = Looks(cache_path=cache_path, freecad_version="0.20.0")
    old_looks.remember("describe_type", {"type_id": "PartDesign::Fillet"}, 
                      {"properties": ["Radius"]})

    new_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    cached = new_looks.answer("describe_type", {"type_id": "PartDesign::Fillet"})

    assert cached is None, \
        "different FreeCAD version means a fresh cache"


def test_corrupted_cache_file_does_not_crash_the_run(tmp_path):
    """
    A cache file that cannot be read is treated as absent. The run continues
    and the Tool is called.
    """
    from core.loop.execute import Looks

    cache_path = tmp_path / "type_cache.json"
    cache_path.write_text("{ this is not valid json")

    looks = Looks(cache_path=str(cache_path), freecad_version="0.21.0")
    cached = looks.answer("describe_type", {"type_id": "PartDesign::Hole"})

    assert cached is None, "corrupted cache is treated as empty"

    looks.remember("describe_type", {"type_id": "PartDesign::Hole"}, 
                  {"properties": ["Depth"]})



def test_two_sessions_writing_the_disk_cache_do_not_erase_each_other(tmp_path):
    """
    Two `Looks` each hold their own snapshot of the cache, read once at
    `__init__`. A second session that already has its snapshot before the
    first session's write used to overwrite the file with only its own
    entries, losing the first session's on `os.replace`.
    """
    from core.loop.execute import Looks

    cache_path = str(tmp_path / "type_cache.json")

    first_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    second_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")

    first_looks.remember("describe_type", {"type_id": "PartDesign::Pad"},
                        {"properties": ["Length"]})
    second_looks.remember("describe_type", {"type_id": "PartDesign::Fillet"},
                         {"properties": ["Radius"]})

    with open(cache_path, encoding="utf-8") as handle:
        on_disk = json.load(handle)

    assert len(on_disk) == 2, \
        "both sessions' entries survive; neither write erases the other"


def test_disk_cached_type_is_reported_as_already_known(tmp_path):
    """
    TOOL_RESULT events for describe_type from disk carry already_known=True,
    so the transcript does not lie.
    """
    from core.loop.execute import Looks, _look
    from core.events import TOOL_RESULT, EventStream

    cache_path = str(tmp_path / "type_cache.json")

    first_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    first_looks.remember("describe_type", {"type_id": "PartDesign::Pad"}, 
                        {"properties": ["Length"]})

    second_looks = Looks(cache_path=cache_path, freecad_version="0.21.0")
    events = EventStream()
    tools = FakeTools()

    _look(tools.registry, {"tool": "describe_type", "arguments": {"type_id": "PartDesign::Pad"}}, 
          events, second_looks)

    results = [e for e in events.of(TOOL_RESULT) if e.data["name"] == "describe_type"]
    assert len(results) == 1
    assert results[0].data["already_known"] is True, \
        "disk cache hit is reported as already_known in TOOL_RESULT"


def test_type_cache_path_is_repo_relative(tmp_path, monkeypatch):
    """
    TYPE_CACHE_PATH is computed at import as repo/logs/type_cache.json.
    Under FreeCAD it resolves through the Mod/ symlink to user data; under
    pytest the fixture redirects it to tmp_path.
    """
    
    monkeypatch.setattr(loop_execute, "TYPE_CACHE_PATH", str(tmp_path / "cache.json"))

    looks = Looks(cache_path=loop_execute.TYPE_CACHE_PATH, freecad_version="0.21.0")
    looks.remember("describe_type", {"type_id": "Test"}, {"ok": True})

    assert (tmp_path / "cache.json").exists(), \
        "cache wrote to the monkeypatched path"


@pytest.mark.freecad
def test_type_cache_fixture_redirects_to_tmp_path(tmp_path):
    """
    The autouse fixture redirects TYPE_CACHE_PATH to tmp_path, so type cache
    writes go there instead of repo/logs.
    """
    
    assert str(tmp_path) in loop_execute.TYPE_CACHE_PATH, \
        "TYPE_CACHE_PATH %s not under tmp_path %s" % (loop_execute.TYPE_CACHE_PATH, tmp_path)




def test_a_closed_step_op_off_the_36_list_is_refused_and_names_the_verb():
    """
    `_steps_of` checks a Step's `op` against `OP_VERBS` (36
    values) only when the Plan reply itself carries `"op_vocabulary":
    "closed"` — the same gate already put on `kind`. Refused rather than
    silently kept, so it never reaches Execute at all: no code is produced
    for it and its citation is left unaccounted for, which is what sends the
    entry it was meant to satisfy back through the ordinary coverage check
    instead of a code path invented for the purpose.
    """
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Squish the tab flat", "op": "squish",
          "satisfies": ["envelope → depth: 10 mm (read)"]}],
        CLOSED_OPS)

    assert steps == []
    assert satisfies == []
    assert ops == []
    assert len(unbuildable) == 1
    assert "squish" in unbuildable[0]["why"]


def test_a_closed_step_op_on_the_36_list_is_kept_with_its_op():
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Pad the base profile 6 mm", "op": "pad"}], CLOSED_OPS)

    assert steps == ["Pad the base profile 6 mm"]
    assert ops == ["pad"]
    assert unbuildable == []


def test_an_unmarked_reply_takes_the_legacy_path_op_or_not():
    """
    No `op_vocabulary` marker — the shape every recorded fixture from before
    this check and every bare-string Step already has — is never checked, whatever `op`
    (if any) rides along with it.
    """
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Squish the tab flat", "op": "squish"}], None)

    assert steps == ["Squish the tab flat"]
    assert unbuildable == []


def test_a_bare_string_step_is_unaffected_by_the_op_check():
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(["Pad the base profile 6 mm"],
                                                          CLOSED_OPS)

    assert steps == ["Pad the base profile 6 mm"]
    assert ops == [None]
    assert unbuildable == []

def test_a_closed_step_written_in_ukrainian_is_refused():
    """
    The vocabulary `config/prompts/language.md` holds is
    closed to one language as much as to one set of verbs, under the same
    `"op_vocabulary": "closed"` marker `_steps_of` already gates the 36-op
    check with.
    """
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Виконати паз шириною 6 мм", "op": "pocket",
          "satisfies": ["envelope \u2192 depth: 10 mm (read)"]}],
        CLOSED_OPS)

    assert steps == []
    assert satisfies == []
    assert ops == []
    assert len(unbuildable) == 1
    assert "English" in unbuildable[0]["why"]


def test_a_closed_step_written_in_english_is_unaffected_by_the_language_check():
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Pocket a 6 mm slot", "op": "pocket"}], CLOSED_OPS)

    assert steps == ["Pocket a 6 mm slot"]
    assert unbuildable == []


def test_a_closed_step_with_domain_symbols_is_not_mistaken_for_ukrainian():
    """
    None of Ø, an em dash or a degree sign is Cyrillic — `Ø40 bore` is
    ordinary English in this domain and must never be refused for language.
    """
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Drill a Ø40 bore — 30° chamfer both ends", "op": "hole"}],
        CLOSED_OPS)

    assert steps == ["Drill a Ø40 bore — 30° chamfer both ends"]
    assert unbuildable == []


def test_a_closed_step_with_one_cyrillic_label_is_not_refused():
    """
    A false positive here costs a Plan that was fine: a Step is Ukrainian
    when Cyrillic outnumbers Latin in its prose, not when it carries a
    single Cyrillic character — a label or a part name copied from the
    Intent in the user's own words.
    """
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Pad the profile 6.5 mm, label it Профіль",
          "op": "pad"}],
        CLOSED_OPS)

    assert steps == ["Pad the profile 6.5 mm, label it Профіль"]
    assert unbuildable == []


def test_an_unmarked_reply_takes_the_legacy_path_for_language_too():
    """The same legacy escape the op check gets applies here: a
    Plan predating the closed vocabulary marker is never refused for
    language, whatever it is written in."""
    from core.loop.plan import _steps_of

    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        [{"do": "Виконати паз шириною 6 мм", "op": "pocket"}], None)

    assert steps == ["Виконати паз шириною 6 мм"]
    assert unbuildable == []



def test_the_op_vocabulary_is_36_verbs_the_32_reachable_plus_four():
    """
    Count checked in test_op_vocabulary.py::test_core_loop_op_verbs_matches_the_prompts_own_list
    (full equality against the prompt), not repeated here as a copy.
    """
    from core.loop.plan import OP_VERBS

    for verb in ("thread", "extend", "trim", "offset"):
        assert verb in OP_VERBS


def test_an_off_list_op_sends_plan_back_naming_the_verb_and_the_36_allowed():
    """
    Excluding the Step is not by itself enough: a Plan told only "an entry is
    uncovered" has no reason to pick a different verb next turn, which is the
    same actionability rule already applied to a refused `kind`.
    The revision names the refused verb and hands back
    the 36 it may choose from instead.
    """
    _, events, llm, _ = drive([
        json.dumps({"declare": {"steps": [
            {"do": "Add a box", "op": "pad"},
            {"do": "Machine a lattice into it", "op": "lattice-mill"},
        ], "op_vocabulary": CLOSED_OPS}}),
        json.dumps({"declare": {"steps": [
            {"do": "Add a box", "op": "pad"},
            {"do": "Emboss a lattice texture", "op": "additive-primitive"},
        ], "op_vocabulary": CLOSED_OPS}}),
        code_reply("doc.addObject('Part::Box', 'Box')",
                   "doc.addObject('Part::Feature', 'Lattice')"),
        suspect_reply(), verdict_reply("pass"),
    ])

    revised = events.first(PLAN_REVISED)
    assert revised.data["because"] == "a Step named an operation off the closed list"
    assert "lattice-mill" in revised.data["reason"]
    retry = json.dumps(llm.calls[2])
    assert "Every build op has to be one of these 36" in retry
    assert "additive-primitive" in retry


def test_a_run_never_asks_execute_about_a_step_whose_op_was_refused():
    """
    A Plan reply names one buildable Step and one off-list `op`. The revised
    Plan Execute is actually shown carries the fixed Step alone — the refused
    op never reaches Execute, so no code is ever asked for it.
    """
    _, _, llm, _ = drive([
        json.dumps({"declare": {"steps": [
            {"do": "Add a box", "op": "pad"},
            {"do": "Machine a lattice into it", "op": "lattice-mill"},
        ], "op_vocabulary": CLOSED_OPS}}),
        json.dumps({"declare": {"steps": [
            {"do": "Add a box", "op": "pad"},
            {"do": "Emboss a lattice texture", "op": "additive-primitive"},
        ], "op_vocabulary": CLOSED_OPS}}),
        code_reply("doc.addObject('Part::Box', 'Box')",
                   "doc.addObject('Part::Feature', 'Lattice')"),
        suspect_reply(), verdict_reply("pass"),
    ])

    asked = json.dumps(llm.calls[3])
    assert "lattice-mill" not in asked
    assert "Add a box" in asked


def test_a_closed_marked_steps_op_reaches_the_apply_step_tool_call():
    """
    `core/lint.py` can only check the TypeId a Step's code
    builds against its declared `op` if that `op` actually reaches
    `apply_step`. A legacy, unmarked Plan (every other test in this file)
    still calls `apply_step` with exactly `{"code", "label"}` — no `op` key
    at all — so the lint check it feeds stays off for every recorded fixture.
    """
    _, events, _, tools = drive([
        json.dumps({"declare": {"steps": [{"do": "Add a box", "op": "pad"}],
                    "op_vocabulary": CLOSED_OPS}}),
        code_reply("doc.addObject('Part::Box', 'Box')", label="Add the box"),
        suspect_reply(), verdict_reply("pass"),
    ])

    assert ("apply_step", {"code": "doc.addObject('Part::Box', 'Box')",
                           "label": "Add the box", "op": "pad"}) in tools.calls
    call = next(e for e in events.of(TOOL_CALL) if e.data["name"] == "apply_step")
    assert call.data["arguments"]["op"] == "pad"


def test_split_to_budget_opens_each_shared_image_at_most_once():
    """
    `_split_to_budget` recomputes `_group_tokens` for overlapping
    subsets of `views` at every recursion level it descends into — a view's
    image must be opened with `PIL.Image.open` at most once per call,
    not once per level.
    """
    import types
    from PIL import Image as PILImage

    views = ["a", "b", "c", "d"]
    pieces_by_view = {view: [{"show": "/fake/%s.png" % view}] for view in views}

    def fake_pieces_of(sheet, views_arg):
        pieces = []
        for view in views_arg:
            pieces.extend(pieces_by_view[view])
        return pieces

    opened = []

    class FakeImage:
        def __init__(self, path):
            self.path = path
            self.width = 2000
            self.height = 2000

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def counting_open(path):
        opened.append(path)
        return FakeImage(path)

    original_pieces_of = loop_design._pieces_of
    original_isfile = os.path.isfile
    original_open = PILImage.open
    original_budget = loop_design._A_GROUP_BUDGET
    loop_design._pieces_of = fake_pieces_of
    os.path.isfile = lambda path: True
    PILImage.open = counting_open
    loop_design._A_GROUP_BUDGET = 1
    try:
        sheet = types.SimpleNamespace(printed=False)
        _split_to_budget(sheet, views, 0)
    finally:
        loop_design._pieces_of = original_pieces_of
        os.path.isfile = original_isfile
        PILImage.open = original_open
        loop_design._A_GROUP_BUDGET = original_budget

    assert sorted(set(opened)) == sorted(opened), (
        "a shared image was reopened across recursion levels: %r" % opened)


def test_run_state_namespaces_are_dataclasses_that_reject_a_typo():
    """
    `progress`, `standing`, `part`, `last_pass` inside `_run` are
    `@dataclass` instances, so a typo'd field name is an immediate
    `AttributeError` rather than a silently created dict key.
    """
    for cls, typo in ((Progress, "aplied"), (Standing, "aproval"),
                       (Part, "teh_part"), (LastPass, "stepz")):
        instance = cls()
        with pytest.raises(AttributeError):
            getattr(instance, typo)


def test_a_fabricated_citation_is_refused_into_conflicts(monkeypatch):
    """
    `_the_citations_are_the_sheets` is the same seam as
    `_every_number_is_the_sheets`: the reading stays on the model, the
    refusal is appended to `conflicts` through `merge`.
    """
    import types
    words = [types.SimpleNamespace(text=word) for word in ["THE", "TOP", "VIEW", "60", "R8", "4X"]]
    monkeypatch.setattr(loop_describe, "_sheets",
                        lambda attachments: [types.SimpleNamespace(printed=words)])

    the_part = {"envelope": {"height": {
        "value": "40 mm", "flag": "read",
        "from": "SECTION B-B, the (40) reference dimension"}}}

    grown = loop_describe._the_citations_are_the_sheets(the_part, ["a.pdf"], Budgets())

    assert grown["envelope"]["height"]["value"] == "40 mm"
    assert len(grown["conflicts"]) == 1
    assert "(40)" in str(grown["conflicts"][0])


def test_an_honest_citation_is_left_alone(monkeypatch):
    import types
    words = [types.SimpleNamespace(text=word) for word in ["THE", "TOP", "VIEW", "R8", "4X"]]
    monkeypatch.setattr(loop_describe, "_sheets",
                        lambda attachments: [types.SimpleNamespace(printed=words)])

    the_part = {"envelope": {"height": {
        "value": "60 mm", "flag": "read", "from": "the top view, R8 4X"}}}

    assert loop_describe._the_citations_are_the_sheets(
        the_part, ["a.pdf"], Budgets()) == the_part


def test_with_no_sheet_a_citation_is_not_flagged(monkeypatch):
    """
    The same abstention as `_off_the_sheet`'s own
    `test_with_no_sheet_to_ask_nothing_is_off_the_sheet`: no sheet to check
    against is not evidence the citation is fabricated.
    """
    monkeypatch.setattr(loop_describe, "_sheets", lambda attachments: [])

    the_part = {"envelope": {"height": {
        "value": "40 mm", "flag": "read",
        "from": "SECTION B-B, the (40) reference dimension"}}}

    assert loop_describe._the_citations_are_the_sheets(
        the_part, ["a.pdf"], Budgets()) == the_part
