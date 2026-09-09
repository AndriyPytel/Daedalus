"""
Measure one Session log by the definitions the QA rounds used.

`review_fail_rate` stays a fraction of that one run's own Review attempts.
`assertion_fail_rate` is a fraction of named checks, not of Assertion
attempts: a run with 12 of 110 named checks unmet reads as ~11%, and one
with 110 of 110 unmet reads as 100%.
"""
from dataclasses import dataclass
from typing import List, Optional

from core.events import (
    APPLIED, ASSERTION, BLOCKED, COST, GATE, PLAN_REVISED, PREFLIGHT_FAILED,
    REVIEW_VERDICT, STEP_STARTED, replay,
)


@dataclass(frozen=True)
class Measurement:
    verdict: Optional[str]
    blocked_reason: Optional[str]
    steps_started: int
    steps_applied: int
    plan_revisions: int
    preflight_failures: int
    cost_usd: float
    wall_clock_seconds: float
    gate_wait_seconds: float
    working_seconds: float
    truncated: bool
    assertion_fail_rate: float
    review_fail_rate: float


def _fail_rate(events, is_failure) -> float:
    if not events:
        return 0.0
    return sum(1 for event in events if is_failure(event)) / len(events)


def _gate_wait_seconds(events) -> float:
    """
    Time spent sitting at the Plan Gate waiting on the user, summed across
    every ask this run made.

    Paired off the `gate` events `core/loop.py :: _run` already emits: one
    with `asked` and no `decision` opens the wait, the next with a `decision`
    closes it. A trailing open with no closing event — the run was cut off
    mid-wait — is dropped rather than counted, so a truncated log never reads
    a negative or unbounded wait.
    """
    total = 0.0
    opened = None
    for event in events:
        if event.kind != GATE:
            continue
        if "decision" in event.data:
            if opened is not None:
                total += event.at - opened
                opened = None
        else:
            opened = event.at
    return total


def _checks_fail_rate(assertions) -> float:
    """
    The fraction of individually named checks that did not hold, across
    every `assertion` event in the run.

    One run: 98 `confirmed` and 12 `unmet`. Read as checks, it is 12/110,
    about 11%: 89% held, which the whole-batch `ok` boolean cannot say.
    """
    unmet = sum(len(event.data.get("unmet") or ()) for event in assertions)
    confirmed = sum(len(event.data.get("confirmed") or ()) for event in assertions)
    total = unmet + confirmed
    return unmet / total if total else 0.0


def measure(path: str) -> Measurement:
    events, truncated = replay(path)
    verdicts = [event for event in events if event.kind == REVIEW_VERDICT]
    blocks = [event for event in events if event.kind == BLOCKED]
    assertions = [event for event in events if event.kind == ASSERTION]
    wall_clock_seconds = (events[-1].at - events[0].at) if len(events) >= 2 else 0.0
    gate_wait_seconds = _gate_wait_seconds(events)
    return Measurement(
        verdict=verdicts[-1].data.get("verdict") if verdicts else None,
        blocked_reason=blocks[-1].data.get("reason") if blocks else None,
        steps_started=sum(1 for event in events if event.kind == STEP_STARTED),
        steps_applied=sum(1 for event in events if event.kind == APPLIED),
        plan_revisions=sum(1 for event in events if event.kind == PLAN_REVISED),
        preflight_failures=sum(1 for event in events if event.kind == PREFLIGHT_FAILED),
        cost_usd=sum(event.data.get("usd") or 0 for event in events if event.kind == COST),
        wall_clock_seconds=wall_clock_seconds,
        gate_wait_seconds=gate_wait_seconds,
        working_seconds=wall_clock_seconds - gate_wait_seconds,
        truncated=truncated,
        assertion_fail_rate=_checks_fail_rate(assertions),
        review_fail_rate=_fail_rate(verdicts, lambda event: event.data.get("verdict") == "fail"),
    )


def _rate(measurements: List[Measurement], predicate) -> float:
    if not measurements:
        return 0.0
    return sum(1 for m in measurements if predicate(m)) / len(measurements)


def _pass_rate(measurements: List[Measurement]) -> float:
    """
    The fraction of runs Review passed, of the runs Review actually reached.

    `verdict is None` is Review never running, not Review saying no.
    """
    reviewed = [m for m in measurements if m.verdict is not None]
    if not reviewed:
        return 0.0
    return sum(1 for m in reviewed if m.verdict == "pass") / len(reviewed)


def _average(measurements: List[Measurement], field) -> float:
    if not measurements:
        return 0.0
    return sum(field(m) for m in measurements) / len(measurements)


_ROWS = (
    ("pass rate", _pass_rate, "{:.1%}"),
    ("blocked rate", lambda ms: _rate(ms, lambda m: m.blocked_reason is not None), "{:.1%}"),
    ("avg Steps started", lambda ms: _average(ms, lambda m: m.steps_started), "{:.1f}"),
    ("avg Steps applied", lambda ms: _average(ms, lambda m: m.steps_applied), "{:.1f}"),
    ("hand-back rate", lambda ms: _rate(ms, lambda m: m.plan_revisions > 0), "{:.1%}"),
    ("avg Preflight failures", lambda ms: _average(ms, lambda m: m.preflight_failures), "{:.1f}"),
    ("avg cost (USD)", lambda ms: _average(ms, lambda m: m.cost_usd), "{:.4f}"),
    ("avg working time (s)", lambda ms: _average(ms, lambda m: m.working_seconds), "{:.1f}"),
    ("avg wall time (s)", lambda ms: _average(ms, lambda m: m.wall_clock_seconds), "{:.1f}"),
    ("assertion fail rate", lambda ms: _average(ms, lambda m: m.assertion_fail_rate), "{:.1%}"),
    ("review fail rate", lambda ms: _average(ms, lambda m: m.review_fail_rate), "{:.1%}"),
)


def compare(baseline: List[str], experiment: List[str],
           baseline_label: str = "baseline", experiment_label: str = "experiment") -> str:
    """A plain Markdown table, one row per metric, readable in a ticket comment."""
    baseline_runs = [measure(path) for path in baseline]
    experiment_runs = [measure(path) for path in experiment]

    lines = [
        "| metric | %s (n=%d) | %s (n=%d) |" % (
            baseline_label, len(baseline_runs), experiment_label, len(experiment_runs)),
        "| --- | --- | --- |",
    ]
    for name, compute, fmt in _ROWS:
        lines.append("| %s | %s | %s |" % (
            name, fmt.format(compute(baseline_runs)), fmt.format(compute(experiment_runs))))
    return "\n".join(lines)
