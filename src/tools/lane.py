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
import random
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))

HANG_SECONDS = 1800
REASON_WIDTH = 60


def _domains():
    import sdk

    return sdk.PART, sdk.ASSEMBLY


def _attempt_timestamp():
    """FreeCAD's own init binds `datetime` to the class in `__main__`, which is this module when it runs as a script."""
    from datetime import datetime as clock

    return clock.now().strftime("%Y%m%d-%H%M%S-%f")


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


QUOTA_PHRASES = ("limit is spent", "usage limit", "quota for this model is exhausted",
                 "out of credit")
QUOTA_EXIT = 75


def quota_refusal(log):
    """
    The sentence of a provider's spent-quota refusal that ended the run in *log*, else `None`.

    Such a run is recorded `quota` and not retried, and `sweep` stops and returns `True`. A rate limit that clears in a moment is not one.
    """
    import sdk

    events, _truncated = sdk.replay(log)
    for event in reversed(events):
        if event.kind == sdk.BLOCKED:
            reason = event.data.get("reason") or ""
            return reason if any(phrase in reason for phrase in QUOTA_PHRASES) else None
    return None


def run_one(label, sheet, model, effort, budget_usd, log, intent, provider,
            python=sys.executable, conflicts=None, base_step=None, attach=(), arm="agent",
            domain=None):
    """
    One run of one sheet, as a subprocess of `tools/run.py`.

    A task with no sheet — `--task bolt-nut=` — is a run of the Intent alone.
    *conflicts* is `tools/run.py --conflicts`; an editing run names *base_step* and the renders in *attach*; *arm* is `--arm`, left off the command line for the default agent; *domain* is `--domain`, left off for Part, and an Assembly run saves an FCStd.
    """
    part, assembly = _domains()
    domain = domain or part
    suffix = ".FCStd" if domain == assembly else ".step"
    save = log[: -len(".jsonl")] + suffix if log.endswith(".jsonl") else log + suffix
    cmd = [python, os.path.join(ROOT, "tools", "run.py"),
          "--intent", intent, "--model", model,
          "--provider", provider, "--effort", effort, "--log", log, "--save", save]
    if sheet:
        cmd += ["--attach", sheet]
    if attach:
        cmd += ["--attach"] + list(attach)
    if budget_usd is not None:
        cmd += ["--budget-usd", str(budget_usd)]
    if conflicts is not None:
        cmd += ["--conflicts", conflicts]
    if base_step:
        cmd += ["--base-step", base_step]
    if arm != "agent":
        cmd += ["--arm", arm]
    if domain != part:
        cmd += ["--domain", domain]
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


RENDER_VIEWS = ("front", "right", "top", "iso")
VOLUME_RATIO = (0.2, 5.0)


def _part():
    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    import FreeCAD  # noqa: F401
    import Part

    return Part


def step_problem(path):
    """
    The reason the official gate would refuse the STEP at *path*, else `None`.

    Refused: no solid, more than one solid, an OCC `isValid()` failure, and the mesh gate
    `core.manifold` mirrors.
    """
    shape = _part().read(path)
    import sdk

    if not shape.Solids:
        return "no solid"
    if len(shape.Solids) > 1:
        return "%d solids" % len(shape.Solids)
    if not shape.isValid():
        return "not valid"
    return sdk.manifold.check_fc(shape)


def guard_edit(edit, base):
    """
    Why the edited STEP *edit* must not ship, else `None`.

    It must pass `step_problem` and keep its volume within `VOLUME_RATIO` of *base*'s; real edits
    moved volume by -15% and +9%, so only an absurd change is a failed edit.
    """
    problem = step_problem(edit)
    if problem:
        return problem
    Part = _part()
    before = Part.read(base).Volume
    ratio = Part.read(edit).Volume / before if before else 1.0
    low, high = VOLUME_RATIO
    return None if low <= ratio <= high else "volume ratio %.3f" % ratio


def _shippable_base(fixture, folder):
    """The fixture's `input.step`, or where it fails the official gate and `repair_base` mends it, that repair written into *folder*."""
    base = os.path.join(fixture, "input.step")
    if not step_problem(base):
        return base
    import sdk

    shape = _part().read(base)
    repaired, _note = sdk.repair_base(shape)
    if repaired is shape:
        return base
    path = os.path.join(folder, "input.repaired.step")
    repaired.exportStep(path)
    sdk.rewrite_step(repaired, path)
    return base if step_problem(path) else path


