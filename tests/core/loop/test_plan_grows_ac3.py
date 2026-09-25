"""Loop level -- AC3: the Plan Gate of a revision shows History apart from the continuation."""
import json

from core.events import GATE

from tests.loop_helpers import (code_reply, driven_through, gate_that, plan_reply,
                               suspect_reply, verdict_reply)


def test_the_gate_of_a_rollback_revision_shows_history_apart_from_the_continuation():
    """
    The Gate call that asks about the revision's continuation carries the
    standing History (Steps 1-5, not yet marked rolled back) separately from
    the two Steps it is actually approving, plus the rollback and its reason.
    """
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
              for n in range(1, 6)]
    applies += [{"status": "applied", "created": ["New%d" % n], "description": {}}
               for n in (4, 5)]

    gate = gate_that()
    result, events, llm, tools = driven_through(
        gate,
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
    gates = [e for e in events if e.kind == GATE and e.data.get("decision") == "approve"]
    revision_gate = gates[-1]
    assert [entry["step"] for entry in revision_gate.data["history"]] == [1, 2, 3, 4, 5], \
        revision_gate.data["history"]
    assert revision_gate.data["steps"] == ["Step 4 redone", "Step 5 redone"], \
        "the continuation shown must not include the rollback pseudo-step or History"
    assert revision_gate.data["rollback"] == {"to_step": 3,
                                              "reason": "the fillet direction is wrong"}

    the_call = gate.calls[-1]
    assert [entry["step"] for entry in the_call["history"]] == [1, 2, 3, 4, 5], (
        "the interactive Gate call must carry the History too, not only the GATE event: "
        "%r" % the_call)
    assert the_call["rollback"]["to_step"] == 3 and \
        the_call["rollback"]["reason"] == "the fillet direction is wrong", (
        "the interactive Gate call must carry the rollback and its reason: %r" % the_call)
