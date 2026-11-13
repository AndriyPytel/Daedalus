#!/usr/bin/env python
"""
One run of one sheet, through `sdk.Client`.

The useful half of the old `tools/ab_run.py`: the RLM_AGENTIC arm and its
retry/`state.json` bookkeeping move to `tools/lane.py`, the one caller that
walks more than one of these. A deletion of a user object is approved only
where the Intent names it.

    .venv/bin/python src/tools/run.py --intent "Build the Phase 1 model" \
        --task tests/modelmania/mm25-p1-1.pdf --budget-usd 20
"""
import argparse
import json
import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(_SRC))

from task import STEP, Task, seed_document  # noqa: E402


NO_SOLID_EXIT = 3


def write_step(client, path, only=None):
    """
    Write *path* as a STEP through a sibling file renamed over it, so a process killed or failing mid-export leaves the earlier STEP whole.

    The sibling keeps the `.step` extension, since the export picks its format from it.
    """
    root, extension = os.path.splitext(path)
    partial = root + ".partial" + extension
    try:
        client.export_step(partial, only) if only else client.export_step(partial)
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def step_numbers(path):
    """The `solids`, `valid` and `volume` of the STEP at *path*; valid is OCC's verdict and the mesh gate `core.manifold` mirrors."""
    import Part

    import sdk

    shape = Part.read(path)
    solids = len(shape.Solids)
    return {"solids": solids,
            "valid": bool(solids) and shape.isValid() and sdk.manifold.check_fc(shape) is None,
            "volume": shape.Volume}


def write_result(path, base_volume):
    """Write `step_numbers(path)` and *base_volume* to `<stem>.json` beside *path*, through a sibling renamed over it."""
    result = os.path.splitext(path)[0] + ".json"
    with open(result + ".partial", "w", encoding="utf-8") as handle:
        json.dump({**step_numbers(path), "base_volume": base_volume}, handle)
    os.replace(result + ".partial", result)


def save_each_step(client, save, only=None, record=None):
    """
    Rewrite the STEP *save* after every applied Step and hand it to *record*, so a run the lane kills at its time limit leaves the build it had.

    A save that is not a STEP waits for the final export, and a Document with no solid yet writes nothing.
    """
    from sdk import APPLIED

    if not save.lower().endswith((".step", ".stp")):
        return

    def rewrite(event):
        if event.kind == APPLIED and client.has_solid():
            write_step(client, save, only)
            if record:
                record(save)

    client.watch(rewrite)


def finish(client, result, save, only=None, record=None):
    """
    Write *save*, hand a STEP to *record*, and give the process exit code: 0 for a passing run, 1 for a failed one.

    A Document with no solid, or for an IFC no Arch object, writes nothing, since an empty file is one a
    scorer reads as a candidate, and exits `NO_SOLID_EXIT` whatever the verdict was.
    *only* names the one object a STEP carries, which an edit run sets to its Body.
    """
    if save and save.lower().endswith(".ifc"):
        if client.export_ifc(save) is None:
            return NO_SOLID_EXIT
        print("saved         :", save)
        return 0 if result.ok else 1
    if not client.has_solid():
        return NO_SOLID_EXIT
    if save:
        if save.lower().endswith((".step", ".stp")):
            write_step(client, save, only)
            if record:
                record(save)
        else:
            client.save(save)
        print("saved         :", save)
    return 0 if result.ok else 1


def main():
    """
    One run, headless.

    FreeCAD is imported before anything of the addon's: importing it is what
    puts its `AdditionalPythonPackages` on `sys.path`, and that is where
    litellm, httpx and pymupdf are installed.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=None,
                        help="A task folder, which carries its own Domain, Intent and files, or a sheet that --intent is built from")
    parser.add_argument("--intent", default=None,
                        help="What to build from the --task sheet, or on its own when there is no --task")
    parser.add_argument("--native", action="store_true",
                        help="A BIM edit task opens its input.ifc in nativeifc rather than its input.FCStd")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default="Claude (subscription)",
                        help="Which provider sells --model, as core.model_catalog names it")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--budget-usd", type=float, default=None,
                        help="Ceiling on what this run may spend; unset runs to completion")
    parser.add_argument("--log", default="/tmp/run_events.jsonl")
    parser.add_argument("--save", default="")
    args = parser.parse_args()

    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import FreeCAD  # noqa: F401

    import sdk
    from sdk import COST, EFFORTS, Client

    if args.effort not in EFFORTS:
        parser.error("--effort must be one of %s" % ", ".join(EFFORTS))
    llm = sdk.client_for(args.provider, args.model, args.effort) if args.model else None
    try:
        task = Task.open(args.task, args.intent, args.native)
    except ValueError as refusal:
        parser.error(str(refusal))
    stepped = args.save.lower().endswith((".step", ".stp"))
    edits_a_step = bool(task.seed) and task.seed.kind == STEP
    keep = os.path.splitext(args.save)[0] + ".base.step" if stepped and edits_a_step else None
    seed = seed_document(task, keep)
    base_volume = step_numbers(keep)["volume"] if keep else None
    record = (lambda path: write_result(path, base_volume)) if stepped else None
    only = "Body" if edits_a_step else None
    client = Client(document=seed, llm=llm)
    client.events.log_to(args.log)

    spent = {"usd": 0.0}

    def track(event, spent=spent):
        if event.kind == COST:
            spent["usd"] += event.data.get("usd") or 0.0

    client.watch(track)
    save_each_step(client, args.save, only, record)

    cancelled = (lambda: spent["usd"] >= args.budget_usd) if args.budget_usd is not None else None
    result = client.agent(task.intent, attachments=task.attachments, cancelled=cancelled,
                          base=bool(seed), domain=task.domain,
                          budget_usd=args.budget_usd)

    print("ok            :", result.ok)
    print("reason        :", result.reason)
    print("steps applied :", result.steps_applied)
    print("spent         : $%.2f" % spent["usd"])
    print("event log     :", args.log)

    code = finish(client, result, args.save, only, record)
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
