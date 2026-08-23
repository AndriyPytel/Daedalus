"""
The trap action — a fourth `core/agent.py :: ask` action, `{"trap":
{"description": "...", "evidence": "..."}}`, closing the one Acceptance
Criterion FCC-185 could not honour once implemented: FCC-184 was not yet in
that branch's ancestry, so Design could not propose a trap it met on the
sheet.

Not hard-coded into `core/loop.py :: design` alone: a trap proposal is the
model's own judgement call, so it belongs beside `look`, `run` and
`delegate` in `ask` itself, wired through wherever a phase already has a
`TrapStore` open (FCC-184) — Design, Plan, Execute and Assertion alike, one
action instead of four call sites (FCC-191's trap-proposal-rate metric).

No FreeCAD anywhere in this file: `ScriptedLLMClient` (`core/llm.py`) stands
in for the provider, and `tests.test_loop.FakeTools` stands in for the
Document where a phase function needs one.
"""
import json

from core import loop
from core.agent import ask
from core.events import EventStream, TRAP_PROPOSED
from core.freecad_tools import TRAPS
from core.llm import ScriptedLLMClient
from core.loop import Budgets
from core.tools import ToolRegistry
from core.traps import TrapStorage, TrapStore
from tests.test_loop import FakeTools


def _budgets(**over):
    return Budgets(**{"agent_turns": 5, "looks_per_turn": 6, **over})


def test_a_trap_action_with_no_store_open_is_refused_and_the_turn_carries_on():
    llm = ScriptedLLMClient([
        json.dumps({"trap": {"description": "x", "evidence": "y"}}),
        json.dumps({"done": True}),
    ])
    events = EventStream()

    reply = ask(llm, events, "design", "system", "user", ToolRegistry(), _budgets())

    assert reply == {"done": True}
    assert events.of(TRAP_PROPOSED) == []


def test_a_trap_proposal_with_a_store_reaches_propose_trap_under_asks_own_phase(tmp_path):
    events = EventStream()
    store = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")),
                      events=events)
    llm = ScriptedLLMClient([
        json.dumps({"trap": {
            "description": "Draft cube() twice loses the second body silently",
            "evidence": "Step 2 log: two Part::Box named Box, only one visible",
        }}),
        json.dumps({"done": True}),
    ])

    reply = ask(llm, events, "execute", "system", "user", ToolRegistry(), _budgets(),
               traps=store)

    assert reply == {"done": True}
    proposed = events.of(TRAP_PROPOSED)
    assert len(proposed) == 1
    assert proposed[0].data["phase"] == "execute"
    pending = store.pending_proposals()
    assert len(pending) == 1
    assert pending[0]["description"].startswith("Draft cube() twice")


def test_a_duplicate_trap_proposal_comes_back_as_an_error_not_a_crash(tmp_path):
    store = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")))
    events = EventStream()
    llm = ScriptedLLMClient([
        json.dumps({"trap": {"description": TRAPS[0], "evidence": "seen again"}}),
        json.dumps({"done": True}),
    ])

    reply = ask(llm, events, "design", "system", "user", ToolRegistry(), _budgets(),
               traps=store)

    assert reply == {"done": True}
    assert store.pending_proposals() == []


def test_a_trap_proposed_by_a_delegated_sub_agent_lands_in_the_same_store(tmp_path):
    events = EventStream()
    store = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")),
                      events=events)
    llm = ScriptedLLMClient([
        json.dumps({"delegate": {"page": "a note on the second box"}}),
        json.dumps({"trap": {
            "description": "Draft cube() twice loses the second body silently",
            "evidence": "Step 2 log: two Part::Box named Box, only one visible",
        }}),
        json.dumps({"done": True}),
        json.dumps({"done": True}),
    ])

    reply = ask(llm, events, "execute", "system", "user", ToolRegistry(), _budgets(), traps=store)

    assert reply == {"done": True}
    pending = store.pending_proposals()
    assert len(pending) == 1
    assert pending[0]["phase"] == "execute"
    assert pending[0]["description"].startswith("Draft cube() twice")


def test_a_delegated_duplicate_of_a_seed_trap_comes_back_as_an_error_not_a_crash(tmp_path):
    store = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")))
    events = EventStream()
    llm = ScriptedLLMClient([
        json.dumps({"delegate": {"page": "a note"}}),
        json.dumps({"trap": {"description": TRAPS[0], "evidence": "seen again"}}),
        json.dumps({"done": True}),
        json.dumps({"done": True}),
    ])

    reply = ask(llm, events, "design", "system", "user", ToolRegistry(), _budgets(), traps=store)

    assert reply == {"done": True}
    assert store.pending_proposals() == []


def _watched_ask(captured):
    def fake_ask(llm, events, phase, system, user, tools, budgets, **kwargs):
        captured.update(kwargs)
        return {"design": {}, "question": ""}
    return fake_ask


def test_design_forwards_its_trap_store_to_the_agent_seam(monkeypatch):
    from core import agent as agent_module

    captured = {}
    monkeypatch.setattr(agent_module, "ask", _watched_ask(captured))
    store = object()

    loop.design({"intent": "a bracket"}, ScriptedLLMClient([]), EventStream(),
               agentic=True, tools=ToolRegistry(), budgets=_budgets(), traps=store)

    assert captured.get("traps") is store


def test_plan_forwards_its_trap_store_to_the_agent_seam(monkeypatch):
    from core import agent as agent_module

    captured = {}
    monkeypatch.setattr(agent_module, "ask", lambda llm, events, phase, system, user, tools,
                        budgets, **kwargs: (captured.update(kwargs) or {"steps": ["Add a box"]}))
    store = object()

    loop.plan("a bracket", {}, ScriptedLLMClient([]), EventStream(),
             agentic=True, tools=ToolRegistry(), budgets=_budgets(), traps=store)

    assert captured.get("traps") is store


def test_execute_forwards_its_trap_store_to_the_agent_seam(monkeypatch):
    from core import agent as agent_module

    captured = {}
    monkeypatch.setattr(agent_module, "ask", lambda llm, events, phase, system, user, tools,
                        budgets, **kwargs: (captured.update(kwargs) or {}))
    store = object()
    tools_double = FakeTools()

    loop.execute(["Add a box"], tools_double.registry, ScriptedLLMClient([]), EventStream(),
                agentic=True, budgets=_budgets(), traps=store)

    assert captured.get("traps") is store


def test_assertion_forwards_its_trap_store_to_the_agent_seam(monkeypatch):
    from core import agent as agent_module

    captured = {}
    monkeypatch.setattr(agent_module, "ask", lambda llm, events, phase, system, user, tools,
                        budgets, **kwargs: (captured.update(kwargs)
                                            or {"code": "assert True", "uncovered": []}))
    store = object()
    tools_double = FakeTools()

    loop.assertion(["it holds"], "described", tools_double.registry, ScriptedLLMClient([]),
                  EventStream(), budgets=_budgets(), agentic=True, traps=store)

    assert captured.get("traps") is store
