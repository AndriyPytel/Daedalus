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
    """
    Drive one slice run end to end and report what the Document ended up with.

    The apply pipeline dispatches each callable directly, because this script
    already runs on the main thread.
    """
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
    from core.events import EventStream
    from core.apply import build_registry
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
            dispatch=lambda func, timeout=None: func(),
            confirm=lambda kinds: args.allow_user_objects is True,
            events=events,
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
    """
    Narrate the run the way the panel does — through `core.narrate`, so it
    cannot say anything different.

    This used to be a second `if/elif` over the same kinds, and it drifted:
    long after ADR-0018 gave the description an event of its own it was still
    looking for it on the `plan` event, so it printed none, and it
    never learned `attached`, `assertion`, `question` or `answer` at all.

    What is left here is the shapes the narrator does not carry, because they
    are not sentences: reasoning as it streams, the Steps of a Plan, and the
    code of a tool call.
    """
    from core.events import PLAN, PLAN_REVISED, THOUGHT, TOOL_CALL
    from core.narrate import narrate

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
        said = narrate(event)
        if said.line:
            print("\n" + said.line)
        if said.title:
            print("\n" + said.title.upper() + ":")
            for line in said.body.splitlines():
                print("  " + line)
        if said.status:
            print("  " + said.status)

        if event.kind in (PLAN, PLAN_REVISED):
            print("\n%s:" % ("PLAN" if event.kind == PLAN else "PLAN REVISED"))
            for n, step in enumerate(data["steps"], start=1):
                print("  %d. %s" % (n, step))
        elif event.kind == TOOL_CALL:
            print("  %s(%s)" % (data["name"], data["arguments"].get("label", "")))
            for line in data["arguments"].get("code", "").strip().splitlines():
                print("    | " + line)

    return show


if __name__ == "__main__":
    sys.exit(main())
