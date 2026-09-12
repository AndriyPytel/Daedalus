#!/usr/bin/env python
"""
The automatic sweep: every sheet against every model, resumable run by run.

Each run is `tools/run.py`, spawned as its own subprocess -- so a run that
goes quiet is one process to kill and restart, not the whole lane, and this
module never touches `ApplyPipeline`, `build_registry` or `Kernel` itself.
State is written to `state.json` after every run, keyed `label/model`;
`--resume` reads it back and skips what is already `done`.

A run writes a `thought` event roughly every minute while it is alive (the
old `ab_watchdog.sh` rule): if its log has not grown for `--hang-seconds`
(default 1800, the wall-clock silence `ab_watchdog.sh` used), the run is
killed and retried rather than left to rot the lane overnight.

One sweep is one provider; two are two sweeps into one `--out`.

`--status` prints `core.measure.compare`'s table over the logs a lane run
already wrote, without starting anything -- two models compare directly the
way `tools/measure_session.py --baseline/--experiment` did; any other count
falls back to that CLI's other form, one `core.measure.measure` report per
log, so neither of its two real functions is lost.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANG_SECONDS = 1800


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
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.status:
        status(args.out)
        return 0

    tasks = [t.split("=", 1) for t in args.task]
    if not tasks or not args.model:
        raise SystemExit("--status, or --model and --task, each at least once")

    os.makedirs(args.out, exist_ok=True)
    sweep(tasks, args.model, args.effort, args.budget_usd, args.intent,
         args.out, args.hang_seconds, args.retries, args.resume, args.provider,
         args.run_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
