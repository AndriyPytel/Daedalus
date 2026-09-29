"""
A declaration forced by `budget_last_call.md` is not a verdict on whether a
Step can be built -- it is the reply `core/agent/agent.py`'s spent-budget
branch demands, carrying whatever the model said, and Execute must tell it
apart from a genuine handback so `going_nowhere` never counts it as one.
"""
import json

from core.agent.subagent import SessionTreeSupervisor
from core.events import BLOCKED, BUDGET_EXHAUSTED, EventStream
from core.llm import ScriptedLLMClient
from core.loop.execute import execute
import importlib
from core.loop.run import run

run_module = importlib.import_module("core.loop.run")
from core.loop.budgets import Budgets
from tests.loop_helpers import FakeTools, StubKernel, design_reply, plan_reply


def test_a_declaration_forced_by_a_spent_turn_budget_is_flagged():
    """AC1's seam: `Written.forced` tells Execute's own caller a declare came
    out of `budget_last_call`, not a real verdict on the Step."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "print(1)", "step": 1}),
        json.dumps({"declare": {
            "unbuildable": "Step 2 of 14 could not be built as written"}}),
    ])
    supervisor = SessionTreeSupervisor(turn_budget=0)
    tools = FakeTools()

    written = execute(["Pad a rib"], tools.registry, llm, EventStream(),
                       kernel=StubKernel(), supervisor=supervisor)

    assert written.forced is True
    assert written.unbuildable == "Step 2 of 14 could not be built as written"


def test_an_ordinary_declaration_is_not_flagged():
    """The same shape, undeclared under budget pressure, stays unforced."""
    llm = ScriptedLLMClient([
        json.dumps({"declare": {
            "unbuildable": "the rib round cannot be swept this way"}}),
    ])

    written = execute(["Round the rib"], FakeTools().registry, llm, EventStream(),
                       kernel=StubKernel())

    assert written.forced is False
    assert written.unbuildable == "the rib round cannot be swept this way"


def _forced_handback(*_args, **_kwargs):
    from core.loop.execute import Written
    return Written(unbuildable="Step 1 of 1 could not be built as written: out of turns",
                    forced=True)


def test_a_forced_handback_never_reaches_the_going_nowhere_gate(monkeypatch):
    """AC1 end to end: `run()` never emits `going_nowhere`'s BLOCKED text for
    a forced declaration, even on the very first pass -- it is excluded from
    the gate's bookkeeping altogether, not merely spared a second time."""
    monkeypatch.setattr(run_module, "execute", _forced_handback)
    llm = ScriptedLLMClient([design_reply(), plan_reply("Pad the rib")])
    events = EventStream()

    result = run("Pad a rib", llm=llm, tools=FakeTools().registry, events=events,
                 kernel=StubKernel())

    assert result.ok is False
    assert "could not be built as written" not in result.reason
    kinds = [event.kind for event in events]
    assert BUDGET_EXHAUSTED in kinds
    assert BLOCKED not in kinds


def test_a_forced_handback_names_the_budget_as_its_terminal_cause(monkeypatch):
    """AC2: the reason a forced declaration ends a run on names the spent
    turn budget, not the Step's own buildability."""
    monkeypatch.setattr(run_module, "execute", _forced_handback)
    llm = ScriptedLLMClient([design_reply(), plan_reply("Pad the rib")])

    result = run("Pad a rib", llm=llm, tools=FakeTools().registry, events=EventStream(),
                 kernel=StubKernel())

    assert "turn budget" in result.reason


def test_a_forced_declaration_carries_how_many_times_the_step_was_rehearsed():
    """
    AC2's seam: `Written.rehearsals` counts every declared-but-rejected
    attempt at a Step, so the caller can tell a Step that was tried and
    tried again apart from one nobody ever wrote code for.
    """
    from tests.loop_helpers import FakeTools, failed

    llm = ScriptedLLMClient([
        json.dumps({"code": "try1()", "step": 1, "label": "Step"}),
        json.dumps({"code": "try2()", "step": 1, "label": "Step"}),
        json.dumps({"code": "try3()", "step": 1, "label": "Step"}),
        json.dumps({"declare": {"unbuildable": "out of turns"}}),
    ])
    supervisor = SessionTreeSupervisor(turn_budget=6)
    tools = FakeTools(applies=[failed("one"), failed("two")])

    written = execute(["Pad a rib"], tools.registry, llm, EventStream(),
                      kernel=StubKernel(), supervisor=supervisor)

    assert written.forced is True
    assert written.rehearsals.get(1) == 2


def _forced_handback_with_rehearsals(*_args, **_kwargs):
    from core.loop.execute import Written
    return Written(unbuildable="Step 1 of 1 could not be built as written: out of turns",
                  forced=True, rehearsals={1: 3})


def test_a_forced_handback_reports_the_rehearsals_it_already_tried(monkeypatch):
    """AC2: a Step rehearsed before the turn budget ran out must say how
    many times, not that it 'never reached a build attempt'."""
    monkeypatch.setattr(run_module, "execute", _forced_handback_with_rehearsals)
    llm = ScriptedLLMClient([design_reply(), plan_reply("Pad the rib")])

    result = run("Pad a rib", llm=llm, tools=FakeTools().registry, events=EventStream(),
                 kernel=StubKernel())

    assert "never reached a build attempt" not in result.reason
    assert "3" in result.reason
    assert "turn budget" in result.reason
