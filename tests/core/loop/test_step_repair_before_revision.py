"""
`docs/research/road-to-the-fillets.md` §2.5, read against the recorded
run's own event log: attempt 3's Step 4 rehearsal code raised
`AttributeError: ... has no attribute 'isError'` -- a Python-level bug in
the Step's own generated code, not a rejection of the Step by `apply_step`
and not a considered `unbuildable` verdict about the Plan. The model wrote
about that error in its own reasoning and then declared the Step
unbuildable at once; a Plan revision followed some 28 seconds later. The
Plan was never wrong -- `PartDesign::AdditivePipe` not carrying `isError`
says nothing about the drawing.

`run_phase` (`core/agent/agent.py`) now refuses an `unbuildable` declared
right after a rehearsal failure of that kind and demands the Step again,
bounded by `budgets.attempts_per_step` the same ceiling `on_declaration`'s
own retries already spend against. A geometric refusal or an explicit
verdict with no Python-level error behind it is not refused this way, and
still reaches the revision immediately, as it does today
(`test_loop_questions.py`'s "the bore misses the web as planned").
"""
import json

from core.events import PLAN_REVISED, TOOL_RESULT
from core.kernel import KernelResult

from tests.loop_helpers import StubKernel, drive, plan_reply, suspect_reply, verdict_reply


_ISERROR = ("Traceback (most recent call last):\n"
           "AttributeError: 'PartDesign::AdditivePipe' object has no attribute 'isError'\n")


def test_a_rehearsal_python_error_is_repaired_in_place_before_any_revision():
    """
    The recorded Step 4 failure, replayed against its own shape: the
    rehearsal raises an `AttributeError`, the model gives up on it at once,
    and the harness sends the Step back rather than take that as final --
    the retry then lands, and no revision follows.
    """
    result, events, llm, tools = drive([
        plan_reply("Sweep the \u00f84 rib round as an additive pipe"),
        json.dumps({"code": "additive_pipe()", "step": 1, "label": "Rib"}),
        json.dumps({"declare": {"unbuildable": "isError does not exist on this object"}}),
        json.dumps({"code": "additive_pipe_fixed()", "step": 1, "label": "Rib"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Rib"}]}}),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=[{"status": "applied", "created": ["Rib"], "description": {}}],
       kernel=StubKernel(fails=[KernelResult(ok=False, traceback=_ISERROR)]))

    assert result.ok, result.reason
    assert not events.of(PLAN_REVISED), (
        "a Python-level error in the Step's own code is a bug to repair, not a "
        "verdict on the Plan; no revision should have followed it")
    assert tools.applied_codes() == ["additive_pipe_fixed()"]
    refusals = [event.data["result"] for event in events.of(TOOL_RESULT)
               if event.data.get("result", {}).get("status") == "refused"]
    assert any("bug in the Step's own code" in refusal.get("traceback", "")
              for refusal in refusals), "the model must be told this is its own bug, not the Plan's"


def test_a_geometric_refusal_still_reaches_the_revision_at_once():
    """
    The negative case: a Step whose rehearsal fails for a reason that is not
    a Python-level error -- a geometric refusal from the Step's own code --
    is not repaired in place. It reaches the revision immediately, exactly
    as an explicit `unbuildable` verdict already does.
    """
    result, events, llm, tools = drive([
        plan_reply("Sweep the \u00f84 rib round as an additive pipe"),
        json.dumps({"code": "additive_pipe()", "step": 1, "label": "Rib"}),
        json.dumps({"declare": {"unbuildable": "the sweep self-intersects as planned"}}),
        plan_reply("Loft the rib round instead of sweeping it"),
        json.dumps({"code": "lofted_rib()", "step": 1, "label": "Rib"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Rib"}]}}),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=[{"status": "applied", "created": ["Rib"], "description": {}}],
       kernel=StubKernel(fails=[KernelResult(
           ok=False, traceback="RuntimeError: BRep_API: command not done")]))

    assert result.ok, result.reason
    revisions = events.of(PLAN_REVISED)
    assert len(revisions) == 1, "a geometric refusal must reach the revision at once"
    assert tools.applied_codes() == ["lofted_rib()"]
