"""
Markdown report over one lane directory's model sweep: one summary row per
model, one detail row per (model, sheet), all built on `core.measure` and
`core.events.replay` -- this module only reads a lane, never writes one.
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.events import ASSERTION, REVIEW_VERDICT, replay
from core.measure import _average, _pass_rate, _rate, measure

REASON_WIDTH = 60


def _logs(out):
    """(model, label, path), one per sweep log -- a rotated attempt is not the run."""
    models = sorted(d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d)))
    for model in models:
        for path in sorted(glob.glob(os.path.join(out, model, "*.jsonl"))):
            if ".attempt" in os.path.basename(path):
                continue
            yield model, os.path.basename(path)[: -len(".jsonl")], path


def _assertion_counts(events):
    confirmed = sum(len(e.data.get("confirmed") or ()) for e in events if e.kind == ASSERTION)
    unmet = sum(len(e.data.get("unmet") or ()) for e in events if e.kind == ASSERTION)
    return confirmed, unmet


def _short(text):
    """One line, short enough not to break a Markdown table row."""
    text = " ".join(str(text).split())
    return text if len(text) <= REASON_WIDTH else text[:REASON_WIDTH - 1] + "..."


def _reason(events, m):
    verdicts = [e for e in events if e.kind == REVIEW_VERDICT]
    if verdicts:
        return _short(verdicts[-1].data.get("reason") or "")
    if m.blocked_reason:
        return _short(m.blocked_reason)
    return ""


def _rows(out):
    """(model, label, events, Measurement), one per sweep log under *out*."""
    for model, label, path in _logs(out):
        events, _truncated = replay(path)
        yield model, label, events, measure(path)


_SUMMARY_ROWS = (
    ("pass rate", _pass_rate, "{:.1%}"),
    ("blocked rate", lambda ms: _rate(ms, lambda m: m.blocked_reason is not None), "{:.1%}"),
    ("avg steps applied", lambda ms: _average(ms, lambda m: m.steps_applied), "{:.1f}"),
    ("assertion fail rate", lambda ms: _average(ms, lambda m: m.assertion_fail_rate), "{:.1%}"),
    ("review fail rate", lambda ms: _average(ms, lambda m: m.review_fail_rate), "{:.1%}"),
    ("avg cost (USD)", lambda ms: _average(ms, lambda m: m.cost_usd), "{:.4f}"),
    ("avg working (s)", lambda ms: _average(ms, lambda m: m.working_seconds), "{:.1f}"),
)


def _row(cells):
    return "| %s |" % " | ".join(cells)


def _summary_table(by_model):
    header = ["model"] + [name for name, _, _ in _SUMMARY_ROWS] + ["runs", "truncated"]
    lines = [_row(header), _row(["---"] * len(header))]
    ranked = sorted(by_model.items(), key=lambda kv: -_pass_rate(kv[1]))
    for model, measurements in ranked:
        cells = [model] + [fmt.format(compute(measurements)) for _, compute, fmt in _SUMMARY_ROWS]
        cells += [str(len(measurements)), str(sum(1 for m in measurements if m.truncated))]
        lines.append(_row(cells))
    return "\n".join(lines)


def _verdict(events, m):
    if not events:
        return "failed to start"
    if m.verdict:
        return m.verdict
    if m.blocked_reason:
        return "blocked"
    return "-"


def _detail_table(rows):
    header = ["model", "sheet", "verdict", "reason", "confirmed/unmet",
             "steps started/applied", "preflight fails", "cost (USD)",
             "working (s)", "truncated"]
    lines = [_row(header), _row(["---"] * len(header))]
    for model, label, events, m in rows:
        confirmed, unmet = _assertion_counts(events)
        cells = [model, label, _verdict(events, m), _reason(events, m),
                "%d/%d" % (confirmed, unmet),
                "%d/%d" % (m.steps_started, m.steps_applied),
                str(m.preflight_failures), "{:.4f}".format(m.cost_usd),
                "{:.1f}".format(m.working_seconds), "yes" if m.truncated else "no"]
        lines.append(_row(cells))
    return "\n".join(lines)


def report(out):
    """The whole Markdown document for the lane directory *out*."""
    rows = list(_rows(out))
    by_model = {}
    for model, _label, _events, m in rows:
        by_model.setdefault(model, []).append(m)
    if not by_model:
        return "# %s\n\nno sweep logs found\n" % out
    return "\n\n".join([
        "# %s" % out,
        "## Summary",
        _summary_table(by_model),
        "## Detail",
        _detail_table(rows),
    ]) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--save")
    args = parser.parse_args()

    text = report(args.out)
    print(text)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as handle:
            handle.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
