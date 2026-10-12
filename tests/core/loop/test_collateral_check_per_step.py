"""
After a Step builds on an edit, an address of the base part that is gone and that the Step did not name undoes the Step and hands it back to Plan with the list. It is an ordinary hand-back: two in a row with nothing applied between them end the run through the existing going-nowhere rule.
"""
from core.events import APPLIED, PREFLIGHT_FAILED, EventStream
from core.llm import ScriptedLLMClient
from core.loop.execute import execute
from tests.event_helpers import events_of
from tests.loop_helpers import (FakeTools, _turns_of, StubKernel, code_reply, design_reply, drive,
                                plan_reply, suspect_reply, verdict_reply)

A, B = "plane z = 0", "hole diameter 30, axis (0, 0, 1), at (0, 0, 0)"
DESIGN = {"features": [{"id": "bore @ " + B, "edit": "changed"}, {"id": A, "edit": "kept"}]}


def _executed(before_now, after_now, changes, base=(A, B)):
    tools = FakeTools(base="x", faces=[{"base": list(base), "now": list(before_now)},
                                       {"base": list(base), "now": list(after_now)}])
    events = EventStream()
    written = execute(["Fill the bore"], tools.registry, ScriptedLLMClient(code_reply("fill()")),
                      events, kernel=StubKernel(), base="x", design=DESIGN, changes=[changes])
    undone = [name for name, _ in tools.calls if name == "undo_step"]
    return written, undone, events


def test_a_step_that_removes_only_the_faces_it_names_stays():
    written, undone, _ = _executed([A, B], [A], [B])

    assert undone == [] and written.for_step(1).apply_failed is None


def test_an_applied_step_reports_the_base_faces_it_changed():
    _, _, events = _executed([A, B], [A], [B])

    assert [event.data["faces_changed"] for event in events_of(events, APPLIED)] == [[B]]


def test_a_step_that_removes_a_face_it_did_not_name_is_undone_and_handed_back_with_the_list():
    written, undone, events = _executed([A, B], [], [B])

    said = written.for_step(1).apply_failed
    assert undone == ["undo_step"]
    assert [event.data["gate"] for event in events_of(events, PREFLIGHT_FAILED)] == ["collateral"]
    assert A in said and "It did not name: %s" % A in said


def test_a_face_moved_to_another_address_is_gone():
    written, undone, _ = _executed([A, B], [A, "plane z = 5"], [])

    assert undone == ["undo_step"] and B in written.for_step(1).apply_failed


def test_a_face_that_keeps_its_address_with_another_area_is_not_gone():
    written, undone, _ = _executed([A, B], [A, B], [])

    assert undone == [] and written.for_step(1).apply_failed is None


def test_an_address_the_base_part_never_had_is_no_collateral():
    written, undone, _ = _executed([A, B, "plane z = 7"], [A, B], [])

    assert undone == [] and written.for_step(1).apply_failed is None


_BASE = "The part before the edit, in mm:\nplane z = 0 (area 100 mm2)"
_MODEL = {"features": [{"id": "bore @ plane z = 0", "edit": "changed", "kind": "bore"}]}
_GONE = [{"base": [A, B], "now": [A, B]}, {"base": [A, B], "now": [A]}]


def _drive(turns, faces):
    return drive(turns, base=_BASE, faces=faces)


def test_two_collateral_rejections_with_nothing_applied_between_them_end_the_run():
    result, events, llm, tools = _drive([
        design_reply(_MODEL),
        plan_reply({"do": "Fill the bore"}), code_reply("fill()"),
        plan_reply({"do": "Fill the bore again"}), code_reply("fill_again()"),
    ], _GONE + _GONE)

    assert not result.ok and result.blocked
    assert result.steps_applied == 0
    assert [name for name, _ in tools.calls].count("undo_step") == 2
    assert "for the second time" in result.reason and "It did not name: %s" % B in result.reason
    assert B in _turns_of(llm, "plan")[1]["what_went_wrong_last_time"]


def test_one_collateral_rejection_followed_by_an_applied_step_does_not_end_the_run():
    kept = [{"base": [A, B], "now": [A, B]}, {"base": [A, B], "now": [A, B]}]
    result, events, llm, tools = _drive([
        design_reply(_MODEL),
        plan_reply({"do": "Fill the bore"}), code_reply("fill()"),
        plan_reply({"do": "Pad the rim", "changes": []}), code_reply("pad()"),
        suspect_reply(), verdict_reply("pass"),
    ], _GONE + kept)

    assert result.ok, result.reason
    assert tools.applied_codes() == ["fill()", "pad()"]
