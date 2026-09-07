#!/usr/bin/env python
"""
One model's lane of the RLM_AGENTIC A/B, resumable run by run.

A lane is one model at one effort. It walks the tasks in the order given and
runs each one twice, `off` then `on`.

The ceiling is per run, not per lane. A lane-wide ceiling is spent in the order
the tasks happen to run, so the first expensive `off` starves the `on` it is
being compared against and the later sheets never run at all: one opus run took
$10.00 of a $15 lane and left $5.00 for the other five. Per run, both arms are
bought the same room. `--lane-cap-usd` is the runaway stop, and defaults to the
per-run ceiling once per task.

Three things this lane does that a plain script does not.

`sys.path` is pinned before the first `core` import. `core` carries no
`__init__.py`, so it is a namespace package whose `__path__` spans every
`sys.path` entry that holds a `core` directory. FreeCAD puts the installed
addon on `sys.path`, and that addon is a symlink to the main checkout, so
`core.freecad.traps` came off the worktree while `core.events` came off the checkout
and the lane died on an ImportError that appeared on one model and not the
next. Every rival `core` is dropped, and the resolved file is asserted.

State is written after every run to `state.json`, keyed `task/arm`. `--resume`
reads it back, skips what is `done`, and restores the money already spent, so
a lane that is killed or fixed mid-flight continues instead of re-buying the
runs that already landed.

A run that ends with no Step and no cost because the provider never answered
is a transient, not a reading. It is retried with a backoff, and if it never
answers it is recorded `failed` rather than averaged into the comparison as a
zero.
"""
import argparse
import json
import os
import signal
import sys
import threading
import time

TRANSIENT = ("ENOTFOUND", "EAI_AGAIN", "Can't reach the API server", "fetch failed",
             "socket hang up", "Connection reset", "Connection error", "network",
             "ended the call as 'success'", "rate limit", "Overloaded",
             "502", "503", "timed out")


def pin_path():
    """Drop every rival `core` off `sys.path` and put the working tree first."""
    here = os.path.realpath(os.getcwd())
    kept = []
    for entry in sys.path:
        try:
            real = os.path.realpath(entry)
        except OSError:
            continue
        if real == here or not os.path.isdir(os.path.join(entry, "core")):
            kept.append(entry)
    sys.path = [here] + [p for p in kept if os.path.realpath(p) != here]
    for name in [n for n in sys.modules
                 if n == "core" or n.startswith("core.")
                 or n in ("ui", "utils") or n.startswith(("ui.", "utils."))]:
        del sys.modules[name]


def assert_path(here):
    """Fail loudly and early rather than half way through a paid lane."""
    import core.events
    got = os.path.realpath(core.events.__file__)
    if not got.startswith(here + os.sep):
        raise SystemExit("core.events resolved to %s, not under %s" % (got, here))
    print("path pinned: core.events -> %s" % got, flush=True)


