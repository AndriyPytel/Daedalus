"""Loop level -- a `rollback` field naming a Step never applied is refused, not crashed on."""
import json

from core.events import PLAN_REVISED, STEP_STARTED

from tests.loop_helpers import code_reply, drive, plan_reply, suspect_reply, verdict_reply


def test_a_rollback_naming_a_step_never_applied_is_refused_and_replanned():
    """
    Steps 1-5 land, Review fails, and the revision's reply names a rollback
    target Step 99 was never applied. The reply is refused through the same
    mechanical replan path a bad op is, not crashed on and not silently
    dropped -- the next reply's ordinary continuation still lands.
    """
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
              for n in range(1, 6)]
    applies.append({"status": "applied", "created": ["Obj6"], "description": {}})

    result, events, llm, tools = drive([
        plan_reply(*("Step %d" % n for n in range(1, 6))),
        *[json.dumps({"code": "s%d()" % n, "step": n, "label": "Step"})
          for n in range(1, 6)],
        json.dumps({"declare": {"steps": [{"step": n, "label": "Step"}
                                for n in range(1, 6)]}}),
        suspect_reply(),
        verdict_reply("fail", "Step 1's footprint is wrong"),
        plan_reply(rollback={"to": 99, "why": "a target never applied"}),
        plan_reply("Fix it"),
        code_reply("fix()", first=6),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=applies)

    assert result.ok, result.reason
    assert result.steps_applied == 6
    starts = [e.data["index"] for e in events if e.kind == STEP_STARTED]
    assert starts[-1] == 6
    revised = [e for e in events if e.kind == PLAN_REVISED]
    assert len(revised) == 2, "the bad rollback must cost a replan, not crash silently"
