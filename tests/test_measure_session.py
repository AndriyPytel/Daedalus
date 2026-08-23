"""
Level 1 — measuring a Session log by the FCC-23/45/55/56 definitions.

Fixtures under `tests/recorded/sessions/` are real Session logs, copied in
whole from `logs/sessions/`, so the numbers below are read off an actual run
rather than invented.
"""
import json
import os

from core.events import EventStream
from core.loop import Budgets, run
from core.measure import Measurement, compare, measure
from core.traps import TrapStorage, TrapStore
from tests.test_loop import (
    ASSERT_FAILED, CHECKS, FakeTools, a_run_over_the_nut, assertion_failed,
    assertion_reply, code_reply, plan_reply, planning_the_nut, scripted, verdict_reply,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "recorded", "sessions")


def _fixture(name):
    return os.path.join(FIXTURES, name)


def test_a_run_that_reached_a_verdict():
    result = measure(_fixture("reached_verdict.jsonl"))

    assert result.verdict == "pass"
    assert result.blocked_reason is None
    assert result.steps_started == 3
    assert result.steps_applied == 3
    assert result.plan_revisions == 0
    assert result.preflight_failures == 0
    assert result.cost_usd == 0
    assert result.wall_clock_seconds == 137.2473270893097
    assert result.truncated is False


def test_a_run_that_was_blocked():
    """Cost is read alongside blocked: a run can spend money and still not finish."""
    result = measure(_fixture("blocked.jsonl"))

    assert result.verdict is None
    assert result.blocked_reason == (
        "Stopped by you. Everything applied so far is still in the Document; "
        "undo it Step by Step if you want it gone."
    )
    assert result.steps_started == 0
    assert result.cost_usd == 0.014854499999999998
    assert result.wall_clock_seconds == 8.653377056121826
    assert result.truncated is False


def test_a_run_with_a_plan_revision_reads_the_last_verdict():
    """Review failed, Plan revised, Review passed: the last verdict is what stands."""
    result = measure(_fixture("plan_revision.jsonl"))

    assert result.verdict == "pass"
    assert result.steps_started == 3
    assert result.steps_applied == 3
    assert result.plan_revisions == 1
    assert result.preflight_failures == 2


def test_a_run_with_preflight_failures_and_no_verdict_yet():
    """Still mid-Step when the log ends: neither a verdict nor a blocked reason."""
    result = measure(_fixture("preflight_failures.jsonl"))

    assert result.verdict is None
    assert result.blocked_reason is None
    assert result.steps_started == 1
    assert result.steps_applied == 0
    assert result.preflight_failures == 2


def test_a_truncated_log_is_measured_from_what_survived_the_cut():
    """The last line was cut mid-write; `replay` drops it and says so."""
    result = measure(_fixture("truncated.jsonl"))

    assert result.truncated is True
    assert result.steps_started == 1
    assert result.preflight_failures == 2
    assert result.verdict is None


def test_a_measurement_is_a_plain_dataclass():
    result = measure(_fixture("reached_verdict.jsonl"))
    assert isinstance(result, Measurement)


def _cells(table, metric):
    row = next(line for line in table.splitlines() if line.split("|")[1].strip() == metric)
    return [cell.strip() for cell in row.split("|")[2:4]]


def test_compare_reports_a_regression_honestly():
    """
    A run that reached a verdict against a run that was blocked: the
    comparison must not hide the blocked side behind a rounded average.
    """
    table = compare(
        [_fixture("reached_verdict.jsonl")], [_fixture("blocked.jsonl")],
        baseline_label="main", experiment_label="rlm-version",
    )

    assert "main (n=1)" in table
    assert "rlm-version (n=1)" in table
    assert _cells(table, "pass rate") == ["100.0%", "0.0%"]
    assert _cells(table, "blocked rate") == ["0.0%", "100.0%"]


def test_compare_with_no_runs_on_one_side_is_zero_not_a_crash():
    table = compare([], [_fixture("reached_verdict.jsonl")])

    assert "n=0" in table
    assert "n=1" in table


