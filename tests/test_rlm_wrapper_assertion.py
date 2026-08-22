"""
Assertion as an RLM-agent (FCC-189): the persistent namespace `core/agent.py`
gave Plan (FCC-183), threaded through Assertion's own retries.

`assertion()` already writes and runs executable checks through
`check_assertions` (FCC-168/FCC-170); this file is not a test of that
capability but of the one thing FCC-189 actually adds — a name a `run`
action binds while writing one attempt's checks is still bound while writing
the next attempt's, within one call to `assertion()`. `agentic=False` is
still the default, so every test in `tests/test_loop.py` keeps exercising the
unwrapped phase unchanged.

No FreeCAD and no network: a fake `ToolRegistry` stands in for the Document,
exactly as `tests/test_agent.py` and `tests/test_loop.py :: FakeTools` already
do, and every model turn comes from `ScriptedLLMClient`.
"""
import json

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


def test_a_name_bound_by_the_first_assert_run_is_visible_to_the_second_within_one_assertion_phase():
    """
    Attempt 1 binds `expected` with a `run` action, then writes a check whose
    own code is broken (a `NameError`, not the model failing its list) — the
    kind of failure `assertion()` retries rather than reports. Attempt 2 opens
    by printing `expected` back — the only way that succeeds is if the
    `Namespace` `agentic=True` opens survived the retry, which is FCC-189's
    whole point: the unwrapped phase has no `Namespace` at all, so there is
    nothing before it for a second attempt to be given.
    """
    tools = _tools(checks=[
        {"ok": False, "traceback": "NameError: name 'held' is not defined"},
        {"ok": True, "held": ACCEPTANCE},
    ])
    llm = ScriptedLLMClient([
        json.dumps({"run": "expected = 27000 * 0.0079"}),
        json.dumps({"code": "assert expected"}),
        json.dumps({"run": "print(expected)"}),
        json.dumps({"code": "assert True"}),
    ])
    events = EventStream()

    checked = assertion(ACCEPTANCE, DESCRIBED, tools, llm, events,
                         budgets=_budgets(), agentic=True)

    assert checked.failed is False
    run_outputs = [event.data["result"]["output"] for event in events.of(TOOL_RESULT)
                  if event.data["name"] == "run"]
    assert run_outputs == ["", "213.3\n"]


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
