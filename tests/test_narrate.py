"""
Level 1 — what an event says to a person.

The wording used to live inside a `QDockWidget`, which is why the second
narrator could drift for months without anything failing. It is plain Python
now, so this runs with no FreeCAD and no Qt.
"""
import pytest

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, ERROR, KINDS, PLAN,
    DESIGN, PLAN_REVISED, PREFLIGHT_FAILED, QUESTION, REVIEW_VERDICT,
    STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, Event,
)
from core.narrate import Narration, narrate

# One plausible event per kind. Shared with the panel's own check, so a kind
# added to the stream and forgotten in both narrators is caught by name here.
SAMPLES = {
    THOUGHT: {"phase": "plan", "text": "thinking"},
    QUESTION: {"text": "How many teeth?"},
    # Deliberately not `by: "user"` — that is the one answer that says nothing,
    # and a sample that says nothing would not prove the kind is covered.
    ANSWER: {"text": "the pockets go through", "by": "drawing"},
    ATTACHED: {"files": [{"name": "sheet.pdf", "path": "/tmp/sheet.pdf"}]},
    DESIGN: {"model": {"envelope": {"length": {"value": "40 mm", "flag": "read"}}}},
    PLAN: {"steps": ["Sketch the base"], "acceptance": []},
    PLAN_REVISED: {"steps": ["Sketch the base"], "reason": "the pocket was too deep"},
    STEP_STARTED: {"index": 1, "total": 3, "description": "Sketch the base"},
    TOOL_CALL: {"name": "apply_step", "arguments": {"code": "pass", "label": "Base"}},
    TOOL_RESULT: {"name": "apply_step", "result": {"status": "applied"}},
    PREFLIGHT_FAILED: {"step": 1, "attempt": 2, "traceback": "AttributeError: Support"},
    APPLIED: {"step": 1, "label": "Base", "created": ["Pad"]},
    ASSERTION: {"ok": True, "confirmed": ["6.5 mm tall"], "uncovered": []},
    REVIEW_VERDICT: {"verdict": "pass", "reason": "matches the Intent"},
    BLOCKED: {"reason": "The budget is spent.", "tried": []},
    ERROR: {"message": "ValueError: no key"},
}


def an_event(kind):
    return Event(kind=kind, data=dict(SAMPLES[kind]))


def test_there_is_a_sample_for_every_kind():
    """The samples below are only worth something if they cover the stream."""
    assert set(SAMPLES) == set(KINDS)


@pytest.mark.parametrize("kind", [PLAN, TOOL_CALL, THOUGHT])
def test_the_shapes_that_are_not_prose_say_nothing(kind):
    """
    A Plan is its Steps, a tool call is its code, and reasoning is a stream.
    Each narrator renders those itself, so the narrator must not also word them
    — or they would appear twice.
    """
    assert narrate(an_event(kind)) == Narration()


def test_the_part_is_a_row_that_carries_the_structure_laid_out():
    """
    Laid out rather than dumped: the Gate is where a misreading is cheap to
    correct, and JSON is not read. FCC-93.
    """
    said = narrate(an_event(DESIGN))

    assert said.title == "What the part is"
    assert said.body == "Envelope\n  length: 40 mm (read)"
    assert not said.line, "the description is the row, not a line above it"


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
