"""
Level 1 — a run ends one of six distinct ways, and none of them is
folded into another when `core/loop.py` maps what happened to what the event
stream says.

The `stopped`-versus-`blocked` tests in `test_measure_session.py` already
prove `stopped` is not `blocked`; these extend the same proof to `aborted`
and `budget_exhausted`.
"""
from core.events import (
    ABORTED, BLOCKED, BUDGET_EXHAUSTED, COMPLETED, FAILED, STOPPED, EventStream,
)
from core.llm import ScriptedLLMClient
from core.loop.run import run
from tests.loop_helpers import FakeTools, code_reply, drive, plan_reply, suspect_reply, verdict_reply


def test_stopping_before_planning_is_recorded_as_stopped_not_aborted():
    """
    Stop lets the in-flight Step finish: `aborted` is never asked to say
    otherwise, so the default (never) applies and the run ends `stopped`.
    `cancelled()` is checked before anything is planned, so an empty
    script never has to answer.
    """
    llm = ScriptedLLMClient([])
    tools = FakeTools()
    events = EventStream()

    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                 cancelled=lambda: True)

    assert not result.ok
    assert result.blocked
    kinds = [event.kind for event in events]
    assert STOPPED in kinds
    assert ABORTED not in kinds
    assert BLOCKED not in kinds


def test_aborting_at_the_same_boundary_is_recorded_as_aborted_not_stopped():
    """
    Same `cancelled()` reaching the same `Stopped` boundary in `run` — what
    tells the two apart is the `aborted` callable alone: killing
    the kernel is the panel's job, not the loop's, so this is exercised at
    the boundary the loop actually owns.
    """
    llm = ScriptedLLMClient([])
    tools = FakeTools()
    events = EventStream()

    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                 cancelled=lambda: True, aborted=lambda: True)

    assert not result.ok
    assert result.blocked
    assert "discarded" in result.reason
    kinds = [event.kind for event in events]
    assert ABORTED in kinds
    assert STOPPED not in kinds
    assert BLOCKED not in kinds


def test_a_completed_run_emits_completed_and_nothing_else_terminal():
    """A genuine `ok=True` return is `completed`, and only that."""
    result, events, _, _ = drive(
        [plan_reply("Add a box"),
         code_reply("doc.addObject('Part::Box', 'Box')"),
         suspect_reply(), verdict_reply("pass")],
        applies=[{"status": "applied", "created": ["Box"], "description": {}}],
        views=[],
    )

    assert result.ok
    kinds = [event.kind for event in events]
    assert COMPLETED in kinds
    assert kinds.count(COMPLETED) == 1
    for other in (STOPPED, ABORTED, BLOCKED, BUDGET_EXHAUSTED, FAILED):
        assert other not in kinds


def test_a_budget_exhausted_run_is_told_apart_from_a_genuine_block():
    """
    `_OutOfRevisions` (`core/loop.py`) is the run spending its own ceiling of
    attempts, not hitting a wall nothing could get past — `_budget_exhausted`
    is the one path that reaches `BUDGET_EXHAUSTED`, and `_block` is the only
    path left that reaches `BLOCKED`.
    """
    from core.loop.run import _block, _budget_exhausted

    events = EventStream()
    result = _budget_exhausted(events, "Out of revisions.", tried=[{"step": 1}], applied=2)

    assert not result.ok
    assert result.blocked
    assert result.steps_applied == 2
    kinds = [event.kind for event in events]
    assert kinds == [BUDGET_EXHAUSTED]
    assert BLOCKED not in kinds

    other_events = EventStream()
    _block(other_events, "The Plan came back with no Steps.", [], 0)
    assert [event.kind for event in other_events] == [BLOCKED]