def test_a_run_reports_the_traps_it_proposed_approved_and_declined():
    """Three proposed, one of them approved and a different one declined."""
    result = measure(_fixture("traps.jsonl"))

    assert len(result.traps_proposed) == 3
    assert "A Sweep along a datum line loses its Twist setting on recompute" \
        in result.traps_proposed
    assert result.traps_approved == (
        "A Sweep along a datum line loses its Twist setting on recompute",
    )
    assert result.traps_declined == (
        "A Draft Mirror across a non-planar face silently produces no shape",
    )


def test_a_run_with_no_trap_events_reports_empty_tuples():
    result = measure(_fixture("reached_verdict.jsonl"))

    assert result.traps_proposed == ()
    assert result.traps_approved == ()
    assert result.traps_declined == ()


def test_a_real_run_that_proposes_a_trap_is_measured_with_it(tmp_path):
    """
    FCC-191's own gap: a Session log came off `core/loop.py :: run` itself,
    not a hand-built `EventStream`, so this is the one test in the suite
    that proves the store `run` now opens actually reaches this module's
    numbers rather than only the `TrapStore` unit tests it was checked
    against.
    """
    log_path = str(tmp_path / "run.jsonl")
    events = EventStream()
    events.log_to(log_path)
    traps = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")),
                      events=events)
    trap = json.dumps({"trap": {
        "description": "Draft cube() twice loses the second body silently",
        "evidence": "Step 2 log: two Part::Box named Box, only one visible",
    }})
    llm = scripted([trap, plan_reply("Add a box"),
                    code_reply("doc.addObject('Part::Box', 'Box')"), verdict_reply("pass")])
    tools = FakeTools()

    result = run("Make a box", llm=llm, tools=tools.registry, events=events,
                budgets=Budgets(), traps=traps)

    assert result.ok
    measured = measure(log_path)
    assert measured.traps_proposed == (
        "Draft cube() twice loses the second body silently",
    )


def test_compare_reports_the_trap_proposal_rate():
    table = compare(
        [_fixture("reached_verdict.jsonl")], [_fixture("traps.jsonl")],
        baseline_label="main", experiment_label="rlm-version",
    )

    assert _cells(table, "trap proposal rate") == ["0.0%", "100.0%"]


def test_a_run_with_no_assertions_or_review_verdicts_has_zero_fail_rates():
    """Blocked before either phase ran: both are 0.0, not an undefined rate."""
    result = measure(_fixture("blocked.jsonl"))

    assert result.assertion_fail_rate == 0.0
    assert result.review_fail_rate == 0.0


def test_a_run_with_no_assertions_but_a_verdict_reads_only_the_verdict():
    result = measure(_fixture("reached_verdict.jsonl"))

    assert result.assertion_fail_rate == 0.0
    assert result.review_fail_rate == 0.0


def test_a_run_with_a_failed_assertion_that_passed_on_retry(tmp_path):
    """
    One failed check, replanned, one that passed: half of this run's own
    Assertion attempts failed. Built through `core.loop.run` itself, the way
    FCC-191's trap test is, rather than hand-written JSON, because nothing in
    `tests/recorded/sessions` carries a failed `assertion` event.
    """
    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        assertion_reply(CHECKS),
        planning_the_nut("Make it 13 mm across the flats"),
        code_reply("fix()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[assertion_failed(ASSERT_FAILED), None])

    log_path = str(tmp_path / "run.jsonl")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(events.as_log())

    result = measure(log_path)

    assert result.assertion_fail_rate == 0.5
    assert result.review_fail_rate == 0.0


def test_a_run_with_a_failed_review_that_passed_on_revision_reads_the_mix():
    """Review failed once and passed once in this same run: half its verdicts failed."""
    result = measure(_fixture("plan_revision.jsonl"))

    assert result.review_fail_rate == 0.5


def test_compare_reports_the_assertion_and_review_fail_rates():
    table = compare(
        [_fixture("reached_verdict.jsonl")], [_fixture("plan_revision.jsonl")],
        baseline_label="main", experiment_label="rlm-version",
    )

    assert _cells(table, "assertion fail rate") == ["0.0%", "0.0%"]
    assert _cells(table, "review fail rate") == ["0.0%", "50.0%"]