def transient_reason(log_path):
    """The provider's own words in the event the run actually ended on.

    Only the last `blocked` or `error` counts. A run that recovered from a
    refusal half way through and went on to apply Steps is a reading, not a
    transient, and one earlier marker anywhere in a 900k log would otherwise
    throw the whole run away.
    """
    if not os.path.exists(log_path):
        return None
    final = None
    with open(log_path, encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("kind") in ("blocked", "error"):
                final = event
    if final is None:
        return None
    text = json.dumps(final.get("data") or {})
    for marker in TRANSIENT:
        if marker.lower() in text.lower():
            return marker
    return None


def watchdog(seconds, record):
    """Kill this process if a single run outruns its own wall-clock ceiling.

    core/llm.py's per-call timer already failed to hold once: a call given a
    900s ceiling logged 1012.9s in the 2026-09-05 pilot run. This backstop
    is timed independently, in the harness process rather than the LLM
    layer, so a ceiling that fails silently there does not also strand a
    lane overnight. `record()` writes this run's own status to disk from
    this thread before either exit path fires, so a run killed here is
    never lost the way `state.json` exists to prevent. `SIGTERM` is tried
    first, its default disposition already terminates the process cleanly;
    `os._exit` is a fallback a few seconds later only for a hang stubborn
    enough that the plain signal does not end it.
    """
    def fire():
        record()
        os.kill(os.getpid(), signal.SIGTERM)
        threading.Timer(10, lambda: os._exit(1)).start()
    timer = threading.Timer(seconds, fire)
    timer.daemon = True
    timer.start()
    return timer


def rotate(stem):
    """Move a previous attempt's log aside so this one starts on an empty file.

    `EventStream.log_to` opens for append, so a retry -- or a lane picked up by
    --resume -- writes a second run into the first one's log. `measure` then
    reads both as one run and the comparison counts Steps and dollars that
    belong to an attempt that was thrown away.
    """
    log = stem + ".jsonl"
    if not os.path.exists(log):
        return
    index = 1
    while os.path.exists("%s.attempt%d.jsonl" % (stem, index)):
        index += 1
    os.rename(log, "%s.attempt%d.jsonl" % (stem, index))


def load_state(out, args):
    path = os.path.join(out, "state.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    state = {"model": args.model, "effort": args.effort,
             "budget_usd": args.budget_usd, "usd": 0.0, "runs": {}}
    legacy = os.path.join(out, "lane.json")
    if os.path.exists(legacy):
        with open(legacy, encoding="utf-8") as handle:
            old = json.load(handle)
        for run_record in old.get("runs", []):
            key = "%s/%s" % (run_record["task"], run_record["arm"])
            marker = transient_reason(run_record.get("log") or "")
            dead = bool(marker) and not run_record.get("ok") \
                and not run_record.get("steps_applied")
            state["runs"][key] = dict(run_record, adopted=True, attempts=1,
                                      transient=marker,
                                      status="failed" if dead else "done")
        state["usd"] = float(old.get("usd") or 0.0)
        print("adopted %d run(s) worth $%.2f from lane.json"
              % (len(state["runs"]), state["usd"]), flush=True)
    return state


def save_state(out, state, compare):
    with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
    done = [r for r in state["runs"].values() if r.get("status") == "done"]
    off = [r["log"] for r in done if r["arm"] == "off"]
    on = [r["log"] for r in done if r["arm"] == "on"]
    if off and on:
        with open(os.path.join(out, "comparison.md"), "w", encoding="utf-8") as handle:
            handle.write("# %s @ %s\n\n" % (state["model"], state["effort"]))
            handle.write(compare(off, on, "RLM_AGENTIC=off", "RLM_AGENTIC=on"))
            handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--intent", required=True)
    parser.add_argument("--task", action="append", default=[],
                        help="label=/path/to/sheet.pdf, repeatable, walked in order")
    parser.add_argument("--baseline", action="store_true",
                        help="Walk the 8 sheets pinned in "
                             "tests/modelmania/baseline_sheets.json, so a baseline run "
                             "and its comparison cannot drift onto different sets; "
                             "adds to --task")
    parser.add_argument("--budget-usd", type=float, required=True,
                        help="Ceiling for ONE run, so `off` and `on` are bought the same room")
    parser.add_argument("--lane-cap-usd", type=float, default=None,
                        help="Ceiling for the whole lane, default budget-usd per task")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--run-timeout-seconds", type=float, default=12000.0,
                        help="Wall-clock backstop per run, independent of core.llm's "
                             "per-call ceiling; default clears the longest observed "
                             "legitimate run (8332s) with margin")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    tasks = [t.split("=", 1) for t in args.task]
    if args.baseline:
        with open("tests/modelmania/baseline_sheets.json", encoding="utf-8") as handle:
            for sheet in json.load(handle)["sheets"]:
                label = os.path.splitext(os.path.basename(sheet))[0]
                tasks.append([label, sheet])
    if not tasks:
        raise SystemExit("no tasks: pass --task or --baseline")
    cap = args.lane_cap_usd if args.lane_cap_usd is not None else args.budget_usd * len(tasks)
    here = os.path.realpath(os.getcwd())

    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    sys.path.insert(0, here)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import FreeCAD

    pin_path()
    assert_path(here)

    from core.freecad.apply import ApplyPipeline, _qt
    from core.events import COST, EventStream
    from core.freecad.freecad_tools import build_registry
    from core.llm import ClaudeCodeClient, ProfiledClient
    from core.loop import APPROVE, Approval, run
    from core.measure import compare, measure
    from core.kernel import Kernel

    _, QtWidgets = _qt()
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    os.makedirs(args.out, exist_ok=True)
    state = load_state(args.out, args) if args.resume else {
        "model": args.model, "effort": args.effort,
        "budget_usd": args.budget_usd, "usd": 0.0, "runs": {}}
    state["budget_usd"] = args.budget_usd
    state["lane_cap_usd"] = cap
    print("ceiling: $%.2f per run, $%.2f per lane" % (args.budget_usd, cap), flush=True)

    for label, sheet in tasks:
        for arm in ("off", "on"):
            key = "%s/%s" % (label, arm)
            stem = os.path.join(args.out, "%s-%s" % (label, arm))
            record = state["runs"].get(key)
            if record and record.get("status") == "done":
                print("SKIP %s: already done, $%.2f" % (key, record.get("usd") or 0.0), flush=True)
                continue
            if state["usd"] >= cap:
                state["runs"][key] = {"task": label, "arm": arm, "status": "skipped",
                                      "reason": "lane cap", "log": stem + ".jsonl"}
                save_state(args.out, state, compare)
                print("SKIP %s: $%.2f of the lane's $%.2f spent"
                      % (key, state["usd"], cap), flush=True)
                continue

            for attempt in range(1, args.retries + 1):
                state["runs"][key] = {"task": label, "arm": arm, "status": "running",
                                      "attempt": attempt, "log": stem + ".jsonl",
                                      "started_at": time.time()}
                save_state(args.out, state, compare)

                os.environ["RLM_AGENTIC"] = arm
                rotate(stem)
                spent = {"usd": 0.0}
                events = EventStream()
                events.log_to(stem + ".jsonl")
                events.subscribe(lambda event, spent=spent: spent.__setitem__(
                    "usd", spent["usd"] + (event.data.get("usd") or 0.0))
                    if event.kind == COST else None)

                client = ProfiledClient(
                    thinking=ClaudeCodeClient(model=args.model, effort=args.effort),
                    doing=ClaudeCodeClient(model=args.model, effort=args.effort),
                )
                doc = FreeCAD.newDocument("AB_%s_%s" % (label.replace("-", "_"), arm))
                doc.UndoMode = 1
                started = time.time()

                def record_timeout(key=key, label=label, arm=arm, stem=stem,
                                   attempt=attempt, spent=spent, started=started):
                    state["runs"][key] = {
                        "task": label, "arm": arm, "log": stem + ".jsonl",
                        "status": "timed_out", "attempts": attempt,
                        "usd": spent["usd"], "seconds": time.time() - started,
                        "finished_at": time.time()}
                    save_state(args.out, state, compare)

                watch = watchdog(args.run_timeout_seconds, record_timeout)
                with Kernel(timeout=180) as worker:
                    pipeline = ApplyPipeline(document=doc, worker=worker,
                                             dispatch=lambda func, timeout=None: func(),
                                             confirm=lambda kinds: False, events=events)
                    result = run(args.intent, llm=client,
                                 tools=build_registry(doc, pipeline=pipeline),
                                 events=events, attachments=[sheet],
                                 gate=lambda steps: Approval(APPROVE),
                                 cancelled=lambda: spent["usd"] >= args.budget_usd
                                 or (state["usd"] + spent["usd"]) >= cap)
                watch.cancel()

                seconds = time.time() - started
                state["usd"] += spent["usd"]
                try:
                    doc.saveAs(stem + ".FCStd")
                except Exception as exc:
                    print("could not save %s: %s" % (stem, exc), flush=True)
                FreeCAD.closeDocument(doc.Name)

                marker = transient_reason(stem + ".jsonl")
                dead = bool(marker) and not result.ok and not result.steps_applied

                reading = measure(stem + ".jsonl")
                with open(stem + ".measure.json", "w", encoding="utf-8") as handle:
                    json.dump({field: getattr(reading, field)
                               for field in reading.__dataclass_fields__}, handle,
                              indent=2, default=list)

                state["runs"][key] = {
                    "task": label, "arm": arm, "log": stem + ".jsonl",
                    "status": "failed" if dead else "done",
                    "attempts": attempt, "transient": marker,
                    "ok": bool(result.ok), "verdict": result.verdict,
                    "reason": (result.reason or "")[:200],
                    "steps_applied": result.steps_applied,
                    "usd": spent["usd"], "seconds": seconds,
                    "finished_at": time.time()}
                save_state(args.out, state, compare)

                print("%s %s %s: %s ok=%s verdict=%s steps=%d $%.2f (lane $%.2f/$%.2f) %.0fs%s"
                      % (args.model, label, arm,
                         "FAILED" if dead else "done", result.ok, result.verdict,
                         result.steps_applied, spent["usd"], state["usd"],
                         cap, seconds,
                         " [%s, attempt %d/%d]" % (marker, attempt, args.retries) if dead else ""),
                      flush=True)

                if not dead:
                    break
                if attempt < args.retries:
                    pause = 30 * attempt
                    print("retrying %s in %ds" % (key, pause), flush=True)
                    time.sleep(pause)

    save_state(args.out, state, compare)
    counted = [r for r in state["runs"].values() if r.get("status") == "done"]
    broken = [k for k, r in state["runs"].items() if r.get("status") == "failed"]
    print("%s lane done: $%.2f over %d run(s), %d failed%s"
          % (args.model, state["usd"], len(counted), len(broken),
             (" (%s)" % ", ".join(broken)) if broken else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
