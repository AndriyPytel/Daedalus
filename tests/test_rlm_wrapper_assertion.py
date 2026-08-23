"""
Assertion as an RLM-agent (FCC-189): `check_assertions` reached through
`core/agent.py :: ask` instead of `_ask`, so a retry may `delegate` or
`trap` the way Plan and Execute already could.

`assertion()` already writes and runs executable checks through
`check_assertions` (FCC-168/FCC-170); wrapping it as an agent does not add a
second way to touch the Document — `run` is refused outright
(`core/loop.py :: NOT_ARMED_FOR_RUN`, BRIEFING Question 5): Assertion is
read-only by construction, and `check_assertions` is already the one path
its checks run through. `agentic=False` is still the default, so every test
in `tests/test_loop.py` keeps exercising the unwrapped phase unchanged.

No FreeCAD and no network: a fake `ToolRegistry` stands in for the Document,
exactly as `tests/test_agent.py` and `tests/test_loop.py :: FakeTools` already
do, and every model turn comes from `ScriptedLLMClient`.
"""
import json
from unittest.mock import Mock

from core import agent
from core.events import ASSERTION, EventStream, THOUGHT, TOOL_CALL, TOOL_RESULT
from core.llm import ScriptedLLMClient
from core.loop import Budgets, assertion
from core.tools import ToolRegistry


def _tools(checks=()):
    """
    A registry over the same measurement Tools Assertion is always handed —
    `overall_size`, `check_assertions` — plus one write-side Tool,
    `apply_step`, so an isolation test has a Plan/Execute-only concern to
    look for and not find.
    """
    registry = ToolRegistry()
    calls = []
    queued = list(checks)

    @registry.tool("check_assertions", "Run read-only checks against a copy.", {
        "type": "object", "properties": {"code": {"type": "string"}},
        "required": ["code"],
    })
    def check_assertions(code):
        calls.append(("check_assertions", {"code": code}))
        return queued.pop(0) if queued else {"ok": True}

    @registry.tool("overall_size", "How big the part came out.", {
        "type": "object", "properties": {},
    })
    def overall_size():
        calls.append(("overall_size", {}))
        return {"size_mm": [30, 30, 30]}

    @registry.tool("apply_step", "Apply one Step.", {
        "type": "object", "properties": {"code": {"type": "string"}},
        "required": ["code"],
    })
    def apply_step(code):
        calls.append(("apply_step", {"code": code}))
        return {"status": "applied"}

    registry.calls = calls
    return registry


def _budgets(**over):
    return Budgets(**{"attempts_per_step": 3, "agent_turns": 5, **over})


ACCEPTANCE = ["Shape.Mass is 27000 * 0.0079 grams"]
DESCRIBED = {"features": []}


def test_an_assertion_turn_with_agentic_on_cannot_run_code_against_the_document():
    """
    A `run` action is refused before it ever reaches `Namespace.run` — the
    turn is told to use `check_assertions` instead, the phase still answers,
    and the check it wrote still runs through the one path Assertion's
    checks were always meant to (`core/loop.py :: NOT_ARMED_FOR_RUN`).
    """
    tools = _tools(checks=[{"ok": True, "held": ACCEPTANCE}])
    llm = ScriptedLLMClient([
        json.dumps({"run": "import FreeCAD; FreeCAD.ActiveDocument.recompute()"}),
        json.dumps({"code": "assert True"}),
    ])
    events = EventStream()

    checked = assertion(ACCEPTANCE, DESCRIBED, tools, llm, events,
                         budgets=_budgets(), agentic=True)

    assert checked.failed is False
    refusals = [event.data["result"] for event in events.of(TOOL_RESULT)
               if event.data["name"] == "run"]
    assert len(refusals) == 1
    assert "error" in refusals[0]
    assert "check_assertions" in refusals[0]["error"]