def _ship_edit(log, base, output):
    """Write *output*: the run's edited STEP if it passes the guard, else a copy of *base*; return the fallback reason."""
    step = log[: -len(".jsonl")] + ".step" if log and log.endswith(".jsonl") else None
    if step and _has_step(log):
        reason = guard_edit(step, base)
        source = base if reason else step
    else:
        reason, source = "no solid", base
    os.makedirs(os.path.dirname(output), exist_ok=True)
    shutil.copyfile(source, output)
    return reason


def _has_step(log, suffix=".step"):
    step = log[: -len(".jsonl")] + suffix if log.endswith(".jsonl") else log + suffix
    return os.path.isfile(step) and os.path.getsize(step) > 0


def sweep(tasks, models, effort, budget_usd, intent, out, hang_seconds, retries,
          resume, provider, run_seconds, conflicts=None, timestamp=None, edits=None, arm="agent",
          domain=None, assemblies=None):
    """
    *conflicts* ("on"/"off"/`None`) is the append-only model's own A/B arm:
    set, it suffixes every task's label the way `logs/ab-rlm-agentic`
    already names an arm (`mm23-off`) and is passed on to
    `tools/run.py --conflicts`. Every attempt gets its own `<stem>.<timestamp>`
    log and, from `run_one`'s own naming, STEP, so a retry's save never
    lands on the same path as any other attempt's. *timestamp* is
    injectable so a test can make attempts distinguishable without a real
    clock, and *edits* maps a label to its editing fixture folder, whose run ends in
    `<model>/<label>/output.step`: the edit, or a copy of `input.step`; *arm* and *domain* go to every run, and *assemblies* maps a label to the task folder whose text and parts it runs on.
    """
    import sdk

    edits = edits or {}
    assemblies = assemblies or {}
    part, assembly = _domains()
    domain = domain or part
    saved = ".FCStd" if domain == assembly else ".step"
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
            refusal = None
            attempts = []
            log = None
            fixture = edits.get(label)
            run_intent = intent
            for attempt in range(1, retries + 1):
                log = _unique_stem(stem, timestamp()) + ".jsonl"
                attempts.append(log)
                kwargs = {"conflicts": conflicts} if conflicts is not None else {}
                if fixture:
                    with open(os.path.join(fixture, "edit_description.txt"), encoding="utf-8") as handle:
                        run_intent = sdk.read_prompt("edit_task_intent.md").strip().format(desc=handle.read().strip())
                    kwargs["base_step"] = os.path.join(fixture, "input.step")
                    kwargs["attach"] = [os.path.join(fixture, "renders", view + ".png")
                                        for view in RENDER_VIEWS]
                if arm != "agent":
                    kwargs["arm"] = arm
                if domain != part:
                    kwargs["domain"] = domain
                if label in assemblies:
                    run_intent, kwargs["attach"] = assembly_task(assemblies[label])
                process = run_one(label, sheet, model, effort, budget_usd, log, run_intent, provider,
                                  **kwargs)
                outcome = watch(process, log, hang_seconds, run_seconds)
                refusal = quota_refusal(log) if outcome == "exited" else None
                if refusal:
                    break
                if outcome == "exited" and process.returncode == 0 and _has_step(log, saved):
                    break
                print("%s attempt %d: %s (exit %s)"
                      % (key, attempt, outcome, process.returncode), flush=True)

            succeeded = outcome == "exited" and process.returncode == 0 and _has_step(log, saved)
            state["runs"][key] = {"label": arm_label, "model": model, "log": log,
                                  "attempts": attempts, "returncode": process.returncode,
                                  "status": "quota" if refusal
                                  else "done" if succeeded else "failed"}
            if fixture and not refusal:
                output = os.path.join(model_dir, label, "output.step")
                os.makedirs(os.path.dirname(output), exist_ok=True)
                base = _shippable_base(fixture, os.path.dirname(output))
                state["runs"][key]["fallback"] = _ship_edit(log, base, output)
                state["runs"][key]["input_invalid"] = step_problem(base)
                if state["runs"][key]["input_invalid"]:
                    print("INPUT INVALID %s: %s" % (label, state["runs"][key]["input_invalid"]),
                          flush=True)
            save_state(state_path, state)
            print("%s: %s" % (key, state["runs"][key]["status"]), flush=True)
            if refusal:
                print("QUOTA SPENT, sweep stopped; --resume after the reset. %s" % refusal,
                      flush=True)
                return True
    return False


