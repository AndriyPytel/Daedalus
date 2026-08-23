"""
Level 1 -- Plan through the agent wrapper (FCC-186).

`core/loop.py :: _plan_once_it_knows_enough` now always calls `plan()` with
`agentic=True` (FCC-183's `core/agent.py :: ask`): Plan may look or run code
mid-turn instead of planning from prose alone. Nothing about what Plan hands
back changes shape for it -- the Steps, the `PLAN`/`PLAN_REVISED` event, and
the Gate that follows are the same as before this ticket. `agentic=False`
remains a caller's own choice and is unaffected by anything this ticket adds.

Written the way the rest of Level 1 is: a `ScriptedLLMClient` and
`tests.test_loop.FakeTools`, no FreeCAD and no subprocess anywhere in the
call.
"""
import json

from core import loop
from core.events import EventStream, PLAN, PLAN_REVISED, TOOL_CALL, TOOL_RESULT
from core.llm import ScriptedLLMClient
from core.loop import Budgets
from tests.test_loop import (
    SNAPSHOT, FakeTools, code_reply, obsolete_reply, plan_reply, verdict_reply,
)


def _tools():
    return FakeTools().registry


def test_a_plan_that_acts_before_answering_still_returns_the_same_steps_shape():
    tools_double = FakeTools()
    llm = ScriptedLLMClient([
        json.dumps({"design": {}, "question": ""}),
        json.dumps({"look": {"tool": "overall_size", "arguments": {}}}),
        plan_reply("Create a 10mm box", "Fillet the top edge"),
        code_reply("box()", "fillet()"),
        verdict_reply("pass"),
    ])

    from core.loop import run
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools_double.registry,
                 events=events, budgets=Budgets())

    assert result.ok
    steps = events.first(PLAN).data["steps"]
    assert steps == ["Create a 10mm box", "Fillet the top edge"]
    assert ("overall_size", {}) in tools_double.calls


def test_the_plan_event_still_carries_the_steps():
    tools_double = FakeTools()
    llm = ScriptedLLMClient([
        json.dumps({"design": {}, "question": ""}),
        plan_reply("Add a box"),
        code_reply("box()"),
        verdict_reply("pass"),
    ])

    from core.loop import run
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools_double.registry,
                 events=events, budgets=Budgets())

    assert result.ok
    plan_event = events.first(PLAN)
    assert plan_event is not None
    assert set(plan_event.data) >= {"steps", "acceptance", "unanswered", "decided"}
    assert plan_event.data["steps"] == ["Add a box"]


def test_a_look_through_the_agent_and_a_look_through_the_caller_level_loop_draw_on_one_looks_per_turn_allowance():
    budgets = Budgets(looks_per_turn=2)
    assert budgets.looks_per_turn == 2
    tools = _tools()
    events = EventStream()
    asking = loop._Asking()

    llm = ScriptedLLMClient([
        json.dumps({"look": {"tool": "overall_size", "arguments": {}}}),
        json.dumps({"look": {"tool": "overall_size", "arguments": {}}}),
        plan_reply("Add a box"),
    ])
    planned, grown = loop._plan_once_it_knows_enough(
        "Make a 10mm box", SNAPSHOT, llm, events, budgets, None,
        lambda: False, asking, tools)

    assert planned.steps == ["Add a box"]
    assert asking.looks == budgets.looks_per_turn == 2

    llm_revision = ScriptedLLMClient([
        json.dumps({"look": {"tool": "overall_size", "arguments": {}}}),
        plan_reply("Add a chamfer"),
    ])
    planned_revision, _ = loop._plan_once_it_knows_enough(
        "Make a 10mm box", SNAPSHOT, llm_revision, events, budgets, grown,
        lambda: False, asking, tools, feedback="try again",
        revising=planned.steps)

    assert planned_revision.steps == ["Add a chamfer"]
    assert asking.looks == 2, "a look already spent through the agent must not be handed a second allowance"
    refused = [event for event in events.of(TOOL_RESULT)
              if event.data["result"] == {"error": "budget spent"}]
    assert len(refused) == 1


def test_a_revision_after_a_hand_back_still_works_through_the_wrapper():
    tools_double = FakeTools()
    llm = ScriptedLLMClient([
        json.dumps({"design": {}, "question": ""}),
        plan_reply("Delete the old pad", "Add the new one"),
        obsolete_reply("There is no pad in this Document; nothing to delete."),
        json.dumps({"run": "1 + 1"}),
        plan_reply("Add the new one, differently"),
        code_reply("add_new()"),
        verdict_reply("pass"),
    ])

    from core.loop import run
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools_double.registry,
                 events=events, budgets=Budgets())

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None
    assert revised.data["steps"] == ["Add the new one, differently"]
    assert any(event.data["name"] == "run" for event in events.of(TOOL_CALL))


def test_the_phase_with_the_wrapper_off_behaves_exactly_as_today():
    tools = _tools()
    budgets = Budgets()

    events_off = EventStream()
    off = loop.plan("Make a 10mm box", SNAPSHOT,
                    ScriptedLLMClient([plan_reply("Add a box")]), events_off,
                    tools=tools, budgets=budgets, agentic=False)

    events_on = EventStream()
    on = loop.plan("Make a 10mm box", SNAPSHOT,
                   ScriptedLLMClient([plan_reply("Add a box")]), events_on,
                   tools=tools, budgets=budgets, agentic=True)

    assert off.steps == on.steps == ["Add a box"]
    assert not events_off.of(TOOL_CALL)
    assert [event.kind for event in events_off] == [event.kind for event in events_on]

    ignored = EventStream()
    with_looks_taken_ignored = loop.plan(
        "Make a 10mm box", SNAPSHOT, ScriptedLLMClient([plan_reply("Add a box")]),
        ignored, tools=tools, budgets=budgets, agentic=False, looks_taken=999)
    assert with_looks_taken_ignored.steps == ["Add a box"]
