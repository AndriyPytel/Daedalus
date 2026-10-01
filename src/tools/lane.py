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
import datetime
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


def _attempt_timestamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _unique_stem(stem, stamp):
    """`<stem>.<stamp>`, or that with a counter suffix if a run already
    claimed it -- two attempts a real clock or an injected *timestamp*
    both gave the same stamp must still land on different files."""
    candidate = "%s.%s" % (stem, stamp)
    index = 2
    while os.path.exists(candidate + ".jsonl"):
        candidate = "%s.%s-%d" % (stem, stamp, index)
        index += 1
    return candidate


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
            python=sys.executable, conflicts=None):
    """
    One run of one sheet, as a subprocess of `tools/run.py`.

    A task with no sheet — `--task bolt-nut=` — is a run of the Intent alone,
    which is what a part named by a standard rather than drawn on a page is.
    *conflicts* is `tools/run.py --conflicts`, unset runs its default ("on").
    """
    save = log[: -len(".jsonl")] + ".step" if log.endswith(".jsonl") else log + ".step"
    cmd = [python, os.path.join(ROOT, "tools", "run.py"),
          "--intent", intent, "--model", model,
          "--provider", provider, "--effort", effort, "--log", log, "--save", save]
    if sheet:
        cmd += ["--attach", sheet]
    if budget_usd is not None:
        cmd += ["--budget-usd", str(budget_usd)]
    if conflicts is not None:
        cmd += ["--conflicts", conflicts]
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


def pinned_tasks():
    """
    The six sheets in `manual/modelmania/phases/`, three repeats each -- the
    suite this repo measures itself against overnight. Three repeats is the
    point: four headless runs of one sheet took 27.4, 32.5, 66.1 and 122.4
    minutes on identical input, and one repeat would report that spread as
    a fact about the sheet rather than about the run.
    """
    from tests import modelmania_harness

    return [("%s-rep%d" % (sheet.stem, rep), str(sheet))
            for sheet in modelmania_harness.PHASES.values()
            for rep in (1, 2, 3)]


SMOKE_SHEET_DEFAULT = "mm2025-phase1.pdf"


def smoke_tasks(sheet_name=SMOKE_SHEET_DEFAULT):
    """One sheet, one repeat -- the fastest run that proves the lane still works.

    *sheet_name* is one of `tests/modelmania_harness.py`'s `PHASES` keys;
    defaults to `mm2025-phase1.pdf`, the sheet the smoke ran before this
    became a choice.
    """
    from tests import modelmania_harness

    sheet = modelmania_harness.PHASES[sheet_name]
    return [(sheet.stem, str(sheet))]


PINNED_BUDGET_USD = 40.0
"""
Default `--budget-usd` for `--pinned`: one opus `mm2025-phase1` run at
`--effort high` cost $9.66 (Design) + $1.54 (Plan) + $20.29 for 8 of 9
Execute Steps, and was cut off before a verdict by a $30 ceiling -- too low
to let a pinned run finish, so `--pinned` defaults to $40 instead of
leaving `--budget-usd` unset and risking an unbounded overnight spend.
"""


