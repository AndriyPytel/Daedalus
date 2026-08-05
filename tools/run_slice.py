#!/usr/bin/env python
"""
Run the vertical slice against a **real model** and a real Document.

`tests/test_vertical_slice_level2.py` runs the same machinery with the model
scripted, because a test that needs a paid API key is a test nobody runs. This
script is the other half: the loop deciding for itself.

    export ANTHROPIC_API_KEY=...
    .venv/bin/python tools/run_slice.py
    .venv/bin/python tools/run_slice.py --model openai/gpt-4o --intent "Make a bracket"

It approves the Plan automatically and refuses to touch anything it did not
create, so it is safe to point at a scratch document. It prints the event stream
as it happens and writes it to a session log.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_INTENT = (
    "In a new PartDesign Body, sketch a 10mm square on the XY plane, pad it by 5mm, "
    "then change the pad length to 12mm, then fillet the top edges with a 1.5mm radius. "
    "Find the top edges from the geometry rather than naming them."
)


def freecad_lib():
    for candidate in (os.environ.get("FREECAD_LIB"),
                      "/Applications/FreeCAD.app/Contents/Resources/lib",
                      "/usr/lib/freecad/lib"):
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="anthropic/claude-3-7-sonnet-20250219")
    parser.add_argument("--intent", default=DEFAULT_INTENT)
    parser.add_argument("--log", default="/tmp/slice_events.jsonl")
    parser.add_argument("--save", help="Save the resulting Document here")
    parser.add_argument("--allow-user-objects", action="store_true",
                        help="Answer yes to the Owned Object prompt instead of refusing")
    args = parser.parse_args()

    library = freecad_lib()
    if library and library not in sys.path:
        sys.path.insert(0, library)

    import FreeCAD

    from core.apply import ApplyPipeline
    from core.events import BLOCKED, ERROR, PREFLIGHT_FAILED, RENDER, THOUGHT, EventStream
    from core.freecad_tools import build_registry
    from core.llm import LiteLLMClient
    from core.loop import APPROVE, Approval, run
    from core.preflight import PreflightWorker

    key = (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
           or os.environ.get("GEMINI_API_KEY"))
    if not key:
        print("Set ANTHROPIC_API_KEY (or OPENAI_API_KEY / GEMINI_API_KEY) first.")
        return 2

    client = LiteLLMClient(
        model=args.model, api_key=key,
        thinking_budget=2048 if args.model.startswith("anthropic/") else None,
    )

    doc = FreeCAD.newDocument("Slice")
    doc.UndoMode = 1

    events = EventStream()
    events.log_to(args.log)
    events.subscribe(_printer())

    with PreflightWorker(timeout=180) as worker:
        pipeline = ApplyPipeline(
            document=doc, worker=worker,
            dispatch=lambda func, timeout=None: func(),   # already the main thread
            confirm=lambda kinds: args.allow_user_objects,
        )
        result = run(args.intent, llm=client,
                     tools=build_registry(doc, pipeline=pipeline),
                     events=events, gate=lambda steps: Approval(APPROVE))

    print()
    print("=" * 70)
    print("ok            :", result.ok)
    print("verdict       :", result.verdict, "-", result.reason)
    print("steps applied :", result.steps_applied)
    print("objects       :", [o.Name for o in doc.Objects])
    print("owned         :", sorted(pipeline.owned))
    print("undo entries  :", list(doc.UndoNames))
    print("render        :", result.render)
    print("event log     :", args.log)

    fillet = next((o for o in doc.Objects if "Fillet" in o.TypeId), None)
    if fillet is not None:
        print()
        print("-- the assertion the slice is for --")
        print("fillet references :", list(fillet.Base[1]), "on", fillet.Base[0].Name)
        pad = next((o for o in doc.Objects if o.TypeId == "PartDesign::Pad"), None)
        if pad is not None:
            before = fillet.Shape.BoundBox.ZMax
            pad.Length = float(pad.Length) * 2
            doc.recompute()
            print("after doubling the pad length:")
            print("  fillet state :", list(fillet.State))
            print("  height       :", before, "->", fillet.Shape.BoundBox.ZMax)
            print("  still valid  :", list(fillet.State) == ["Up-to-date"])

    if args.save:
        doc.saveAs(args.save)
        print("saved         :", args.save)

    return 0 if result.ok else 1


def _printer():
    """Narrate the run the way the panel would."""
    from core.events import (
        APPLIED, BLOCKED, ERROR, PLAN, PLAN_REVISED, PREFLIGHT_FAILED, RENDER,
        REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT,
    )

    state = {"phase": None}

    def show(event):
        data = event.data
        if event.kind == THOUGHT:
            if state["phase"] != data["phase"]:
                state["phase"] = data["phase"]
                sys.stdout.write("\n[%s thinking] " % data["phase"])
            sys.stdout.write(data["text"])
            sys.stdout.flush()
            return

        state["phase"] = None
        if event.kind in (PLAN, PLAN_REVISED):
            if data.get("reading"):
                print("\n\nHOW THE DRAWING WAS READ:")
                for line in data["reading"].splitlines():
                    print("  " + line)
            print("\n\n%s:" % ("PLAN" if event.kind == PLAN else "PLAN REVISED"))
            for n, step in enumerate(data["steps"], start=1):
                print("  %d. %s" % (n, step))
        elif event.kind == STEP_STARTED:
            print("\n> Step %d/%d: %s" % (data["index"], data["total"], data["description"]))
        elif event.kind == TOOL_CALL:
            code = data["arguments"].get("code", "")
            print("  %s(%s)" % (data["name"], data["arguments"].get("label", "")))
            for line in code.strip().splitlines():
                print("    | " + line)
        elif event.kind == TOOL_RESULT:
            print("  -> " + str(data["result"].get("status")))
        elif event.kind == PREFLIGHT_FAILED:
            print("  x attempt %d failed:" % data["attempt"])
            for line in data["traceback"].strip().splitlines()[-6:]:
                print("    " + line)
        elif event.kind == APPLIED:
            print("  applied %r, created %s" % (data["label"], data.get("created")))
        elif event.kind == RENDER:
            print("  render: " + data["path"])
        elif event.kind == REVIEW_VERDICT:
            print("\nREVIEW: %s - %s" % (data["verdict"], data["reason"]))
        elif event.kind == BLOCKED:
            print("\nBLOCKED: " + data["reason"])
        elif event.kind == ERROR:
            print("\nERROR: " + data["message"])

    return show


if __name__ == "__main__":
    sys.exit(main())
