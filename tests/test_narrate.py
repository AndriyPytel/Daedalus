"""
Level 1 — what an event says to a person.

The wording used to live inside a `QDockWidget`, which is why the second
narrator could drift for months without anything failing. It is plain Python
now, so this runs with no FreeCAD and no Qt.

SAMPLES is one plausible event per kind, shared with the panel's own check
so that a kind added to the stream and forgotten in both narrators is caught
by name. The ANSWER sample deliberately uses `by: "drawing"` rather than
`by: "user"` — the latter is the one answer that says nothing, and a sample
that says nothing would not prove the kind is covered. The GATE sample carries
the answered Gate rather than the waiting one, because a sample has to carry
the field the wording turns on.
"""
import pytest

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, CALL_STARTED, COST, ERROR,
    GATE, INTENT, KINDS, OUTSTANDING, PART, PLAN, DESIGN, PLAN_REVISED,
    PREFLIGHT_FAILED, QUESTION, REVIEW_VERDICT, STEP_STARTED, THOUGHT,
    TOOL_CALL, TOOL_RESULT, TRAP_APPROVED, TRAP_DECLINED, TRAP_PROPOSED,
    UNBUILT, Event,
)
from core.narrate import Narration, narrate

SAMPLES = {
    INTENT: {"text": "Build the pipe clamp on this sheet."},
    THOUGHT: {"phase": "plan", "text": "thinking"},
    QUESTION: {"text": "How many teeth?"},
    ANSWER: {"text": "the pockets go through", "by": "drawing"},
    ATTACHED: {"files": [{"name": "sheet.pdf", "path": "/tmp/sheet.pdf"}]},
    DESIGN: {"model": {"envelope": {"length": {"value": "40 mm", "flag": "read"}}}},
    PART: {"path": "/tmp/parts/model-mania 2023-c8a24f60-20260812-134925-000001.yaml"},
    PLAN: {"steps": ["Sketch the base"], "acceptance": []},
    PLAN_REVISED: {"steps": ["Sketch the base"], "reason": "the pocket was too deep",
                   "revision": 2, "of": 3, "because": "Execute could not build a Step"},
    GATE: {"decision": "approve"},
    STEP_STARTED: {"index": 1, "total": 3, "description": "Sketch the base"},
    TOOL_CALL: {"name": "apply_step", "arguments": {"code": "pass", "label": "Base"}},
    TOOL_RESULT: {"name": "apply_step", "result": {"status": "applied"}},
    PREFLIGHT_FAILED: {"step": 1, "attempt": 2, "traceback": "AttributeError: Support"},
    APPLIED: {"step": 1, "label": "Base", "created": ["Pad"]},
    ASSERTION: {"ok": True, "confirmed": ["6.5 mm tall"], "uncovered": []},
    OUTSTANDING: {"entries": ["envelope → across the flats: 13.0 mm (read)"]},
    UNBUILT: {"step": 8, "total": 8, "description": "Assign plain carbon steel",
              "why": "no card in this build carries it",
              "entries": ["material: plain carbon steel (read)"]},
    REVIEW_VERDICT: {"verdict": "pass", "reason": "matches the Intent"},
    BLOCKED: {"reason": "The budget is spent.", "tried": []},
    CALL_STARTED: {"phase": "plan", "model": "sonnet", "ceiling": 900.0},
    COST: {"phase": "design", "usd": 0.0049255, "model": "sonnet", "seconds": 12.3},
    ERROR: {"message": "ValueError: no key"},
    TRAP_PROPOSED: {"id": "620ede8b", "description": "A Sweep along a datum line "
                    "loses its Twist setting on recompute",
                    "evidence": "Step 4: Sweep feature Touched after Twist was set "
                    "to 90 deg", "phase": "execute"},
    TRAP_APPROVED: {"id": "620ede8b", "description": "A Sweep along a datum line "
                    "loses its Twist setting on recompute", "phase": "execute"},
    TRAP_DECLINED: {"id": "60eea80f", "description": "A Draft Mirror across a "
                    "non-planar face silently produces no shape", "phase": "review"},
}


def an_event(kind):
    return Event(kind=kind, data=dict(SAMPLES[kind]))


def test_there_is_a_sample_for_every_kind():
    """The samples below are only worth something if they cover the stream."""
    assert set(SAMPLES) == set(KINDS)