def sweep(tasks, models, effort, budget_usd, intent, out, hang_seconds, retries,
          resume, provider, run_seconds, conflicts=None, timestamp=None):
    """
    *conflicts* ("on"/"off"/`None`) is the append-only model's own A/B arm:
    set, it suffixes every task's label the way `logs/ab-rlm-agentic`
    already names an arm (`mm23-off`) and is passed on to
    `tools/run.py --conflicts`. Every attempt gets its own `<stem>.<timestamp>`
    log and, from `run_one`'s own naming, STEP, so a retry's save never
    lands on the same path as any other attempt's. *timestamp* is
    injectable so a test can make attempts distinguishable without a real
    clock.
    """
    timestamp = timestamp or _attempt_timestamp
    state_path = os.path.join(out, "state.json")
    state = load_state(state_path) if resume else {"runs": {}}

    for model in models:
        model_dir = os.path.join(out, model)
        os.makedirs(model_dir, exist_ok=True)
        for label, sheet in tasks:
            arm_label = "%s-%s" % (label, conflicts) if conflicts else label
            key = "%s/%s" % (arm_label, model)
            if state["runs"].get(key, {}).get("status") == "done":
                print("SKIP %s: already done" % key, flush=True)
                continue

            stem = os.path.join(model_dir, arm_label)
            outcome = "hung"
            attempts = []
            log = None
            for attempt in range(1, retries + 1):
                log = _unique_stem(stem, timestamp()) + ".jsonl"
                attempts.append(log)
                kwargs = {"conflicts": conflicts} if conflicts is not None else {}
                process = run_one(label, sheet, model, effort, budget_usd, log, intent, provider,
                                  **kwargs)
                outcome = watch(process, log, hang_seconds, run_seconds)
                if outcome == "exited" and process.returncode == 0:
                    break
                print("%s attempt %d: %s (exit %s)"
                      % (key, attempt, outcome, process.returncode), flush=True)

            state["runs"][key] = {"label": arm_label, "model": model, "log": log,
                                  "attempts": attempts,
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
    """
    (model, label, path), one per run -- `state.json`, when a sweep wrote
    one, names each key's own final attempt so a retried, failed attempt
    at the same key does not also show up as a run of its own; a directory
    of logs no sweep ever touched falls back to every `.jsonl` in it.
    """
    state_path = os.path.join(out, "state.json")
    if os.path.exists(state_path):
        state = load_state(state_path)
        for key, run in sorted(state["runs"].items()):
            if run.get("log") and os.path.exists(run["log"]):
                yield run["model"], run["label"], run["log"]
        return

    models = sorted(d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d)))
    for model in models:
        for path in sorted(glob.glob(os.path.join(out, model, "*.jsonl"))):
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


MODELMANIA_DIR = os.environ.get(
    "DAE_MODELMANIA_DIR", os.path.join(os.path.dirname(ROOT), "manual", "modelmania"))
EXEMPLAR_DIR = os.path.join(MODELMANIA_DIR, "exemplar")


def _exemplar_for(label):
    """The Exemplar STEP *label* scores against, or `None` when it names none.

    A baseline label is `<sheet stem>` or `<sheet stem>-rep<n>`; only the
    six `manual/modelmania/exemplar/<stem>.step` sheets have one, and
    `DAE_MODELMANIA_DIR` names where that tree actually lives, same as
    `tests/modelmania_harness.py`'s own `EXEMPLAR_DIR`.
    """
    import re as _re

    stem = _re.sub(r"-rep\d+(-on|-off)?$", "", label)
    exemplar = os.path.join(EXEMPLAR_DIR, stem + ".step")
    return exemplar if os.path.isfile(exemplar) else None


def _score_for(path, label):
    """The run's own Score against its Exemplar, or `None` when either is absent."""
    candidate = path[: -len(".jsonl")] + ".step" if path.endswith(".jsonl") else None
    exemplar = _exemplar_for(label)
    if not candidate or not exemplar or not os.path.exists(candidate):
        return None

    import score as score_module

    result = score_module.score(candidate, exemplar)
    return result["score"] if result is not None else None


def _rows(out):
    """(model, label, events, Measurement), one per sweep log under *out*."""
    from core.events import replay
    from core.measure import measure

    for model, label, path in _logs(out):
        events, _truncated = replay(path)
        yield model, label, events, measure(path, score=_score_for(path, label))


def _score_average(measurements):
    """Mean Score over the runs that have one; `0.0` when none do, like `core.measure._average`."""
    scored = [m.score for m in measurements if m.score is not None]
    return sum(scored) / len(scored) if scored else 0.0


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
        ("avg score", _score_average, "{:.4f}"),
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


def _fmt_score(value):
    return "-" if value is None else "{:.4f}".format(value)


