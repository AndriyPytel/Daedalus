#!/usr/bin/env python
"""
One task, one arm, N runs — the A/B harness for RLM_AGENTIC.

Both arms run from the same checkout, the same client and the same budget, so
the only thing that differs between them is the environment variable the loop
reads per call.

The spend ceiling is enforced through `cancelled`, which `core/loop.py :: run`
asks between Steps and between attempts: a run that has already cost more than
the ceiling stops there rather than at the end of whatever it was doing, and no
further run is started once the ceiling is reached. The price comes off the
`cost` events the run itself emits, which is what the subscription route reports
a call would have cost.

Retry/backoff, `state.json` and the wall-clock watchdog are `tools/ab_lane.py`'s
own logic, imported rather than copied: a transient (`ENOTFOUND` and friends) is
retried instead of recorded as a dead reading, `state.json` is written after
every run so a crash mid-arm does not re-buy runs already paid for, and
`--resume` skips what it already marked `done`. `spend.json` is kept for
backward compatibility, rebuilt from `state.json` after each run instead of
written once at the end.
"""
import argparse
import json
import os
import sys
import time


def pin_path(here):
    """Drop every rival `core` off `sys.path` and put the working tree first.

    FreeCAD's own import adds the installed addon's directory to `sys.path`
    ahead of `here` -- and that addon is a symlink to a different checkout, so
    `core.events` (and anything else `core.*`) can resolve to the wrong
    repository's copy of FCC-292's fix (`tools/ab_lane.py` hit this first).
    """
    kept = []
    for entry in sys.path:
        try:
            real = os.path.realpath(entry)
        except OSError:
            continue
        if real == here or not os.path.isdir(os.path.join(entry, "core")):
            kept.append(entry)
    sys.path[:] = [here] + [p for p in kept if os.path.realpath(p) != here]
    for name in [n for n in sys.modules
                 if n == "core" or n.startswith("core.")
                 or n in ("ui", "utils") or n.startswith(("ui.", "utils."))]:
        del sys.modules[name]


def assert_path(here):
    """Fail loudly and early rather than half way through a paid run."""
    import core.events
    got = os.path.realpath(core.events.__file__)
    if not got.startswith(here + os.sep):
        raise SystemExit("core.events resolved to %s, not under %s" % (got, here))


