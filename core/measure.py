"""
Measure one Session log by the definitions the QA rounds used.

FCC-23/45/55/56 judged a run by whether it reached a Review verdict or was
blocked, how many Steps it started and how many it got applied, how many
times Preflight turned a Step back, how many times Plan was revised, what the
provider calls cost, and how long the run took wall clock. Every one of those
reads straight off the event stream `core/events.py :: replay` already
parses, so this module has no FreeCAD import and runs anywhere the Python is.

A run can carry more than one `review_verdict` (Review fails a Step, Plan
revises, Review runs again) or more than one `blocked` in principle, so both
read the last of their kind — the one that stands once the run stopped
emitting. A log with neither is a run still in the pipeline when the log
ends, not a failure of either kind: `verdict` and `blocked_reason` are both
`None`, read independently, since one run can have a `blocked` on an earlier
cycle and a `verdict` on a later one.

`cost_usd` only sums the `cost` events that carried a price: `core/events.py`
documents that a subscription route emits `cost` with no `usd` at all, so a
run entirely on a subscription costs 0.0 here, not "unknown".

FCC-191 also answers the two questions a baseline-against-experiment
comparison is actually asked for: how often the Assertion failed and how
often Review failed. Both read events `core/loop.py` already emits -
`assertion` (whose `ok` is `True`, `False`, or `None` when the phase gave up
without writing a check that ran, and whose `confirmed`/`unmet` name the
individual checks that phase wrote) and `review_verdict` (whose `verdict` is
always `"pass"` or `"fail"`).

`review_fail_rate` stays a fraction of that one run's own Review attempts,
not of the run set: a run whose first Review failed and whose second, after
a revision, passed carries `review_fail_rate` `0.5`, the same run
`plan_revisions` already counts as one hand-back.

`assertion_fail_rate` is a fraction of named checks, not of Assertion
attempts (FCC-191 review, `logs/review/ab-lane-outcomes.md` §5c): an
attempt's own `ok` is one boolean for the whole batch of checks it ran, and
a run with 12 of 110 named checks unmet came back with the identical
`ok: false` as one with 110 of 110 unmet, reading as the same `100%` either
way. Counting `confirmed` against `unmet` instead reads the first as ~11%
and the second as 100% - the difference the whole-batch boolean cannot
show, and the one number this metric now exists to tell apart.

A run with neither kind - blocked before Assertion ran, or before Review
did - reads both rates as `0.0`, the same way `hand-back rate` reads a run
with no `plan_revised` as not having handed back: nothing having happened is
not a failure, and counting it as one would make a short, clean run look
worse than a long one that failed once and then passed.
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
    every `assertion` event in the run — not the fraction of Assertion
    *attempts* that came back with one unmet entry among many.

    `opus/mm23-off` (FCC-191 review) is the run this replaced the old
    reading for: one `assertion` event, `ok: false`, 98 `confirmed` entries
    and 12 `unmet` ones. Read as one attempt out of one failed, that is
    100% — the same number a run with 0 of 110 checks passing reports. Read
    as checks, it is 12/110, about 11%: 89% of what the drawing asked for
    held, which the whole-batch `ok` boolean cannot say.

    `confirmed` and `unmet` are absent on the `ok: None` event a phase emits
    when it gave up without writing a check that ran (nothing measured,
    nothing to count either way) and on the envelope's own early failure
    (`unmet` only, `confirmed` always `[]` there because nothing but the
    envelope was checked) — `.get(..., ()) or ()` reads both as no checks
    rather than a crash.
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


def _average(measurements: List[Measurement], field) -> float:
    if not measurements:
        return 0.0
    return sum(field(m) for m in measurements) / len(measurements)


_ROWS = (
    ("pass rate", lambda ms: _rate(ms, lambda m: m.verdict == "pass"), "{:.1%}"),
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
    """
    A plain Markdown table, one row per FCC-23/45/55/56 metric, readable in a
    ticket comment.

    Takes Session log paths rather than `Measurement`s so a caller never has
    to import `measure` separately, and reports `n` runs per side because a
    rate over zero runs would otherwise read as a false 0%.
    """
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
