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
    parser.add_argument("--out", required=True)
    parser.add_argument("--save", default="")
    args = parser.parse_args()

    os.environ["RLM_AGENTIC"] = args.arm
    here = os.path.realpath(os.getcwd())
    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    sys.path.insert(0, here)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import FreeCAD

    pin_path(here)
    assert_path(here)

    from core.apply import ApplyPipeline, _qt
    from core.events import COST, EventStream
    from core.freecad_tools import build_registry
    from core.llm import ClaudeCodeClient, ProfiledClient
    from core.loop import APPROVE, Approval, run
    from core.preflight import PreflightWorker

    _, QtWidgets = _qt()
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    os.makedirs(args.out, exist_ok=True)
    spent = {"usd": 0.0, "runs": []}

    for attempt in range(1, args.runs + 1):
        if spent["usd"] >= args.budget_usd:
            print("%s: stopping before run %d, $%.2f of $%.2f spent"
                  % (args.arm, attempt, spent["usd"], args.budget_usd), flush=True)
            break

        log = os.path.join(args.out, "%s-%d.jsonl" % (args.arm, attempt))
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

        with PreflightWorker(timeout=180) as worker:
            pipeline = ApplyPipeline(document=doc, worker=worker,
                                     dispatch=lambda func, timeout=None: func(),
                                     confirm=lambda kinds: False, events=events)
            result = run(args.intent, llm=client,
                         tools=build_registry(doc, pipeline=pipeline),
                         events=events, attachments=list(args.attach),
                         gate=lambda steps: Approval(APPROVE),
                         cancelled=lambda: (spent["usd"] + this_run["usd"]) >= args.budget_usd)

        spent["usd"] += this_run["usd"]
        spent["runs"].append({"log": log, "usd": this_run["usd"],
                              "ok": bool(result.ok), "verdict": result.verdict,
                              "steps_applied": result.steps_applied,
                              "seconds": time.time() - started})
        print("%s run %d: ok=%s verdict=%s steps=%d $%.2f (arm total $%.2f/$%.2f) %.0fs -> %s"
              % (args.arm, attempt, result.ok, result.verdict, result.steps_applied,
                 this_run["usd"], spent["usd"], args.budget_usd,
                 time.time() - started, log), flush=True)

        if args.save:
            doc.saveAs(os.path.join(args.save, "%s-%d.FCStd" % (args.arm, attempt)))
        FreeCAD.closeDocument(doc.Name)

    with open(os.path.join(args.out, "spend.json"), "w", encoding="utf-8") as handle:
        json.dump(spent, handle, indent=2)
    print("%s: $%.2f over %d run(s)" % (args.arm, spent["usd"], len(spent["runs"])), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
