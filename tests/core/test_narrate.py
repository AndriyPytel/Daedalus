"""
Level 1 — what an event says to a person.

It is plain Python, so this runs with no FreeCAD and no Qt. SAMPLES is one
plausible event per narrated kind; the ANSWER sample uses `by: "drawing"`
because `by: "user"` is the one answer that says nothing, and a sample that
says nothing would not prove the kind is covered.
"""
from core.events import (
    ANSWER, APPLIED, ATTACHED, BUDGET_EXHAUSTED, COMPLETED, COST, DOMAIN, ERROR, FAILED,
    INTENT, PREFLIGHT_FAILED, QUESTION, REGISTRY, ROUTED, THOUGHT, TOOL_CALL, TOOL_RESULT,
    WARNING, Event, Narration,
)
from core.narrate import narrate

SAMPLES = {
    INTENT: {"text": "Build the pipe clamp on this sheet."},
    QUESTION: {"text": "How many teeth?"},
    ANSWER: {"text": "the pockets go through", "by": "drawing"},
    ATTACHED: {"files": [{"name": "sheet.pdf", "path": "/tmp/sheet.pdf"}]},
    TOOL_RESULT: {"name": "apply_step", "result": {"status": "applied"}},
    PREFLIGHT_FAILED: {"step": 1, "attempt": 2, "traceback": "AttributeError: Support",
                       "gate": None},
    APPLIED: {"step": 1, "label": "Base", "created": ["Pad"]},
    BUDGET_EXHAUSTED: {"reason": "Out of revisions.", "tried": []},
    FAILED: {"message": "ValueError: no key"},
    COMPLETED: {"summary": "done"},
    ERROR: {"message": "ValueError: no key"},
    WARNING: {"message": "the table lost unusable fields: through_count"},
    ROUTED: {"route": "design"},
    DOMAIN: {"domain": "assembly", "inferred": True},
}


def an_event(kind):
    return Event(kind=kind, data=dict(SAMPLES[kind]))


def test_there_is_a_sample_for_every_narrated_kind_and_it_carries_only_its_keys():
    narrated = {name for name, kind in REGISTRY.items() if kind.narration}

    assert set(SAMPLES) == narrated
    for name, data in SAMPLES.items():
        assert set(data) <= set(REGISTRY[name].keys), name


def test_every_sample_says_something():
    for kind in SAMPLES:
        assert narrate(an_event(kind)) != Narration(), kind


def test_what_a_call_cost_is_not_narrated():
    """
    The one kind that is written and not shown. A number per call belongs in
    the session log, which is permanent; narrating it would put a price beside
    every phase of a run the user is not being charged for.
    """
    data = {"phase": "agent", "usd": 0.0049255, "model": "sonnet", "seconds": 12.3}

    assert narrate(Event(kind=COST, data=data)) == Narration()


def test_the_shapes_that_are_not_prose_say_nothing():
    """
    A tool call is its code and reasoning is a stream. The renderer draws
    those itself, so the narrator must not also word them — or they would
    appear twice.
    """
    for kind in (TOOL_CALL, THOUGHT):
        data = {"name": "code", "arguments": {}, "phase": "agent", "text": "hmm"}
        assert narrate(Event(kind=kind, data=data)) == Narration()


def test_what_the_run_was_asked_for_is_a_line_of_its_own():
    """
    Given the way the part is given, a line naming it. The run is judged
    against what was asked, and a transcript that never says it cannot be
    judged at all.
    """
    assert narrate(an_event(INTENT)).line == \
        "What was asked for: Build the pipe clamp on this sheet."


def test_the_users_own_answer_is_not_repeated_back_at_them():
    """It is already in the transcript; saying it again is the panel talking."""
    assert narrate(Event(kind=ANSWER, data={"text": "20 teeth", "by": "user"})) == Narration()


def test_an_answer_off_the_drawing_says_so():
    """
    A value read off the sheet is read, not assumed, so it may not fall through
    to "No values given".
    """
    assert narrate(an_event(ANSWER)).line == \
        "Looked at the drawing again — the pockets go through"


def test_nobody_answering_says_the_plan_will_assume():
    event = Event(kind=ANSWER, data={"text": "", "by": None})

    assert narrate(event).line == "No values given, so the Plan will say what it assumed."


def test_running_out_of_budget_carries_both_the_reason_and_what_was_tried():
    """The two halves are a line and a folded row, which is why both exist."""
    event = Event(kind=BUDGET_EXHAUSTED, data={
        "reason": "Execute could not build it.",
        "tried": [{"code": "pad()", "traceback": "AttributeError"},
                  {"review": "the flats are wrong"}],
    })
    said = narrate(event)

    assert said.line == "⏸  Execute could not build it."
    assert said.title == "What was tried (2)"
    assert "pad()" in said.body and "AttributeError" in said.body
    assert "the flats are wrong" in said.body


def test_running_out_of_budget_with_nothing_tried_has_no_row_to_fold():
    assert not narrate(an_event(BUDGET_EXHAUSTED)).title


def test_progress_replaces_itself_and_leaves_nothing_permanent():
    """A tool result is status, not transcript."""
    said = narrate(an_event(TOOL_RESULT))

    assert said.status
    assert not said.line and not said.title


def test_an_unknown_kind_says_nothing_rather_than_raising():
    """A renderer that dies takes the run's narration with it."""
    assert narrate(Event(kind="something_new", data={})) == Narration()
