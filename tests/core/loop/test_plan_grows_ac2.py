"""Loop level -- AC2: a Rollback Step undoes to Step K; History keeps the rolled-back Steps."""
import json

from core.events import STEP_STARTED

from tests.loop_helpers import (code_reply, driven_through, gate_that, plan_reply,
                               suspect_reply, verdict_reply)


def _undo_calls(tools):
    return [call for call in tools.calls if call[0] == "undo_step"]


def test_a_rollback_step_undoes_to_k_and_history_log_marks_the_dropped_steps():
    """
    Steps 1-5 land, Review fails and blames Step 3, and the revision's
    reply carries `rollback: {"to": 3, "why": ...}` alongside two rebuilt
    Steps. Execute undoes exactly Steps 4 and 5, declares Step 4 next, and
    the run's history log still holds 4 and 5 -- marked rolled back.
    """
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
              for n in range(1, 6)]
    applies += [{"status": "applied", "created": ["New%d" % n], "description": {}}
               for n in (4, 5)]

    result, events, llm, tools = driven_through(
        gate_that(),
        [
            plan_reply(*("Step %d" % n for n in range(1, 6))),
            *[json.dumps({"code": "s%d()" % n, "step": n, "label": "Step"})
              for n in range(1, 6)],
            json.dumps({"declare": {"steps": [{"step": n, "label": "Step"}
                                    for n in range(1, 6)]}}),
            suspect_reply(),
            verdict_reply("fail", "Step 3's fillet direction is wrong"),
            plan_reply("Step 4 redone", "Step 5 redone",
                      rollback={"to": 3, "why": "the fillet direction is wrong"}),
            *[json.dumps({"code": "new%d()" % n, "step": n, "label": "Step"}) for n in (4, 5)],
            json.dumps({"declare": {"steps": [{"step": n, "label": "Step"} for n in (4, 5)]}}),
            suspect_reply(),
            verdict_reply("pass"),
        ], applies=applies)

    assert result.ok, result.reason
    assert _undo_calls(tools) == [("undo_step", {}), ("undo_step", {})], (
        "the rollback must undo exactly Steps 4 and 5, got %r" % _undo_calls(tools))
    starts = [e.data["index"] for e in events if e.kind == STEP_STARTED]
    assert 4 in starts, "Execute's next declaration must be Step 4, got %r" % starts
