"""
Level 1 — measuring a Session log by the FCC-23/45/55/56 definitions.

Fixtures under `tests/recorded/sessions/` are real Session logs, copied in
whole from `logs/sessions/`, so the numbers below are read off an actual run
rather than invented.
"""
import os

import pytest

from core.measure import Measurement, compare, measure
from core.events import REVIEW_VERDICT
from tests.test_loop import (
    ASSERT_FAILED, CHECKS, a_run_over_the_nut, assertion_failed,
    assertion_reply, code_reply, planning_the_nut, suspect_reply, verdict_reply,
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
    assert result.gate_wait_seconds == 0.0
    assert result.working_seconds == result.wall_clock_seconds
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
    carries a failed `assertion` event. `result.ok` and a logged
    `review_verdict` event guard against a script that scores a rate of
    0.0 for the wrong reason — an aborted run with zero verdicts logged
    reads identically to a run where Review genuinely never failed.
    """
    result, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        planning_the_nut("Make it 13 mm across the flats"),
        code_reply("fix()"),
        suspect_reply(),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[assertion_failed(ASSERT_FAILED), None])

    assert result.ok
    assert events.first(REVIEW_VERDICT) is not None

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


def test_review_suspected_never_counts_toward_the_review_fail_rate(tmp_path):
    """
    `review_suspected` is `review_suspect`'s own event, not a verdict — it never
    gates and it is not what `review_fail_rate` measures. A log with more
    `review_suspected` events than passing `review_verdict` ones must still
    read a fail rate of 0%, or the two phases FCC-258 split apart would be
    read back together (FCC-257, FCC-258).
    """
    from core.events import REVIEW_SUSPECTED, REVIEW_VERDICT, EventStream

    events = EventStream()
    events.emit(REVIEW_SUSPECTED, suspected=["envelope → width: 40 mm (suspected)"])
    events.emit(REVIEW_SUSPECTED, suspected=["envelope → width: 40 mm (suspected)"])
    events.emit(REVIEW_VERDICT, verdict="pass", reason="matches the Intent")

    log_path = str(tmp_path / "run.jsonl")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(events.as_log())

    result = measure(log_path)

    assert result.review_fail_rate == 0.0


def test_compare_reports_the_assertion_and_review_fail_rates():
    table = compare(
        [_fixture("reached_verdict.jsonl")], [_fixture("plan_revision.jsonl")],
        baseline_label="main", experiment_label="rlm-version",
    )

    assert _cells(table, "assertion fail rate") == ["0.0%", "0.0%"]
    assert _cells(table, "review fail rate") == ["0.0%", "50.0%"]
def test_assertion_fail_rate_counts_named_checks_not_whole_batches(tmp_path):
    """
    `opus/mm23-off` (FCC-191 review): one `assertion` event, `ok: false`,
    98 `confirmed` entries and 12 `unmet` ones — 110 named checks, 89% of
    them held. The event-level reading was `1/1 = 100%`; counting checks
    instead reads the same run as ~10.9% failed, not a total loss.
    """
    from core.events import ASSERTION, EventStream

    events = EventStream()
    events.emit(ASSERTION, ok=False, code="checks()",
                confirmed=["check %d" % i for i in range(98)],
                uncovered=[], traceback="AssertionError",
                unmet=["check %d" % i for i in range(98, 110)])

    log_path = str(tmp_path / "run.jsonl")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(events.as_log())

    result = measure(log_path)

    assert result.assertion_fail_rate == pytest.approx(12 / 110)


def test_assertion_fail_rate_counts_named_checks_not_whole_batches(tmp_path):
    """
    `opus/mm23-off` (FCC-191 review): one `assertion` event, `ok: false`,
    98 `confirmed` entries and 12 `unmet` ones — 110 named checks, 89% of
    them held. The event-level reading was `1/1 = 100%`; counting checks
    instead reads the same run as ~10.9% failed, not a total loss.
    """
    from core.events import ASSERTION, EventStream

    events = EventStream()
    events.emit(ASSERTION, ok=False, code="checks()",
                confirmed=["check %d" % i for i in range(98)],
                uncovered=[], traceback="AssertionError",
                unmet=["check %d" % i for i in range(98, 110)])

    log_path = str(tmp_path / "run.jsonl")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(events.as_log())

    result = measure(log_path)

    assert result.assertion_fail_rate == pytest.approx(12 / 110)


def _write_gate_log(tmp_path, gate_pairs):
    """
    *gate_pairs* is a list of ("asked", at) / ("decision", at) tuples in
    stream order; an "asked" with no matching "decision" is left dangling on
    purpose, the way a run cut off mid-wait leaves its log.
    """
    from core.events import Event, GATE, INTENT

    events = [Event(kind=INTENT, data={"text": "a nut"}, at=gate_pairs[0][1] - 1.0)]
    for kind, at in gate_pairs:
        data = {"steps": 1, "asked": True} if kind == "asked" else {"decision": "approve"}
        events.append(Event(kind=GATE, data=data, at=at))

    log_path = str(tmp_path / "run.jsonl")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(event.as_json() for event in events))
    return log_path


def test_gate_wait_is_excluded_from_working_time(tmp_path):
    """
    12:45:57 asked, 13:40:09 decided — 54 minutes of a 124 minute run spent
    waiting on the user's plan approval (`logs/sessions/90de8d14...`). Wall
    clock keeps the whole 54 minutes; working time does not.
    """
    log_path = _write_gate_log(tmp_path, [("asked", 100.0), ("decision", 3340.0)])

    result = measure(log_path)

    assert result.gate_wait_seconds == pytest.approx(3240.0)
    assert result.working_seconds == pytest.approx(result.wall_clock_seconds - 3240.0)


def test_gate_wait_sums_across_multiple_asks_in_one_run(tmp_path):
    log_path = _write_gate_log(tmp_path, [
        ("asked", 100.0), ("decision", 130.0),
        ("asked", 200.0), ("decision", 260.0),
    ])

    result = measure(log_path)

    assert result.gate_wait_seconds == pytest.approx(30.0 + 60.0)


def test_a_run_cut_off_mid_wait_does_not_crash_or_go_negative(tmp_path):
    """A trailing `asked` with no `decision` after it: the run's log ends there."""
    log_path = _write_gate_log(tmp_path, [
        ("asked", 100.0), ("decision", 130.0),
        ("asked", 200.0),
    ])

    result = measure(log_path)

    assert result.gate_wait_seconds == pytest.approx(30.0)
    assert result.gate_wait_seconds >= 0
    assert result.working_seconds >= 0