def test_what_a_call_cost_is_not_narrated():
    """
    The one kind that is written and not shown. A number per call belongs in
    the session log, which is permanent; narrating it would put a price beside
    every phase of a run the user is not being charged for.
    """
    assert narrate(an_event(COST)) == Narration()


@pytest.mark.parametrize("kind", [TRAP_PROPOSED, TRAP_APPROVED, TRAP_DECLINED])
def test_a_trap_event_is_written_and_not_narrated(kind):
    """
    A proposal is for the human reviewing it and the ticket comment FCC-191
    reads the counts from, not for the transcript of the run that raised it.
    """
    assert narrate(an_event(kind)) == Narration()


@pytest.mark.parametrize("kind", [PLAN, TOOL_CALL, THOUGHT])
def test_the_shapes_that_are_not_prose_say_nothing(kind):
    """
    A Plan is its Steps, a tool call is its code, and reasoning is a stream.
    Each narrator renders those itself, so the narrator must not also word them
    — or they would appear twice.
    """
    assert narrate(an_event(kind)) == Narration()


def test_what_the_run_was_asked_for_is_a_line_of_its_own():
    """
    Given the way the part is given, a line naming it. The run is judged
    against what was asked, and a transcript that never says it cannot be
    judged at all.
    """
    assert narrate(an_event(INTENT)).line == \
        "What was asked for: Build the pipe clamp on this sheet."


def test_the_description_is_not_said_and_the_file_it_went_to_is():
    """
    The description is a document, and a document is opened rather than said:
    what the transcript carries is the path it was written to, which is the
    file a misreading is corrected in and the run is planned from (FCC-108).
    """
    assert narrate(an_event(DESIGN)) == Narration()
    assert narrate(an_event(PART)).line == \
        "The part as read: " + SAMPLES[PART]["path"]


def test_the_users_own_answer_is_not_repeated_back_at_them():
    """It is already in the transcript; saying it again is the panel talking."""
    assert narrate(Event(kind=ANSWER, data={"text": "20 teeth", "by": "user"})) == Narration()


def test_an_answer_off_the_drawing_says_so():
    """
    A value read off the sheet is read, not assumed, so it may not fall through
    to "No values given" (FCC-90).
    """
    event = Event(kind=ANSWER, data={"text": "the pockets go through", "by": "drawing"})

    assert narrate(event).line == "Looked at the drawing again — the pockets go through"


def test_nobody_answering_says_the_plan_will_assume():
    event = Event(kind=ANSWER, data={"text": "", "by": None})

    assert narrate(event).line == "No values given, so the Plan will say what it assumed."


def test_a_block_carries_both_the_reason_and_what_was_tried():
    """The two halves are a line and a folded row, which is why both exist."""
    event = Event(kind=BLOCKED, data={
        "reason": "Execute could not build it.",
        "tried": [{"code": "pad()", "traceback": "AttributeError"},
                  {"review": "the flats are wrong"}],
    })
    said = narrate(event)

    assert said.line == "⏸  Execute could not build it."
    assert said.title == "What was tried (2)"
    assert "pad()" in said.body and "AttributeError" in said.body
    assert "the flats are wrong" in said.body


def test_a_block_with_nothing_tried_has_no_row_to_fold():
    assert not narrate(an_event(BLOCKED)).title


def test_the_acceptance_checks_have_three_outcomes_not_two():
    """
    `ok` is None when the checks could not be written at all — a fact about the
    checking code, not a verdict on the part.
    """
    unwritten = Event(kind=ASSERTION, data={"ok": None})
    failed = Event(kind=ASSERTION, data={"ok": False, "traceback": "assert 6.5 == 6.0"})
    passed = Event(kind=ASSERTION, data={"ok": True, "confirmed": ["a", "b"],
                                         "uncovered": ["the tooth flank"]})

    assert narrate(unwritten).line.startswith("⚠")
    assert narrate(failed).line.startswith("✗")
    assert "assert 6.5 == 6.0" in narrate(failed).line
    assert narrate(passed).line == ("✓ Acceptance checks passed: 2 confirmed. "
                                    "Left to Review: the tooth flank.")


def test_progress_replaces_itself_and_leaves_nothing_permanent():
    """A Step and a tool result are status, not transcript."""
    for kind in (STEP_STARTED, TOOL_RESULT):
        said = narrate(an_event(kind))
        assert said.status
        assert not said.line and not said.title


def test_an_unknown_kind_says_nothing_rather_than_raising():
    """A renderer that dies takes the run's narration with it."""
    assert narrate(Event(kind="something_new", data={})) == Narration()
