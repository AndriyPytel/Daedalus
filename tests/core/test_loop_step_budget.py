"""
FCC-cheapest-experiment: the per-Step exploration budget crosses a
same-Step regeneration (DAE-A-53) instead of resetting with it.

`analysis-checkpoint-rollback.md` fix (b): `since_declaration`
(`core.agent.agent`) demands a declaration after `_DECLARATION_LIMIT`
undeclared turns, but was scoped to one `run_phase` call -- a fresh
`execute()` regeneration for a Step nobody wrote code for got a fresh
budget every time. This is the seam's own plumbing test: `execute()` must
carry the count forward when `_work_through` retries the same Step, not
just reset it because a new Session started.
"""
import json

from core.agent import agent as agent_module
from core.agent import phase_contract as pc
from tests.loop_helpers import _MultiTurn, code_reply, drive, plan_reply, suspect_reply, verdict_reply


def _no_step_turn(text):
    return json.dumps({"code": "print(%r)" % text})


_DECLARE_NOTHING = json.dumps({"declare": {"steps": []}})


def test_a_same_step_regeneration_inherits_the_prior_calls_exploration_count(monkeypatch):
    seen = []
    real_run_phase = agent_module.run_phase

    def spying(phase, *args, **kwargs):
        if phase == pc.EXECUTE:
            seen.append(dict(kwargs.get("exploration") or {}))
        return real_run_phase(phase, *args, **kwargs)

    monkeypatch.setattr(agent_module, "run_phase", spying)

    result, events, llm, tools = drive([
        plan_reply("Pad the outline"),
        _MultiTurn([_no_step_turn("probe 1"), _no_step_turn("probe 2"), _DECLARE_NOTHING]),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=[{"status": "applied", "created": ["Box"], "description": {}}])

    assert result.ok, result.reason
    assert len(seen) == 2, "one execute() call per regeneration, two in this run"
    assert seen[0].get("turns", 0) == 0, "the first regeneration starts cold"
    assert seen[1].get("turns", 0) >= 2, (
        "the second regeneration must inherit the exploration turns the first "
        "one spent on this same Step, not start over at 0")


def test_a_plan_revision_inherits_the_exploration_count_the_step_before_it_spent(monkeypatch):
    """
    `_run`'s replan loop must not hand a fresh `_work_through` pass a clean
    exploration slate: a Step whose prose survives a revision unchanged has
    to keep the turns it already burned probing it in the pass before.
    """
    seen = []
    real_run_phase = agent_module.run_phase

    def spying(phase, *args, **kwargs):
        if phase == pc.EXECUTE:
            seen.append(dict(kwargs.get("exploration") or {}))
        return real_run_phase(phase, *args, **kwargs)

    monkeypatch.setattr(agent_module, "run_phase", spying)

    result, events, llm, tools = drive([
        plan_reply("Pad the outline"),
        _MultiTurn([_no_step_turn("probe 1"), _no_step_turn("probe 2"),
                   json.dumps({"declare": {"unbuildable": "cannot pad it as written"}})]),
        plan_reply("Pad the outline"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=[{"status": "applied", "created": ["Box"], "description": {}}])

    assert result.ok, result.reason
    assert len(seen) == 2, "one execute() call before the revision, one after"
    assert seen[0].get("turns", 0) == 0, "the pre-revision pass starts cold"
    assert seen[1].get("turns", 0) >= 2, (
        "the post-revision pass must inherit what the pre-revision pass spent "
        "on this same Step, not start over at 0")