def status(out):
    import sdk

    models = sorted(d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d)))
    logs = {m: sorted(glob.glob(os.path.join(out, m, "*.jsonl"))) for m in models}

    if len(models) == 2:
        a, b = models
        print(sdk.compare(logs[a], logs[b], a, b))
        return

    for model in models:
        for log in logs[model]:
            reading = sdk.measure(log)
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
    import sdk

    confirmed = sum(len(e.data.get("confirmed") or ()) for e in events if e.kind == sdk.ASSERTION)
    unmet = sum(len(e.data.get("unmet") or ()) for e in events if e.kind == sdk.ASSERTION)
    return confirmed, unmet


def _short(text):
    """One line, short enough not to break a Markdown table row."""
    text = " ".join(str(text).split())
    return text if len(text) <= REASON_WIDTH else text[:REASON_WIDTH - 1] + "..."


def _reason(events, m):
    import sdk

    verdicts = [e for e in events if e.kind == sdk.REVIEW_VERDICT]
    if verdicts:
        return _short(verdicts[-1].data.get("reason") or "")
    if m.blocked_reason:
        return _short(m.blocked_reason)
    if m.disputed:
        return _short("disputed: " + "; ".join(m.disputed))
    return ""


MODELMANIA_DIR = os.environ.get(
    "DAE_MODELMANIA_DIR", os.path.join(os.path.dirname(ROOT), "manual", "modelmania"))
EXEMPLAR_DIR = os.path.join(MODELMANIA_DIR, "exemplar")
TEST_DATA_DIR = os.environ.get("DAE_TEST_DATA_DIR", os.path.join(os.path.dirname(ROOT), "test-data"))
SPLITS = ("validation", "test")


