#!/usr/bin/env python
"""
The automatic sweep: every sheet against every model, resumable run by run,
each run `tools/run.py` spawned as its own subprocess so a run that goes
quiet is one process to kill and restart, not the whole lane. State is
written to `state.json` after every run, keyed `label/model`, `--resume`
reads it back and skips what is already `done`, and a run whose log has not
grown for `--hang-seconds` (default 1800) is killed and retried rather than
left to rot the lane overnight. `--status` prints `core.measure.compare`'s
table over the logs a lane run already wrote, without starting anything,
and any other count falls back to one `core.measure.measure` report per
log; `--report` prints the fuller Markdown document `report()` builds over
the same logs, one summary row per model and one detail row per run.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))
HANG_SECONDS = 1800
REASON_WIDTH = 60


def rotate(stem):
    """Move a previous attempt's log aside so a retry starts on an empty file.

    `EventStream.log_to` opens for append, so a retry -- or a run picked back
    up by `--resume` -- would otherwise write a second run into the first
    one's log, and `core.measure` would then read both as one.
    """
    log = stem + ".jsonl"
    if not os.path.exists(log):
        return
    index = 1
    while os.path.exists("%s.attempt%d.jsonl" % (stem, index)):
        index += 1
    os.rename(log, "%s.attempt%d.jsonl" % (stem, index))


def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    return {"runs": {}}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def watch(process, log, hang_seconds, run_seconds=None, poll_seconds=5,
          sleep=time.sleep, now=time.time):
    """Kill *process* once *log* has stopped growing for *hang_seconds*.

    Polls the log's size rather than the process's own liveness: a run that
    is alive but stuck talking to a provider looks identical to one working
    quietly on a big Step, and only silence on disk tells the two apart.

    *run_seconds* caps a run that never goes quiet: a gateway model's `cost`
    events carry no `usd`, so `--budget-usd` cannot trip on one.
    """
    last_size = -1
    started = last_change = now()
    while process.poll() is None:
        sleep(poll_seconds)
        size = os.path.getsize(log) if os.path.exists(log) else 0
        if size != last_size:
            last_size, last_change = size, now()
        elif now() - last_change > hang_seconds:
            process.kill()
            process.wait()
            return "hung"
        if run_seconds is not None and now() - started > run_seconds:
            process.kill()
            process.wait()
            return "over time"
    return "exited"


def run_one(label, sheet, model, effort, budget_usd, log, intent, provider,
            python=sys.executable):
    """
    One run of one sheet, as a subprocess of `tools/run.py`.

    A task with no sheet — `--task bolt-nut=` — is a run of the Intent alone,
    which is what a part named by a standard rather than drawn on a page is.
    """
    cmd = [python, os.path.join(ROOT, "tools", "run.py"),
          "--intent", intent, "--model", model,
          "--provider", provider, "--effort", effort, "--log", log]
    if sheet:
        cmd += ["--attach", sheet]
    if budget_usd is not None:
        cmd += ["--budget-usd", str(budget_usd)]
    return subprocess.Popen(cmd)


def baseline_tasks():
    """
    The 8 sheets pinned in `manual/modelmania/baseline_sheets.json`, each
    walked twice: two replicates of the one architecture on the stack today,
    reported as a spread rather than as an off/on comparison.
    """
    from tests import modelmania_harness

    return [("%s-rep%d" % (sheet.stem, rep), str(sheet))
            for sheet in modelmania_harness.baseline_sheets()
            for rep in (1, 2)]


def sweep(tasks, models, effort, budget_usd, intent, out, hang_seconds, retries,
          resume, provider, run_seconds):
    state_path = os.path.join(out, "state.json")
    state = load_state(state_path) if resume else {"runs": {}}

    for model in models:
        model_dir = os.path.join(out, model)
        os.makedirs(model_dir, exist_ok=True)
        for label, sheet in tasks:
            key = "%s/%s" % (label, model)
            if state["runs"].get(key, {}).get("status") == "done":
                print("SKIP %s: already done" % key, flush=True)
                continue

            stem = os.path.join(model_dir, label)
            log = stem + ".jsonl"
            outcome = "hung"
            for attempt in range(1, retries + 1):
                rotate(stem)
                process = run_one(label, sheet, model, effort, budget_usd, log, intent, provider)
                outcome = watch(process, log, hang_seconds, run_seconds)
                if outcome == "exited" and process.returncode == 0:
                    break
                print("%s attempt %d: %s (exit %s)"
                      % (key, attempt, outcome, process.returncode), flush=True)

            state["runs"][key] = {"label": label, "model": model, "log": log,
                                  "status": "done" if outcome == "exited"
                                  and process.returncode == 0 else "failed"}
            save_state(state_path, state)
            print("%s: %s" % (key, state["runs"][key]["status"]), flush=True)


def status(out):
    from core.measure import compare, measure

    models = sorted(d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d)))
    logs = {m: sorted(glob.glob(os.path.join(out, m, "*.jsonl"))) for m in models}

    if len(models) == 2:
        a, b = models
        print(compare(logs[a], logs[b], a, b))
        return

    for model in models:
        for log in logs[model]:
            reading = measure(log)
            print(model, log)
            for field in reading.__dataclass_fields__:
                print("  %s: %s" % (field, getattr(reading, field)))


def _logs(out):
    """(model, label, path), one per sweep log -- a rotated attempt is not the run."""
    models = sorted(d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d)))
    for model in models:
        for path in sorted(glob.glob(os.path.join(out, model, "*.jsonl"))):
            if ".attempt" in os.path.basename(path):
                continue
            yield model, os.path.basename(path)[: -len(".jsonl")], path


def _assertion_counts(events):
    from core.events import ASSERTION

    confirmed = sum(len(e.data.get("confirmed") or ()) for e in events if e.kind == ASSERTION)
    unmet = sum(len(e.data.get("unmet") or ()) for e in events if e.kind == ASSERTION)
    return confirmed, unmet


def _short(text):
    """One line, short enough not to break a Markdown table row."""
    text = " ".join(str(text).split())
    return text if len(text) <= REASON_WIDTH else text[:REASON_WIDTH - 1] + "..."


def _reason(events, m):
    from core.events import REVIEW_VERDICT

    verdicts = [e for e in events if e.kind == REVIEW_VERDICT]
    if verdicts:
        return _short(verdicts[-1].data.get("reason") or "")
    if m.blocked_reason:
        return _short(m.blocked_reason)
    return ""


def _rows(out):
    """(model, label, events, Measurement), one per sweep log under *out*."""
    from core.events import replay
    from core.measure import measure

    for model, label, path in _logs(out):
        events, _truncated = replay(path)
        yield model, label, events, measure(path)


def _summary_rows():
    from core.measure import _average, _pass_rate, _rate

    return (
        ("pass rate", _pass_rate, "{:.1%}"),
        ("blocked rate", lambda ms: _rate(ms, lambda m: m.blocked_reason is not None), "{:.1%}"),
        ("avg steps applied", lambda ms: _average(ms, lambda m: m.steps_applied), "{:.1f}"),
        ("assertion fail rate", lambda ms: _average(ms, lambda m: m.assertion_fail_rate), "{:.1%}"),
        ("review fail rate", lambda ms: _average(ms, lambda m: m.review_fail_rate), "{:.1%}"),
        ("avg cost (USD)", lambda ms: _average(ms, lambda m: m.cost_usd), "{:.4f}"),
        ("avg working (s)", lambda ms: _average(ms, lambda m: m.working_seconds), "{:.1f}"),
        ("avg questions", lambda ms: _average(ms, lambda m: m.question_count), "{:.1f}"),
    )


def _row(cells):
    return "| %s |" % " | ".join(cells)


def _summary_table(by_model):
    from core.measure import _pass_rate

    summary_rows = _summary_rows()
    header = ["model"] + [name for name, _, _ in summary_rows] + ["runs", "truncated"]
    lines = [_row(header), _row(["---"] * len(header))]
    ranked = sorted(by_model.items(), key=lambda kv: -_pass_rate(kv[1]))
    for model, measurements in ranked:
        cells = [model] + [fmt.format(compute(measurements)) for _, compute, fmt in summary_rows]
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
             "working (s)", "questions", "truncated"]
    lines = [_row(header), _row(["---"] * len(header))]
    for model, label, events, m in rows:
        confirmed, unmet = _assertion_counts(events)
        cells = [model, label, _verdict(events, m), _reason(events, m),
                "%d/%d" % (confirmed, unmet),
                "%d/%d" % (m.steps_started, m.steps_applied),
                str(m.preflight_failures), "{:.4f}".format(m.cost_usd),
                "{:.1f}".format(m.working_seconds), str(m.question_count),
                "yes" if m.truncated else "no"]
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
    parser.add_argument("--model", action="append", default=[],
                        help="Repeatable; each model sweeps every --task")
    parser.add_argument("--task", action="append", default=[],
                        help="label=/path/to/sheet.pdf, repeatable; an empty path attaches nothing")
    parser.add_argument("--intent", default="Build the Phase 1 model from this drawing.")
    parser.add_argument("--provider", default="Claude (subscription)",
                        help="Which provider sells every --model of this sweep")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--hang-seconds", type=float, default=HANG_SECONDS)
    parser.add_argument("--run-seconds", type=float, default=None,
                        help="Wall-clock ceiling on one run, whatever it is doing")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status", action="store_true",
                        help="Print the comparison table for --out and exit")
    parser.add_argument("--report", action="store_true",
                        help="Print the Markdown report for --out and exit")
    parser.add_argument("--save", help="Also write --report's Markdown to this path")
    parser.add_argument("--baseline", action="store_true",
                        help="Sweep the 8 pinned baseline sheets, 2 replicates each, over --model")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.status:
        status(args.out)
        return 0

    if args.report:
        text = report(args.out)
        print(text)
        if args.save:
            with open(args.save, "w", encoding="utf-8") as handle:
                handle.write(text)
        return 0

    if args.baseline:
        if len(args.model) != 1:
            raise SystemExit("--baseline takes exactly one --model, run twice as its own replicate")
        tasks = baseline_tasks()
    else:
        tasks = [t.split("=", 1) for t in args.task]
        if not tasks or not args.model:
            raise SystemExit("--status, --report, or --model and --task, each at least once")

    os.makedirs(args.out, exist_ok=True)
    sweep(tasks, args.model, args.effort, args.budget_usd, args.intent,
         args.out, args.hang_seconds, args.retries, args.resume, args.provider,
         args.run_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
