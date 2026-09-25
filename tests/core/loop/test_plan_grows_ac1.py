"""Loop level -- AC1: a revision after Steps have landed continues, undoing nothing."""
import json

from core.events import STEP_STARTED

from tests.loop_helpers import code_reply, drive, plan_reply, suspect_reply, verdict_reply


def _undo_calls(tools):
    return [call for call in tools.calls if call[0] == "undo_step"]


def test_a_revision_after_a_handback_and_a_miscounted_rewrite_undoes_nothing():
    """
    Steps 1-16 land, Step 17 comes back unbuildable, and the first revision's
    continuation for it does not add up -- the mechanical arithmetic check
    catches it before anything runs. Neither cause calls Steps 1-16 into
    question: replaying run 55981c03's fault, the second revision must not
    undo a single Step, and Execute's next declaration is still Step 17.
    """
    seventeen = ["Step %d" % n for n in range(1, 18)]
    applies = [{"status": "applied", "created": ["Obj%d" % n], "description": {}}
              for n in range(1, 17)]

    result, events, llm, tools = drive([
        plan_reply(*seventeen),
        *[json.dumps({"code": "s%d()" % n, "step": n, "label": "Step"})
          for n in range(1, 17)],
        json.dumps({"declare": {"steps": [{"step": n, "label": "Step"} for n in range(1, 17)],
                    "unbuildable": "Step 17 cannot be built as written"}}),
        plan_reply("Step 17 rewritten: 73 * cos(45) = 60 mm"),
        plan_reply("Step 17 rewritten: 73 * cos(45) = 51.6 mm"),
        code_reply("fillet()", first=17),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=applies)

    assert result.ok, result.reason
    assert result.steps_applied == 17
    assert _undo_calls(tools) == [], (
        "a revision that only continues from Step 17 must not undo anything, "
        "got %r" % _undo_calls(tools))
    starts = [e.data["index"] for e in events if e.kind == STEP_STARTED]
    assert starts[-1] == 17, "Execute's next declaration must be Step 17, got %r" % starts