def sampled_tasks(split, count, out, labels_file=None):
    """
    The `(label, sheet.pdf)` tasks of *split* in `test-data/split.json`: *count* labels drawn with a fresh seed, or the lines of *labels_file*.

    The seed is printed and written, with the labels, to *out*; the labels file is the one `--labels` reads back.
    """
    if labels_file:
        with open(labels_file, encoding="utf-8") as handle:
            labels = handle.read().split()
    else:
        with open(os.path.join(TEST_DATA_DIR, "split.json"), encoding="utf-8") as handle:
            pool = json.load(handle)[split]
        seed = random.SystemRandom().randrange(2 ** 32)
        labels = random.Random(seed).sample(pool, count)
        print("seed", seed)
        with open(os.path.join(out, "seed.txt"), "w", encoding="utf-8") as handle:
            handle.write(str(seed))
    with open(os.path.join(out, "labels.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(labels) + "\n")
    return [(label, os.path.join(TEST_DATA_DIR, split, label, "sheet.pdf")) for label in labels]


ASSEMBLY_DIR = os.path.join(TEST_DATA_DIR, "assembly")
FROM_PARTS = "from_parts"
FROM_SCRATCH = "from_scratch"
ASSEMBLY_MODE = FROM_PARTS


def assembly_task(folder):
    """The Intent and the absolute STEP paths of the assembly benchmark task in *folder*; a from-scratch task has its graph in the Intent and no paths."""
    with open(os.path.join(folder, "task.json"), encoding="utf-8") as handle:
        task = json.load(handle)
    if "graph" in task["inputs"]:
        return task["text"] + "\n\nThe graph:\n" + json.dumps(task["inputs"]["graph"], indent=1), []
    return task["text"], [os.path.join(folder, part["file"]) for part in task["inputs"]["parts"]]


def sampled_assembly_tasks(split, count, out, labels_file=None, mode=ASSEMBLY_MODE):
    """The `(label, folder)` tasks of *split* in the Assembly benchmark's `split.json`, drawn or read back like `sampled_tasks`."""
    if labels_file:
        with open(labels_file, encoding="utf-8") as handle:
            labels = handle.read().split()
    else:
        with open(os.path.join(ASSEMBLY_DIR, "split.json"), encoding="utf-8") as handle:
            pool = json.load(handle)[mode][split]
        seed = random.SystemRandom().randrange(2 ** 32)
        labels = random.Random(seed).sample(pool, count)
        print("seed", seed)
        with open(os.path.join(out, "seed.txt"), "w", encoding="utf-8") as handle:
            handle.write(str(seed))
    with open(os.path.join(out, "labels.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(labels) + "\n")
    return [(label, os.path.join(ASSEMBLY_DIR, mode, label)) for label in labels]


def assembly_table(rows):
    """One row per task of *rows* (label, passed, score, parts, minutes, usd) and the means under them; the pass mean is the share that passed the Assertion and the parts mean the share of `build_part` calls that came back ok."""
    header = ["task", "Assertion pass", "score", "parts ok", "min", "$"]
    lines = [_row(header), _row(["---"] * len(header))]
    for row in rows:
        lines.append(_row([row["label"], "yes" if row["passed"] else "no", "%.4f" % row["score"],
                           "%d/%d" % row["parts"] if row.get("parts") else "-",
                           "%.1f" % row["minutes"], "%.2f" % row["usd"]]))
    count = len(rows) or 1
    built = [r["parts"] for r in rows if r.get("parts")]
    lines.append(_row(["mean", "%d%%" % round(100 * sum(r["passed"] for r in rows) / count),
                       "%.4f" % (sum(r["score"] for r in rows) / count),
                       "%d%%" % round(100 * sum(ok for ok, _ in built) / (sum(n for _, n in built) or 1))
                       if built else "-",
                       "%.1f" % (sum(r["minutes"] for r in rows) / count),
                       "%.2f" % (sum(r["usd"] for r in rows) / count)]))
    return "\n".join(lines)


def parts_built(log):
    """How many `build_part` results in the event log *log* are ok, and how many there are."""
    answers = []
    with open(log, encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event["kind"] == "tool_result" and event["data"].get("name") == "build_part":
                answers.append(bool(event["data"]["result"].get("ok")))
    return sum(answers), len(answers)


def assembly_rows(out, mode=ASSEMBLY_MODE):
    """The `assembly_table` rows of the sweep under *out*: the Assertion passed where the run exited 0, the Score from `harness/assembly_score` against the task's reference, and minutes and dollars from the log."""
    import sdk

    sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "harness"))
    import assembly_score

    state = load_state(os.path.join(out, "state.json"))
    rows = []
    for key, run in sorted(state["runs"].items()):
        saved = run["log"][: -len(".jsonl")] + ".FCStd"
        reading = sdk.measure(run["log"])
        passed = run.get("returncode") == 0 and os.path.isfile(saved)
        score = 0.0
        if os.path.isfile(saved):
            reference = os.path.join(ASSEMBLY_DIR, mode, run["label"], "reference.FCStd")
            score = assembly_score.score(saved, reference)["score"]
        rows.append({"label": run["label"], "passed": passed, "score": score,
                     "parts": parts_built(run["log"]) if mode == FROM_SCRATCH else None,
                     "minutes": reading.working_seconds / 60, "usd": reading.cost_usd})
    return rows


def _exemplar_for(label):
    """The Exemplar STEP *label* scores against, or `None` when it names none.

    A baseline label is `<sheet stem>` or `<sheet stem>-rep<n>`; only the
    six `manual/modelmania/exemplar/<stem>.step` sheets have one, and
    `DAE_MODELMANIA_DIR` names where that tree actually lives, same as
    `tests/modelmania_harness.py`'s own `EXEMPLAR_DIR`.
    """
    import re as _re

    stem = _re.sub(r"-rep\d+(-on|-off)?$", "", label)
    candidates = [os.path.join(EXEMPLAR_DIR, stem + ".step")] + [
        os.path.join(TEST_DATA_DIR, split, stem, "exemplar.step") for split in SPLITS]
    return next((path for path in candidates if os.path.isfile(path)), None)


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
    import sdk

    for model, label, path in _logs(out):
        events, _truncated = sdk.replay(path)
        yield model, label, events, sdk.measure(path, score=_score_for(path, label))


def _score_average(measurements):
    """Mean Score over the runs that have one; `0.0` when none do, like `core.measure._average`."""
    scored = [m.score for m in measurements if m.score is not None]
    return sum(scored) / len(scored) if scored else 0.0


def _summary_rows():
    import sdk

    return (
        ("pass rate", sdk.pass_rate, "{:.1%}"),
        ("blocked rate", lambda ms: sdk.rate(ms, lambda m: m.blocked_reason is not None), "{:.1%}"),
        ("avg steps applied", lambda ms: sdk.average(ms, lambda m: m.steps_applied), "{:.1f}"),
        ("assertion fail rate", lambda ms: sdk.average(ms, lambda m: m.assertion_fail_rate), "{:.1%}"),
        ("review fail rate", lambda ms: sdk.average(ms, lambda m: m.review_fail_rate), "{:.1%}"),
        ("avg cost (USD)", lambda ms: sdk.average(ms, lambda m: m.cost_usd), "{:.4f}"),
        ("avg working (s)", lambda ms: sdk.average(ms, lambda m: m.working_seconds), "{:.1f}"),
        ("avg questions", lambda ms: sdk.average(ms, lambda m: m.question_count), "{:.1f}"),
        ("avg score", _score_average, "{:.4f}"),
    )


def _row(cells):
    return "| %s |" % " | ".join(cells)


def _summary_table(by_model):
    import sdk

    summary_rows = _summary_rows()
    header = ["model"] + [name for name, _, _ in summary_rows] + ["runs", "truncated"]
    lines = [_row(header), _row(["---"] * len(header))]
    ranked = sorted(by_model.items(), key=lambda kv: -sdk.pass_rate(kv[1]))
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
    if m.disputed:
        return "unverified"
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
    import sdk

    grouped = {}
    for model, label, _events, m in rows:
        grouped.setdefault((model, _sheet_stem(label)), []).append(m)
    spreads = {key: sdk.score_spread(ms) for key, ms in grouped.items()}
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
    parser.add_argument("--edit-task", action="append", default=[],
                        help="id=/path/to/fixture, repeatable; edits the folder's input.step "
                             "per edit_description.txt and writes <model>/<id>/output.step")
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
    parser.add_argument("--arm", choices=("agent", "pipeline"), default="agent",
                        help="Which arm tools/run.py runs each task as; pipeline is the phased pipeline")
    parser.add_argument("--domain", choices=_domains(), default=_domains()[0],
                        help="The Domain every run builds under; assembly takes --assembly-task or --sample/--labels over the from_parts tasks")
    parser.add_argument("--assembly-task", action="append", default=[],
                        help="label=/path/to/task-folder, repeatable; the assembly benchmark task whose text and parts the run is given")
    parser.add_argument("--mode", choices=(FROM_PARTS, FROM_SCRATCH), default=ASSEMBLY_MODE,
                        help="The assembly benchmark mode --sample, --labels and --report read; from_scratch gives the text and graph and builds every part")
    parser.add_argument("--sample", type=int, help="Draw this many random labels of --split from test-data/split.json")
    parser.add_argument("--split", choices=SPLITS, default="validation")
    parser.add_argument("--labels", help="A labels.txt of an earlier --sample, reused as the tasks")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.status:
        status(args.out)
        return 0

    if args.report and args.domain == _domains()[1]:
        text = assembly_table(assembly_rows(args.out, args.mode)) + "\n"
        print(text)
        if args.save:
            with open(args.save, "w", encoding="utf-8") as handle:
                handle.write(text)
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
    elif args.domain == _domains()[1] and (args.sample or args.labels):
        os.makedirs(args.out, exist_ok=True)
        tasks = sampled_assembly_tasks(args.split, args.sample, args.out, args.labels, args.mode)
        args.assembly_task = ["%s=%s" % task for task in tasks]
    elif args.sample or args.labels:
        os.makedirs(args.out, exist_ok=True)
        tasks = sampled_tasks(args.split, args.sample, args.out, args.labels)
    elif args.assembly_task:
        tasks = [(task.split("=", 1)[0], "") for task in args.assembly_task]
    else:
        tasks = [t.split("=", 1) for t in args.task]
        if not tasks and not args.edit_task or not args.model:
            raise SystemExit("--status, --report, or --model and --task, each at least once")

    edits = dict(t.split("=", 1) for t in args.edit_task)
    assemblies = dict(t.split("=", 1) for t in args.assembly_task)
    tasks = list(tasks) + [(label, "") for label in edits]
    os.makedirs(args.out, exist_ok=True)
    stopped = sweep(tasks, args.model, args.effort, args.budget_usd, args.intent,
                    args.out, args.hang_seconds, args.retries, args.resume, args.provider,
                    args.run_seconds, args.conflicts, edits=edits, arm=args.arm,
                    domain=args.domain, assemblies=assemblies)
    return QUOTA_EXIT if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
