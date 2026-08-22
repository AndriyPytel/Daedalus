"""
Level 1 — `core/agent.py`, the bounded multi-turn loop over one phase call
(FCC-183).

Written the way the rest of Level 1 is: a `ScriptedLLMClient` and a small
`ToolRegistry`, no FreeCAD and no subprocess anywhere in the call.
"""
import inspect

import pytest

from core import agent, loop
from core.agent import Namespace, ask
from core.events import COST, EventStream, TOOL_CALL, TOOL_RESULT
from core.llm import ScriptedLLMClient
from core.loop import Budgets, PhaseError
from core.tools import ToolError, ToolRegistry


def _tools(measured=None):
    registry = ToolRegistry()
    calls = []

    @registry.tool("measure", "A read-only look.", {
        "type": "object", "properties": {},
    })
    def measure():
        calls.append(("measure", {}))
        return dict(measured or {"length_mm": 12.5})

    @registry.tool("apply_step", "Writes to the Document; not a look.", {
        "type": "object", "properties": {"code": {"type": "string"}},
        "required": ["code"],
    })
    def apply_step(code):
        calls.append(("apply_step", {"code": code}))
        return {"status": "applied"}

    registry.calls = calls
    return registry


def _budgets(**over):
    return Budgets(**{"agent_turns": 5, "looks_per_turn": 6, **over})


def test_names_bound_by_one_action_are_still_bound_in_the_next_action_of_the_same_phase_invocation():
    llm = ScriptedLLMClient([
        '{"run": "x = 41"}',
        '{"run": "print(x + 1)"}',
        '{"done": true}',
    ])
    events = EventStream()

    reply = ask(llm, events, "plan", "system", "user", _tools(), _budgets())

    assert reply == {"done": True}
    outputs = [event.data["result"]["output"] for event in events.of(TOOL_RESULT)
              if event.data["name"] == "run"]
    assert outputs == ["", "42\n"]


def test_a_tool_called_through_an_action_returns_exactly_what_toolregistry_call_returns_directly():
    tools = _tools(measured={"length_mm": 99.25})
    llm = ScriptedLLMClient([
        '{"look": {"tool": "measure", "arguments": {}}}',
        '{"done": true}',
    ])
    events = EventStream()

    ask(llm, events, "plan", "system", "user", tools, _budgets())

    from_the_action = events.of(TOOL_RESULT)[0].data["result"]
    assert from_the_action == tools.call("measure", {})


def test_each_action_emits_a_tool_call_event_in_order_and_the_phases_final_object_comes_back_unchanged():
    tools = _tools()
    llm = ScriptedLLMClient([
        '{"look": {"tool": "measure", "arguments": {}}}',
        '{"run": "1 + 1"}',
        '{"ok": true, "extra": 1}',
    ])
    events = EventStream()

    reply = ask(llm, events, "plan", "system", "user", tools, _budgets())

    calls = events.of(TOOL_CALL)
    assert [event.data["name"] for event in calls] == ["measure", "run"]
    assert reply == {"ok": True, "extra": 1}


def test_a_look_through_an_action_and_a_look_through_the_caller_level_loop_draw_on_one_looks_per_turn_allowance():
    budgets = _budgets(looks_per_turn=1)
    tools = _tools()
    events = EventStream()

    result, taken = agent._take_a_look(
        tools, {"tool": "measure", "arguments": {}}, events, 0, budgets)
    assert taken == 1
    refused, taken = agent._take_a_look(
        tools, {"tool": "measure", "arguments": {}}, events, taken, budgets)

    assert taken == budgets.looks_per_turn == 1
    assert "budget" in refused["error"]
    assert "budgets.looks_per_turn" in inspect.getsource(loop._plan_once_it_knows_enough)
    assert "budgets.looks_per_turn" in inspect.getsource(agent._take_a_look)


def test_a_reply_carrying_no_object_is_asked_again_once_exactly_as_ask_does_and_the_second_reply_is_accepted():
    llm = ScriptedLLMClient([
        "I need to check the Hole feature's properties first.",
        '{"done": true}',
    ])
    events = EventStream()

    reply = ask(llm, events, "plan", "system", "user", _tools(), _budgets(agent_turns=1))

    assert reply == {"done": True}
    assert len(llm.calls) == 2
    assert llm.calls[0][0]["content"] == "system"
    assert llm.calls[1][0]["content"] == "system"


def test_a_phase_error_raised_inside_an_action_reaches_the_caller_with_its_own_message():
    llm = ScriptedLLMClient([
        '{"run": "raise PhaseError(\'the sketch has no closed profile\')"}',
    ])
    events = EventStream()

    with pytest.raises(PhaseError, match="the sketch has no closed profile"):
        ask(llm, events, "plan", "system", "user", _tools(), _budgets())


def test_the_turn_ceiling_is_enforced_and_raises_phaseerror_naming_what_the_agent_was_still_doing():
    llm = ScriptedLLMClient([
        '{"look": {"tool": "measure", "arguments": {}}}',
    ])
    events = EventStream()

    with pytest.raises(PhaseError, match="looking with 'measure'"):
        ask(llm, events, "plan", "system", "user", _tools(), _budgets(agent_turns=1))


def test_the_agent_spawns_no_subprocess(monkeypatch):
    import subprocess

    def refuses(*args, **kwargs):
        raise AssertionError("ask() must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "Popen", refuses)
    llm = ScriptedLLMClient([
        '{"look": {"tool": "measure", "arguments": {}}}',
        '{"run": "2 + 2"}',
        '{"done": true}',
    ])
    events = EventStream()

    reply = ask(llm, events, "plan", "system", "user", _tools(), _budgets())

    assert reply == {"done": True}


def test_budgets_requires_agent_turns_to_be_read_rather_than_invented_by_ask():
    assert Budgets().agent_turns == 10
    with pytest.raises(TypeError):
        ask(None, EventStream(), "plan", "system", "user", _tools())
