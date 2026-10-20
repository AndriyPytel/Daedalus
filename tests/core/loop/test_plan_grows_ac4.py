"""Loop level -- AC4: a rollback deeper than the undo stack is refused and replanned."""
import json

from core.events import EventStream, PLAN_REVISED
from core.loop.run import run

from tests.loop_helpers import (a_person_who_declines, FakeTools, StubKernel, gate_that, plan_reply, scripted,
                               suspect_reply, verdict_reply)


def _undo_calls(tools):
    return [call for call in tools.calls if call[0] == "undo_step"]


def test_a_rollback_past_the_undo_stack_is_refused_before_any_undo_call():
    """
    Steps 1-5 land, but the undo stack only reaches back 2 -- a live
    Document whose stack already dropped the older transactions. A
    revision that asks to roll back to Step 1 (needing 4 undos) is
    refused through the same mechanical replan path a bad op is, before
    any undo call, and the next reply's ordinary continuation still lands.
    """
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
              for n in range(1, 6)]
    applies.append({"status": "applied", "created": ["Obj6"], "description": {}})
    turns = [
        plan_reply(*("Step %d" % n for n in range(1, 6))),
        *[json.dumps({"code": "s%d()" % n, "step": n, "label": "Step"}) for n in range(1, 6)],
        json.dumps({"declare": {"steps": [{"step": n, "label": "Step"} for n in range(1, 6)]}}),
        suspect_reply(),
        verdict_reply("fail", "Step 1's footprint is wrong"),
        plan_reply(rollback={"to": 1, "why": "the footprint is wrong from the start"}),
        plan_reply("Fix it"),
        json.dumps({"code": "fix()", "step": 6, "label": "Step"}),
        json.dumps({"declare": {"steps": [{"step": 6, "label": "Step"}]}}),
        suspect_reply(),
        verdict_reply("pass"),
    ]
    llm = scripted(turns)
    tools = FakeTools(applies, undo_capacity=2)
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                gate=gate_that(), kernel=StubKernel(), ask=a_person_who_declines)

    assert result.ok, result.reason
    assert _undo_calls(tools) == [], (
        "an insufficient undo stack must be refused before any undo call, got %r"
        % _undo_calls(tools))
    revised = [e for e in events if e.kind == PLAN_REVISED]
    assert len(revised) == 2, "the unreachable rollback must cost a replan, not end the run"