def write_spend(out, state):
    """Rebuild the old `spend.json` shape from `state.json`'s `done` runs.

    Kept for whatever already reads `spend.json`; `state.json` is the one
    written for resume, this is just its old, flatter reflection.
    """
    done = [r for r in state["runs"].values() if r.get("status") == "done"]
    spent = {"usd": state["usd"],
             "runs": [{"log": r["log"], "usd": r.get("usd") or 0.0,
                      "ok": r.get("ok"), "verdict": r.get("verdict"),
                      "steps_applied": r.get("steps_applied"),
                      "seconds": r.get("seconds")} for r in done]}
    with open(os.path.join(out, "spend.json"), "w", encoding="utf-8") as handle:
        json.dump(spent, handle, indent=2)
    return spent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("on", "off"), required=True)
    parser.add_argument("--intent", required=True)
    parser.add_argument("--attach", nargs="*", default=[])
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--budget-usd", type=float, required=True,
                        help="Ceiling on what this arm may spend, over all its runs")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--run-timeout-seconds", type=float, default=12000.0,
                        help="Wall-clock backstop per run, independent of core.llm's "
                             "per-call ceiling; default clears the longest observed "
                             "legitimate run (8332s) with margin")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--save", default="")
    args = parser.parse_args()

    os.environ["RLM_AGENTIC"] = args.arm
    here = os.path.realpath(os.getcwd())
    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    sys.path.insert(0, here)

    from tools.ab_lane import load_state, rotate, save_state, transient_reason, watchdog

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import FreeCAD

    pin_path(here)
    assert_path(here)

    from core.freecad.apply import ApplyPipeline, _qt
    from core.events import COST, EventStream
    from core.freecad.freecad_tools import build_registry
    from core.llm import ClaudeCodeClient, ProfiledClient
    from core.loop import APPROVE, Approval, run
    from core.measure import compare
    from core.kernel import Kernel

    _, QtWidgets = _qt()
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    os.makedirs(args.out, exist_ok=True)
    state = load_state(args.out, args) if args.resume else {
        "model": args.model, "effort": args.effort,
        "budget_usd": args.budget_usd, "usd": 0.0, "runs": {}}

    for attempt in range(1, args.runs + 1):
        key = "%s-%d" % (args.arm, attempt)
        record = state["runs"].get(key)
        if record and record.get("status") == "done":
            print("SKIP %s: already done, $%.2f" % (key, record.get("usd") or 0.0), flush=True)
            continue
        if state["usd"] >= args.budget_usd:
            print("%s: stopping before run %d, $%.2f of $%.2f spent"
                  % (args.arm, attempt, state["usd"], args.budget_usd), flush=True)
            break

        stem = os.path.join(args.out, "%s-%d" % (args.arm, attempt))
        for sub_attempt in range(1, args.retries + 1):
            rotate(stem)
            log = stem + ".jsonl"
            this_run = {"usd": 0.0}

            events = EventStream()
            events.log_to(log)

            def priced(event, this_run=this_run):
                if event.kind == COST:
                    this_run["usd"] += event.data.get("usd") or 0.0

            events.subscribe(priced)

            client = ProfiledClient(
                thinking=ClaudeCodeClient(model=args.model, effort=args.effort),
                doing=ClaudeCodeClient(model=args.model, effort=args.effort),
            )
            doc = FreeCAD.newDocument("AB_%s_%d" % (args.arm, attempt))
            doc.UndoMode = 1
            started = time.time()

            def record_timeout(key=key, stem=stem, sub_attempt=sub_attempt,
                               this_run=this_run, started=started):
                state["runs"][key] = {
                    "arm": args.arm, "log": stem + ".jsonl", "status": "timed_out",
                    "attempts": sub_attempt, "usd": this_run["usd"],
                    "seconds": time.time() - started, "finished_at": time.time()}
                save_state(args.out, state, compare)
                write_spend(args.out, state)

            watch = watchdog(args.run_timeout_seconds, record_timeout)
            with Kernel(timeout=180) as worker:
                pipeline = ApplyPipeline(document=doc, worker=worker,
                                         dispatch=lambda func, timeout=None: func(),
                                         confirm=lambda kinds: False, events=events)
                result = run(args.intent, llm=client,
                             tools=build_registry(doc, pipeline=pipeline),
                             events=events, attachments=list(args.attach),
                             gate=lambda steps: Approval(APPROVE),
                             cancelled=lambda: (state["usd"] + this_run["usd"]) >= args.budget_usd)
            watch.cancel()

            seconds = time.time() - started
            if args.save:
                doc.saveAs(os.path.join(args.save, "%s-%d.FCStd" % (args.arm, attempt)))
            FreeCAD.closeDocument(doc.Name)

            marker = transient_reason(log)
            dead = bool(marker) and not result.ok and not result.steps_applied

            state["usd"] += this_run["usd"]
            state["runs"][key] = {
                "arm": args.arm, "log": log, "status": "failed" if dead else "done",
                "attempts": sub_attempt, "transient": marker,
                "ok": bool(result.ok), "verdict": result.verdict,
                "steps_applied": result.steps_applied,
                "usd": this_run["usd"], "seconds": seconds, "finished_at": time.time()}
            save_state(args.out, state, compare)
            write_spend(args.out, state)

            print("%s run %d: %s ok=%s verdict=%s steps=%d $%.2f (arm total $%.2f/$%.2f) %.0fs%s -> %s"
                  % (args.arm, attempt, "FAILED" if dead else "done", result.ok, result.verdict,
                     result.steps_applied, this_run["usd"], state["usd"], args.budget_usd, seconds,
                     " [%s, attempt %d/%d]" % (marker, sub_attempt, args.retries) if dead else "",
                     log), flush=True)

            if not dead:
                break
            if sub_attempt < args.retries:
                pause = 30 * sub_attempt
                print("retrying %s in %ds" % (key, pause), flush=True)
                time.sleep(pause)

    done = [r for r in state["runs"].values() if r.get("status") == "done"]
    print("%s: $%.2f over %d run(s)" % (args.arm, state["usd"], len(done)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
