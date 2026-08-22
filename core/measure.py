"""
Measure one Session log by the definitions the QA rounds used.

FCC-23/45/55/56 judged a run by whether it reached a Review verdict or was
blocked, how many Steps it started and how many it got applied, how many
times Preflight turned a Step back, how many times Plan was revised, what the
provider calls cost, and how long the run took wall clock. Every one of those
reads straight off the event stream `core/events.py :: replay` already
parses, so this module has no FreeCAD import and runs anywhere the Python is.

FCC-191 adds what `core/traps.py` proposed, approved and declined on this
run: the descriptions, not just a count, since "which traps" is the question
a ticket comment needs answered, not only "how many". A `TrapStore` built
with no `EventStream` (FCC-184's own tests do this) leaves a Session log with
none of these three kinds, so a run measured from that log reports empty
tuples rather than a crash.

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
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.events import (
    APPLIED, BLOCKED, COST, PLAN_REVISED, PREFLIGHT_FAILED, REVIEW_VERDICT,
    STEP_STARTED, TRAP_APPROVED, TRAP_DECLINED, TRAP_PROPOSED, replay,
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
    truncated: bool
    traps_proposed: Tuple[str, ...]
    traps_approved: Tuple[str, ...]
    traps_declined: Tuple[str, ...]


def measure(path: str) -> Measurement:
    events, truncated = replay(path)
    verdicts = [event for event in events if event.kind == REVIEW_VERDICT]
    blocks = [event for event in events if event.kind == BLOCKED]
    return Measurement(
        verdict=verdicts[-1].data.get("verdict") if verdicts else None,
        blocked_reason=blocks[-1].data.get("reason") if blocks else None,
        steps_started=sum(1 for event in events if event.kind == STEP_STARTED),
        steps_applied=sum(1 for event in events if event.kind == APPLIED),
        plan_revisions=sum(1 for event in events if event.kind == PLAN_REVISED),
        preflight_failures=sum(1 for event in events if event.kind == PREFLIGHT_FAILED),
        cost_usd=sum(event.data.get("usd") or 0 for event in events if event.kind == COST),
        wall_clock_seconds=(events[-1].at - events[0].at) if len(events) >= 2 else 0.0,
        truncated=truncated,
        traps_proposed=tuple(event.data.get("description") for event in events
                             if event.kind == TRAP_PROPOSED),
        traps_approved=tuple(event.data.get("description") for event in events
                             if event.kind == TRAP_APPROVED),
        traps_declined=tuple(event.data.get("description") for event in events
                             if event.kind == TRAP_DECLINED),
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
    ("avg run time (s)", lambda ms: _average(ms, lambda m: m.wall_clock_seconds), "{:.1f}"),
    ("trap proposal rate", lambda ms: _rate(ms, lambda m: len(m.traps_proposed) > 0), "{:.1%}"),
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
