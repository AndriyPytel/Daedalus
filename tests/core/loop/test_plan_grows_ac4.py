"""Loop level -- AC4: a rollback deeper than the undo stack blocks, undoing nothing."""
import json

from core.events import EventStream
from core.loop.run import run

from tests.loop_helpers import FakeTools, StubKernel, gate_that, plan_reply, scripted, suspect_reply, verdict_reply


def _undo_calls(tools):
    return [call for call in tools.calls if call[0] == "undo_step"]


def test_a_rollback_past_the_undo_stack_blocks_and_undoes_nothing():
    """
    Steps 1-5 land, but the undo stack only reaches back 2 -- a live
    Document whose stack already dropped the older transactions. A
    revision that asks to roll back to Step 1 (needing 4 undos) is
    refused before any undo call, with the reason named.
    """
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
              for n in range(1, 6)]
    turns = [
        plan_reply(*("Step %d" % n for n in range(1, 6))),
        *[json.dumps({"code": "s%d()" % n, "step": n, "label": "Step"}) for n in range(1, 6)],
        json.dumps({"declare": {"steps": [{"step": n, "label": "Step"} for n in range(1, 6)]}}),
        suspect_reply(),
        verdict_reply("fail", "Step 1's footprint is wrong"),
        plan_reply(rollback={"to": 1, "why": "the footprint is wrong from the start"}),
    ]
    llm = scripted(turns)
    tools = FakeTools(applies, undo_capacity=2)
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                gate=gate_that(), kernel=StubKernel())

    assert not result.ok
    assert _undo_calls(tools) == [], (
        "an insufficient undo stack must block before any undo call, got %r"
        % _undo_calls(tools))
    assert "rolls back" in result.reason and "to Step 1" in result.reason, result.reason
