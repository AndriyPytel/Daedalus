"""
Design as an RLM-agent through `run()`'s own production wiring (FCC-185), not
`design()`'s own unit contract (`tests/test_design_agent_wrapper.py`).

`run(agentic=True)` reaches Design the same way it already reaches Execute
(FCC-187) and Assertion (FCC-189): the one seam, on for all three at once.
`FakeTools` (`tests/test_loop.py`) registers a real `snapshot` Tool, the same
one Plan and Execute are handed — proving Design cannot reach it even though
this run's own registry carries it is the whole point of this file.
"""
import os
import tempfile

from core.events import EventStream, TOOL_RESULT
from core.llm import ScriptedLLMClient
from core.loop import Budgets, run
from core.traps import TrapStorage, TrapStore
from tests.test_loop import (FakeTools, assertion_reply, code_reply, design_reply,
                             everything, look_reply, plan_reply, verdict_reply, A_CUBE)


def test_an_agentic_design_turn_cannot_look_at_this_run_s_own_document():
    """
    The first provider call of any run is Design's (FCC-153). Scripted to try
    `{"look": {"tool": "snapshot", ...}}` first, the same real Tool Plan and
    Execute go on to call through this run's own `FakeTools` registry — and
    Design's attempt is refused, because `run` never hands Design that
    registry (`core/loop.py :: _DESIGN_HAS_NOTHING_TO_LOOK_WITH`). The run
    still finishes: refused is not fatal, the same way an unresolved `look`
    never is (`core/agent.py :: _take_a_look`).
    """
    llm = ScriptedLLMClient([
        look_reply("snapshot"),
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        assertion_reply("assert True"),
        verdict_reply("pass"),
    ])
    tools = FakeTools()
    events = EventStream()
    traps = TrapStore(storage=TrapStorage(
        path=os.path.join(tempfile.mkdtemp(), "trap_proposals.json")), events=events)

    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                 budgets=Budgets(), agentic=True, traps=traps)

    assert result.ok

    first_result = next(event.data for event in events.of(TOOL_RESULT))
    assert first_result["name"] == "snapshot"
    assert "error" in first_result["result"]