def _detail_table(rows):
    header = ["model", "sheet", "verdict", "reason", "confirmed/unmet",
             "steps started/applied", "preflight fails", "cost (USD)",
             "working (s)", "questions", "truncated", "score"]
    lines = [_row(header), _row(["---"] * len(header))]
    for model, label, events, m in rows:
        confirmed, unmet = _assertion_counts(events)
        cells = [model, label, _verdict(events, m), _reason(events, m),
                "%d/%d" % (confirmed, unmet),
                "%d/%d" % (m.steps_started, m.steps_applied),
                str(m.preflight_failures), "{:.4f}".format(m.cost_usd),
                "{:.1f}".format(m.working_seconds), str(m.question_count),
                "yes" if m.truncated else "no", _fmt_score(m.score)]
        lines.append(_row(cells))
    return "\n".join(lines)


def _sheet_stem(label):
    """
    The sheet a repeat's label names, e.g. `mm2025-phase1-rep2` -> `mm2025-phase1`.
    An A/B arm suffix (`mm2025-phase1-rep2-off`) survives the strip
    (`mm2025-phase1-off`), so the noise floor is measured within one arm's
    own repeats and never conflated with the difference the arms exist to show.
    """
    import re as _re

    return _re.sub(r"-rep\d+(-on|-off)?$", r"\1", label)


def _noise_floor_section(rows):
    """
    The spread of Score across the repeats of each sheet, per model, and the
    largest of them -- the noise floor a later comparison must clear before
    it can be trusted as a real change, not this instrument's own jitter.
    """
    from core.measure import score_spread

    grouped = {}
    for model, label, _events, m in rows:
        grouped.setdefault((model, _sheet_stem(label)), []).append(m)
    spreads = {key: score_spread(ms) for key, ms in grouped.items()}
    spreads = {key: s for key, s in spreads.items() if s is not None}

    if not spreads:
        return ("## Noise floor\n\nnot enough repeats of any one sheet to measure a "
                "spread; run `--pinned` (3 repeats per sheet) and `--report` again.")

    floor = max(spreads.values())
    lines = ["## Noise floor", "", "| model | sheet | score spread |", "| --- | --- | --- |"]
    for (model, stem), spread in sorted(spreads.items()):
        lines.append("| %s | %s | %.4f |" % (model, stem, spread))
    lines.append("")
    lines.append("Noise floor (largest spread across repeats of any one sheet): "
                 "**%.4f**." % floor)
    lines.append("A comparison that moves the score by less than this has not been "
                 "shown to be a change.")
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
        _noise_floor_section(rows),
    ]) + "\n"


def main():
    from tests import modelmania_harness

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
    parser.add_argument("--pinned", action="store_true",
                        help="Sweep the 6 pinned phase sheets, 3 repeats each, over --model")
    parser.add_argument("--smoke", action="store_true",
                        help="Sweep 1 sheet, 1 repeat, over --model")
    parser.add_argument("--smoke-sheet", choices=sorted(modelmania_harness.PHASES),
                        default=SMOKE_SHEET_DEFAULT,
                        help="Sheet --smoke runs; defaults to %r" % SMOKE_SHEET_DEFAULT)
    parser.add_argument("--conflicts", choices=("on", "off"), default=None,
                        help="The conflict-recording A/B arm; unset runs tools/run.py's own "
                             "default (on)")
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

    if args.pinned:
        if len(args.model) != 1:
            raise SystemExit("--pinned takes exactly one --model, run again for a second one")
        tasks = pinned_tasks()
        if args.budget_usd is None:
            args.budget_usd = PINNED_BUDGET_USD
    elif args.smoke:
        if len(args.model) != 1:
            raise SystemExit("--smoke takes exactly one --model, run again for a second one")
        tasks = smoke_tasks(args.smoke_sheet)
    elif args.baseline:
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
         args.run_seconds, args.conflicts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
