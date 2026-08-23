"""
Level 1 — measuring a Session log by the FCC-23/45/55/56 definitions.

Fixtures under `tests/recorded/sessions/` are real Session logs, copied in
whole from `logs/sessions/`, so the numbers below are read off an actual run
rather than invented.
"""
import os

from core.measure import Measurement, compare, measure
from tests.test_loop import (
    ASSERT_FAILED, CHECKS, a_run_over_the_nut, assertion_failed,
    assertion_reply, code_reply, planning_the_nut, verdict_reply,
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
    Assertion attempts failed. Built through `core.loop.run` itself rather
    than hand-written JSON, because nothing in `tests/recorded/sessions`
    carries a failed `assertion` event.
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
