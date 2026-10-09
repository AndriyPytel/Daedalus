"""
`attempts_per_step` must bind on the Step itself, not only on the directive
`on_declaration` hands back. A recorded run showed six `preflight_failed`
events on one Step where one free refusal plus `Budgets.attempts_per_step`
(3) implies at most four: `on_declaration` (`src/core/loop/execute.py`)
stopped returning `retry: True` at the fourth, but nothing stopped a fifth
or sixth declaration for the same Step from reaching `apply_step` again --
the ceiling was advisory, not enforced.
"""
from core.events import PREFLIGHT_FAILED
from core.loop.budgets import Budgets

from tests.loop_helpers import drive, failed, plan_reply, retried_code_reply
from tests.event_helpers import events_of


def test_a_step_refused_past_its_budget_reaches_apply_step_no_more_times_than_the_ceiling_allows():
    """
    The recorded run's own shape: six attempts at one Step, none of them a
    plan revision in between. `attempts_per_step=3` plus the free first
    refusal caps a Step at 4 `preflight_failed` events; a 5th and 6th must
    never reach `apply_step` at all. The run is left incomplete on purpose
    -- what it does with the 6 scripted attempts, not whether it finishes,
    is what this asserts.
    """
    _, events, _, tools = drive([
        plan_reply("Pad the rib"),
        retried_code_reply("one()", "two()", "three()", "four()", "five()", "six()", step=1),
    ], applies=[failed("boom")] * 6, budgets=Budgets(attempts_per_step=3))

    failures = events_of(events, PREFLIGHT_FAILED)
    assert len(failures) == 4, (
        "attempts_per_step=3 plus the free first refusal caps a Step at 4 "
        "preflight_failed events; got %d" % len(failures))
    assert len(tools.applied_codes()) == 4, (
        "a Step already past its ceiling must not reach apply_step again; "
        "got %d apply_step calls" % len(tools.applied_codes()))