def test_the_assertion_event_shape_is_unchanged_by_wrapping_the_phase_as_an_agent():
    """Same event, same keys, whichever way the phase got its answer."""
    plain_tools = _tools(checks=[{"ok": True, "held": ACCEPTANCE}])
    plain_llm = ScriptedLLMClient([json.dumps({"code": "assert True"})])
    plain_events = EventStream()
    assertion(ACCEPTANCE, DESCRIBED, plain_tools, plain_llm, plain_events,
              budgets=_budgets())

    agentic_tools = _tools(checks=[{"ok": True, "held": ACCEPTANCE}])
    agentic_llm = ScriptedLLMClient([json.dumps({"code": "assert True"})])
    agentic_events = EventStream()
    assertion(ACCEPTANCE, DESCRIBED, agentic_tools, agentic_llm, agentic_events,
              budgets=_budgets(), agentic=True)

    plain_event = plain_events.of(ASSERTION)[0]
    agentic_event = agentic_events.of(ASSERTION)[0]
    assert set(plain_event.data) == set(agentic_event.data)
    assert plain_event.data == agentic_event.data


def test_a_failing_assert_still_reports_what_did_not_hold_beside_what_did():
    tools = _tools(checks=[{
        "ok": False,
        "traceback": "AssertionError: these did not hold: the mass",
        "held": ["the part has four faces"],
        "failed": ["the mass"],
    }])
    llm = ScriptedLLMClient([json.dumps({"code": "assert False, 'these did not hold: the mass'"})])
    events = EventStream()

    checked = assertion(ACCEPTANCE, DESCRIBED, tools, llm, events,
                         budgets=_budgets(), agentic=True)

    assert checked.failed is True
    assert checked.confirmed == ["the part has four faces"]
    assert checked.unmet == ["the mass"]
    event = events.of(ASSERTION)[0]
    assert event.data["confirmed"] == ["the part has four faces"]
    assert event.data["unmet"] == ["the mass"]


def test_assertion_still_receives_no_channel_to_any_other_phase_when_wrapped_as_an_agent():
    """
    A wrapped Assertion turn that looks something up and fails its list still
    never names a Plan- or Execute-only concern on the wire, and its own
    reasoning never mentions a Step or a Plan.
    """
    tools = _tools(checks=[{
        "ok": False,
        "traceback": "AssertionError: these did not hold: the mass",
        "held": [],
        "failed": ["the mass"],
    }])
    llm = ScriptedLLMClient([
        json.dumps({"look": {"tool": "overall_size", "arguments": {}}}),
        json.dumps({"code": "assert False, 'these did not hold: the mass'"}),
    ])
    events = EventStream()

    assertion(ACCEPTANCE, DESCRIBED, tools, llm, events, budgets=_budgets(), agentic=True)

    call_names = [event.data["name"] for event in events.of(TOOL_CALL)]
    assert "apply_step" not in call_names
    for event in events.of(THOUGHT):
        text = str(event.data.get("text") or "")
        assert "Step" not in text and "Plan" not in text


def test_assertion_delegates_a_check_to_rlm_and_says_so_on_the_stream(monkeypatch):
    """
    The delegation seam Design already exercises
    (`tests/test_design_agent_wrapper.py`) is `core/agent.py :: ask` itself,
    not something Design's own call site adds — `assertion()`'s `agentic`
    branch reaches the very same `ask`, so a `{"delegate": ...}` reply is
    read here exactly as Design reads one: `rlm` is called with the request
    named, and a `THOUGHT` names the delegation before the phase's own final
    answer.
    """
    tools = _tools(checks=[{"ok": True, "held": ACCEPTANCE}])
    llm = ScriptedLLMClient([
        json.dumps({"delegate": {"page": "the tolerance note"}}),
        json.dumps({"code": "assert True"}),
    ])
    spawned = Mock(return_value={"answer": "read off the tolerance note"})
    monkeypatch.setattr(agent, "rlm", spawned)
    events = EventStream()

    checked = assertion(ACCEPTANCE, DESCRIBED, tools, llm, events,
                         budgets=_budgets(), agentic=True)

    assert checked.failed is False
    assert spawned.call_count == 1
    called_with = spawned.call_args.args
    assert any("the tolerance note" in str(argument) for argument in called_with)

    thoughts = [event.data["text"] for event in events.of(THOUGHT)]
    assert any("delegat" in text and "the tolerance note" in text for text in thoughts)
