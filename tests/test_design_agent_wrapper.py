"""
FCC-185 — Design as an RLM-agent: the delegation seam onto `core/agent.py ::
ask`, off by default the way Plan's own (FCC-183) is.

Design never reaches a FreeCAD API, so `run`'s namespace buys it nothing
(ADR-0019 leaves nothing for a Design turn to build with); `delegate` is the
whole of what being an agent adds here — a sub-agent per page or per sheet,
so a long drawing is read in parts. No FreeCAD, no network: every turn is
`ScriptedLLMClient`'s own and `core.agent.rlm` is mocked rather than let
spawn a real sub-agent, the same way `test_agent.py` never lets `Namespace.run`
reach a real Document.
"""
import json
from unittest.mock import Mock

from core import agent
from core.events import DESIGN, THOUGHT, EventStream
from core.llm import ScriptedLLMClient
from core.loop import Budgets, PhaseError, design
from core.tools import ToolRegistry


def _budgets(**over):
    return Budgets(**{"agent_turns": 5, "looks_per_turn": 6, **over})


def test_design_delegates_a_page_to_rlm_and_says_so_on_the_stream(monkeypatch):
    """
    Design asks to delegate page 2 instead of answering outright, `rlm` is
    called with that page named, and the stream carries a `THOUGHT` about the
    delegation before Design's own final object.
    """
    events = EventStream()
    llm = ScriptedLLMClient([
        '{"delegate": {"page": "page 2"}}',
        '{"design": {"envelope": {"length": {"value": "80 mm", "flag": "read"}}},'
        ' "question": ""}',
    ])
    spawned = Mock(return_value={"answer": "read off page 2"})
    monkeypatch.setattr(agent, "rlm", spawned)

    the_part, question = design({"intent": "Build the bracket"}, llm, events,
                                agentic=True, tools=ToolRegistry(), budgets=_budgets())

    assert the_part == {"envelope": {"length": {"value": "80 mm", "flag": "read"}}}
    assert question == ""

    assert spawned.call_count == 1
    called_with = spawned.call_args.args
    assert any("page 2" in str(argument) for argument in called_with)

    thoughts = [event.data["text"] for event in events.of(THOUGHT)]
    assert any("delegat" in text and "page 2" in text for text in thoughts)


def test_a_delegated_page_that_fails_does_not_lose_the_rest_of_the_reading(monkeypatch):
    """
    `rlm` raising `PhaseError` — a sub-agent that spent its own turns without
    answering — costs the delegated page, not the whole turn: Design reads the
    failure back as this turn's result and still reaches its own final object
    with whatever else it already knew, the same graceful-degradation contract
    a failed `look` already has (`core/agent.py :: _take_a_look`).
    """
    events = EventStream()
    llm = ScriptedLLMClient([
        '{"delegate": {"page": "page 3"}}',
        '{"design": {"envelope": {"length": {"value": "80 mm", "flag": "read"}}},'
        ' "question": ""}',
    ])
    monkeypatch.setattr(agent, "rlm", Mock(side_effect=PhaseError("out of turns")))

    the_part, question = design({"intent": "Build the bracket"}, llm, events,
                                agentic=True, tools=ToolRegistry(), budgets=_budgets())

    assert the_part == {"envelope": {"length": {"value": "80 mm", "flag": "read"}}}
    assert question == ""


def test_a_design_turn_with_no_delegation_produces_the_same_document_shape():
    """
    The non-agentic and the agentic route hand `design()`'s own caller back
    the same `(the_part, question)` shape when nothing was delegated — turning
    the seam on changes nothing for a turn that never uses it, the way Plan's
    own agentic seam changes nothing for a turn that never looks or runs code.
    """
    model = {"envelope": {"length": {"value": "80 mm", "flag": "read"}}}
    plain_events, agentic_events = EventStream(), EventStream()

    reply = json.dumps({"design": model, "question": ""})
    plain = design({"intent": "Build the bracket"}, ScriptedLLMClient([reply]),
                   plain_events)
    agentic = design({"intent": "Build the bracket"}, ScriptedLLMClient([reply]),
                     agentic_events, agentic=True, tools=ToolRegistry(),
                     budgets=_budgets())

    assert plain == agentic == (model, "")
