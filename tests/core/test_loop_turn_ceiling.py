"""
The run's turn ceiling is one supervisor for the whole run, not a
fresh one per phase continuation.

`core.loop.execute.execute()` and `core.loop.plan.plan()` each default an
unsupplied `supervisor` to a fresh `SessionTreeSupervisor()`
(`execute.py:179`, `plan.py:90`) -- a caller that never threads one in gets a
new 30-turn allowance every time it calls in, which is exactly what
`postepic-smoke-analysis.md` §3 found: Execute spent its cap twice in one
run, once mid-pass and again after a plan revision restarted it. `_run` must
create the supervisor once and hand the same object to every phase call it
makes, so a Step retry or a plan revision inherits what was already spent.
"""
import json

from core.loop import execute as execute_module
from core.loop import plan as plan_module
from tests.loop_helpers import code_reply, drive, plan_reply, suspect_reply, verdict_reply


def _unbuildable_after_two_steps():
    return json.dumps({"declare": {"steps": [
        {"step": 1, "label": "Step"}, {"step": 2, "label": "Step"}],
        "unbuildable": "cannot build Step 3 as written"}})


def test_a_plan_revision_shares_the_same_supervisor_execute_spent_its_turns_on(monkeypatch):
    seen = []
    real_run_phase = execute_module.run_phase

    def spying(*args, **kwargs):
        seen.append(kwargs.get("supervisor"))
        return real_run_phase(*args, **kwargs)

    monkeypatch.setattr(execute_module, "run_phase", spying)

    result, events, llm, tools = drive([
        plan_reply("Pad the outline", "Pad the boss", "Fillet the edge"),
        json.dumps({"code": "pad1()", "step": 1, "label": "Step"}),
        json.dumps({"code": "pad2()", "step": 2, "label": "Step"}),
        _unbuildable_after_two_steps(),
        plan_reply("Fillet the edge, done differently"),
        code_reply("fillet()"),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=[
        {"status": "applied", "created": ["Pad1"], "description": {}},
        {"status": "applied", "created": ["Pad2"], "description": {}},
    ])

    assert result.ok, result.reason
    assert len(seen) == 2, "one execute() call before the revision, one after"
    assert seen[0] is not None and seen[1] is not None
    assert seen[0] is seen[1], (
        "the post-revision Execute call must share the same supervisor the "
        "pre-revision call spent turns on, not start a fresh 30-turn budget")


def test_execute_and_plan_share_the_one_supervisor_the_run_created(monkeypatch):
    execute_seen = []
    plan_seen = []
    real_execute_run_phase = execute_module.run_phase
    real_plan_run_phase = plan_module.run_phase

    def spying_execute(*args, **kwargs):
        execute_seen.append(kwargs.get("supervisor"))
        return real_execute_run_phase(*args, **kwargs)

    def spying_plan(*args, **kwargs):
        plan_seen.append(kwargs.get("supervisor"))
        return real_plan_run_phase(*args, **kwargs)

    monkeypatch.setattr(execute_module, "run_phase", spying_execute)
    monkeypatch.setattr(plan_module, "run_phase", spying_plan)

    result, events, llm, tools = drive([
        plan_reply("Pad the outline"),
        code_reply("box()"),
        suspect_reply(),
        verdict_reply("pass"),
    ])

    assert result.ok, result.reason
    assert plan_seen and execute_seen
    assert all(supervisor is not None for supervisor in plan_seen + execute_seen)
    assert plan_seen[0] is execute_seen[0], (
        "Plan and Execute must run under the one supervisor _run created for "
        "this run, not each default their own")


def test_a_long_plan_gets_a_ceiling_sized_from_its_own_step_count(monkeypatch):
    """
    `TURN_BUDGET` (30) is sized for the p90 sub-agent tree, never measured
    against a long Execute. The ceiling `_run` hands the one supervisor it
    creates must grow with the Plan it is sized from, staying at least
    `3 * len(steps) + RESERVE`.
    """
    from core.agent.subagent import RESERVE

    seen = []
    real_run_phase = execute_module.run_phase

    def spying(*args, **kwargs):
        seen.append(kwargs.get("supervisor"))
        return real_run_phase(*args, **kwargs)

    monkeypatch.setattr(execute_module, "run_phase", spying)

    steps = [f"Step {number}" for number in range(1, 17)]
    codes = [f"op{number}()" for number in range(1, 17)]

    result, events, llm, tools = drive([
        plan_reply(*steps),
        code_reply(*codes),
        suspect_reply(),
        verdict_reply("pass"),
    ])

    assert result.ok, result.reason
    assert seen and seen[0] is not None
    assert seen[0].turn_budget == 3 * 16 + RESERVE, (
        "a 16-Step Plan must get a ceiling of at least 3 turns per Step plus "
        "the reserve, not the flat 30 sized for a much shorter tree")
