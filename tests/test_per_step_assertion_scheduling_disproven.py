"""
FCC-247's own three questions, answered by measurement before any scheduling
changed, from `tests/recorded/assertion_scheduling_cost.json` (built from `logs/sessions/`
and `logs/ab-rlm-agentic/`, gitignored, hence the tracked fixture -- FCC-231's
established substitute; the script that built it is gone).

**Q1 -- what does an acceptance line cost to evaluate?** Session
`480b9995421e443d97b439a4f55bc3fe`, the session FCC-247 itself is about:
the model's own `cost` event for writing the assertion script costs 485.6 s;
the `check_assertions` tool call immediately after it, running that already-
written script against the Document, costs 1.2 s -- a 400x gap. Evaluating a
line that already has code behind it is cheap. Writing the code is not, and
that is the whole cost of the phase FCC-247's own ticket measured at "one
call and 486 s".

**Q2 -- can a line be evaluated before its own features exist?** Answered by
the code itself, not this fixture: `core/loop.py:1270`'s `Plan.satisfies`
threads each Step's own citations into `assertion()` (`core/loop.py:2416`),
which is the acceptance list restricted to what that Step names -- a Step
carries exactly the lines that became checkable when it landed, no earlier.

**Q3 and AC 4 -- does per-Step evaluation cost more than it saves?** Every
qualifying Step (a non-empty `satisfies`, 540 of them across 85 sessions
that logged a Plan) is a candidate for a per-Step check-writing call,
whether or not anything it claims is ever false -- there is no way to know
in advance which Steps to skip. Only 46, across 7 of those 85 sessions,
are a Step applied after another Step whose own claim was later found false
at the end -- the Steps a per-Step check could have caught earlier instead
of at Assertion. 540 candidate calls against 46 that would ever have found
anything is an 11.7x multiplier that holds regardless of what a single
check-writing call is assumed to cost (mean, median, or the cheapest
`execute`-phase call observed in the whole corpus) -- the ratio is
`qualifying_steps_with_satisfies / downstream_steps_after_earliest_failure`
and the per-call cost term cancels out of both sides.

**The verdict.** Scheduling `check_assertions` per Step, restricted to what
that Step's own `satisfies` names (AC 2's design), costs roughly 11.7x what
it would save, at every cost figure this fixture carries -- because the
expensive part (Q1) is writing a new check, and a per-Step schedule writes
one for every qualifying Step, not only the ones that would ever fail. This
ticket closes on that number, the way FCC-240 and FCC-242 did: the defect
FCC-247 was raised for is real and already caught, exactly, by the
end-of-run `assertion()` phase this ticket leaves unmoved (AC 3).
"""
import json
import os

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "recorded", "assertion_scheduling_cost.json")


def _fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_writing_the_check_costs_400x_running_it():
    data = _fixture()["cost"]
    ratio = data["evidence_write_seconds"] / data["evidence_check_execution_seconds"]
    assert data["evidence_session"] == "480b9995421e443d97b439a4f55bc3fe"
    assert ratio > 100


def test_540_qualifying_steps_against_46_that_would_ever_be_caught_early():
    data = _fixture()["measured_from"]
    assert data["qualifying_steps_with_satisfies"] == 540
    assert data["downstream_steps_after_earliest_failure"] == 46
    assert data["sessions_with_assertion_failure"] == 12
    assert data["sessions_with_resolvable_owning_step"] == 7


def test_the_added_cost_outweighs_the_saved_cost_at_every_price_point():
    fixture = _fixture()
    measured, cost = fixture["measured_from"], fixture["cost"]
    qualifying = measured["qualifying_steps_with_satisfies"]
    downstream = measured["downstream_steps_after_earliest_failure"]

    for seconds in (cost["execute_mean_seconds"], cost["execute_median_seconds"],
                   cost["execute_min_seconds"]):
        added = qualifying * seconds
        saved = downstream * seconds
        assert added / saved > 10


def test_the_ratio_is_the_same_at_every_price_point_because_the_cost_term_cancels():
    """
    `added / saved` is `qualifying_steps_with_satisfies /
    downstream_steps_after_earliest_failure` times the same per-call cost on
    both sides -- so the verdict does not depend on which of the fixture's
    three cost figures (mean, median, cheapest `execute` call observed)
    stands in for what a per-Step check-writing call would cost.
    """
    fixture = _fixture()
    measured, cost = fixture["measured_from"], fixture["cost"]
    expected = (measured["qualifying_steps_with_satisfies"]
               / measured["downstream_steps_after_earliest_failure"])
    for seconds in (cost["execute_mean_seconds"], cost["execute_median_seconds"],
                   cost["execute_min_seconds"]):
        added = measured["qualifying_steps_with_satisfies"] * seconds
        saved = measured["downstream_steps_after_earliest_failure"] * seconds
        assert abs(added / saved - expected) < 1e-9
