"""
Measure one Session log by the definitions the QA rounds used.

`review_fail_rate` stays a fraction of that one run's own Review attempts.
`assertion_fail_rate` is a fraction of named checks, not of Assertion
attempts: a run with 12 of 110 named checks unmet reads as ~11%, and one
with 110 of 110 unmet reads as 100%.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from core.events import (
    APPLIED, ASSERTION, BLOCKED, COST, DESIGN, GATE, PLAN_REVISED, PREFLIGHT_FAILED,
    QUESTION, REVIEW_VERDICT, STEP_DECLARED, STEP_STARTED, replay,
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
    question_count: int
    score: Optional[float] = None
    not_defined_count: int = 0
    conflicted_count: int = 0
    preflight_by_gate: dict = field(default_factory=dict)


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


def _the_part(events):
    """
    The settled part from this run's last `design` event, or `{}` where
    Design never emitted one -- `not_defined_count`/`conflicted_count` read
    `not_defined` and `conflicts` off this same document, not a second pass
    over the sheet.
    """
    designs = [event for event in events if event.kind == DESIGN]
    return (designs[-1].data.get("model") or {}) if designs else {}


def _not_defined_count(the_part) -> int:
    return len(the_part.get("not_defined") or ())


def _conflicted_count(the_part) -> int:
    return len(the_part.get("conflicts") or ())


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


def _preflight_by_gate(events) -> dict:
    """
    Per-gate count of `PREFLIGHT_FAILED` events, `gate` values as the keys.
    A drift recorded before it carried a gate is recognised by its own
    traceback; any other event with no `gate` is not counted under any key.
    """
    counts = {}
    for event in events:
        if event.kind != PREFLIGHT_FAILED:
            continue
        gate = event.data.get("gate") or (
            "drift" if "signature did not match what the kernel verified"
            in (event.data.get("traceback") or "") else None)
        if gate:
            counts[gate] = counts.get(gate, 0) + 1
    return counts


def measure(path: str, score: Optional[float] = None) -> Measurement:
    """
    One run's process metrics from its Session log, plus *score* -- the
    Score a Document this run produced got against its Exemplar, when the
    caller already computed one (`tools/lane.py`'s report does, by name).
    """
    events, truncated = replay(path)
    verdicts = [event for event in events if event.kind == REVIEW_VERDICT]
    blocks = [event for event in events if event.kind == BLOCKED]
    assertions = [event for event in events if event.kind == ASSERTION]
    the_part = _the_part(events)
    wall_clock_seconds = (events[-1].at - events[0].at) if len(events) >= 2 else 0.0
    gate_wait_seconds = _gate_wait_seconds(events)
    return Measurement(
        verdict=verdicts[-1].data.get("verdict") if verdicts else None,
        blocked_reason=blocks[-1].data.get("reason") if blocks else None,
        steps_started=len({event.data.get("index") or event.data.get("step")
                            for event in events if event.kind in (STEP_STARTED, STEP_DECLARED)}),
        steps_applied=sum(1 for event in events if event.kind == APPLIED),
        plan_revisions=sum(1 for event in events if event.kind == PLAN_REVISED),
        preflight_failures=sum(1 for event in events if event.kind == PREFLIGHT_FAILED),
        preflight_by_gate=_preflight_by_gate(events),
        cost_usd=sum(event.data.get("usd") or 0 for event in events if event.kind == COST),
        wall_clock_seconds=wall_clock_seconds,
        gate_wait_seconds=gate_wait_seconds,
        working_seconds=wall_clock_seconds - gate_wait_seconds,
        not_defined_count=_not_defined_count(the_part),
        conflicted_count=_conflicted_count(the_part),
        truncated=truncated,
        assertion_fail_rate=_checks_fail_rate(assertions),
        review_fail_rate=_fail_rate(verdicts, lambda event: event.data.get("verdict") == "fail"),
        question_count=sum(1 for event in events if event.kind == QUESTION),
        score=score,
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


def score_spread(measurements: List[Measurement]) -> Optional[float]:
    """
    `max - min` of the runs' own Score, or `None` under two of them --
    the smallest difference a later comparison of the same sheet can trust
    is one repeat's own spread, and a spread needs at least two readings.
    """
    scored = [m.score for m in measurements if m.score is not None]
    if len(scored) < 2:
        return None
    return max(scored) - min(scored)


def _score_average(measurements: List[Measurement]) -> float:
    scored = [m.score for m in measurements if m.score is not None]
    return sum(scored) / len(scored) if scored else 0.0


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
    ("avg question count", lambda ms: _average(ms, lambda m: m.question_count), "{:.1f}"),
    ("avg not_defined count", lambda ms: _average(ms, lambda m: m.not_defined_count), "{:.1f}"),
    ("avg conflicted count", lambda ms: _average(ms, lambda m: m.conflicted_count), "{:.1f}"),
    ("avg score", _score_average, "{:.4f}"),
)


def compare(baseline: List[str], experiment: List[str],
           baseline_label: str = "baseline", experiment_label: str = "experiment",
           score_floor: Optional[float] = None) -> str:
    """
    A plain Markdown table, one row per metric, readable in a ticket comment.

    *score_floor* is the noise floor `tools/lane.py --pinned --report`
    measured -- a Score difference smaller than it is marked unmeasurable
    instead of printed as though it were a real change.
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

    if score_floor is not None:
        diff = _score_average(experiment_runs) - _score_average(baseline_runs)
        if abs(diff) < score_floor:
            lines.append("")
            lines.append("Score moved by %.4f, smaller than the noise floor %.4f -- "
                         "unmeasurable." % (diff, score_floor))
        else:
            lines.append("")
            lines.append("Score moved by %.4f, larger than the noise floor %.4f." % (diff, score_floor))
    return "\n".join(lines)
