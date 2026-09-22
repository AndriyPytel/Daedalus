"""
FCC-433: `attempts_per_step` must bind on the Step itself, not only on the
directive `on_declaration` hands back. Attempt 1 of the post-epic smoke
(`logs/mm24-smoke-postepic/opus/mm2024-phase1.attempt1.jsonl`) recorded six
`preflight_failed` events on Step 5 -- one free refusal plus
`Budgets.attempts_per_step` (3) implies at most four. `on_declaration`
(`src/core/loop/execute.py`) stops returning `retry: True` at the fourth,
but nothing stops a fifth or sixth declaration for the same Step from
reaching `apply_step` again: the ceiling is advisory, not enforced.
"""
import json

from core.events import PREFLIGHT_FAILED
from core.loop.budgets import Budgets

from tests.loop_helpers import drive, failed, plan_reply, retried_code_reply, suspect_reply, verdict_reply


def test_a_step_refused_past_its_budget_reaches_apply_step_no_more_times_than_the_ceiling_allows():
    """
    The recorded run's own shape: six attempts at one Step, none of them a
    plan revision in between. `attempts_per_step=3` plus the free first
    refusal caps a Step at 4 `preflight_failed` events; a 5th and 6th must
    never reach `apply_step` at all.
    """
    _, events, _, tools = drive([
        plan_reply("Pad the rib"),
        retried_code_reply("one()", "two()", "three()", "four()", "five()", "six()", step=1),
        plan_reply("Pad the rib, differently"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[failed("boom")] * 6, budgets=Budgets(attempts_per_step=3))

    failures = events.of(PREFLIGHT_FAILED)
    assert len(failures) <= 4, (
        "attempts_per_step=3 plus the free first refusal caps a Step at 4 "
        "preflight_failed events; got %d" % len(failures))
    assert len(tools.applied_codes()) <= 4, (
        "a Step already past its ceiling must not reach apply_step again; "
        "got %d apply_step calls" % len(tools.applied_codes()))
