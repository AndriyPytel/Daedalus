#!/usr/bin/env python
"""
One run of one sheet, through `sdk.Client`.

The useful half of the old `tools/ab_run.py`: the RLM_AGENTIC arm and its
retry/`state.json` bookkeeping move to `tools/lane.py`, the one caller that
walks more than one of these. A deletion of a user object is approved only
where the Intent names it.

    .venv/bin/python src/tools/run.py --intent "Build the Phase 1 model" \
        --attach tests/modelmania/mm25-p1-1.pdf --budget-usd 20
"""
import argparse
import os
import sys
import tempfile

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(_SRC))


def base_document(step_path):
    """
    A saved Document whose one Body has *step_path*'s solid as its BaseFeature,
    so an edit adds features to that Body and `export_step` writes the Body alone,
    never the imported solid beside it.
    """
    import tempfile

    import FreeCAD
    import Import

    doc = FreeCAD.newDocument("Edit")
    Import.insert(step_path, doc.Name)
    base = next(obj for obj in doc.Objects
                if getattr(obj, "Shape", None) is not None and obj.Shape.Solids)
    import sdk

    repaired, note = sdk.repair_base(base.Shape)
    if note:
        print("base repair   :", note)
    if repaired is not base.Shape:
        base.Shape = repaired
    body = doc.addObject("PartDesign::Body", "Body")
    body.BaseFeature = base
    doc.recompute()
    handle, path = tempfile.mkstemp(prefix="fcc-edit-", suffix=".FCStd")
    os.close(handle)
    os.remove(path)
    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)
    return path


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


def save_each_step(client, save, only=None):
    """
    Rewrite the STEP *save* after every applied Step, so a run the lane kills at its time limit leaves the build it had.

    A save that is not a STEP waits for the final export, and a Document with no solid yet writes nothing.
    """
    from sdk import APPLIED

    if not save.lower().endswith((".step", ".stp")):
        return

    def rewrite(event):
        if event.kind == APPLIED and client.has_solid():
            write_step(client, save, only)

    client.watch(rewrite)


def finish(client, result, save, only=None):
    """
    Write *save* and give the process exit code: 0 for a passing run, 1 for a failed one.

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
    parser.add_argument("--intent", required=True)
    parser.add_argument("--attach", nargs="*", default=[])
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default="Claude (subscription)",
                        help="Which provider sells --model, as core.model_catalog names it")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--budget-usd", type=float, default=None,
                        help="Ceiling on what this run may spend; unset runs to completion")
    parser.add_argument("--log", default="/tmp/run_events.jsonl")
    parser.add_argument("--save", default="")
    parser.add_argument("--domain", default="part",
                        help="The Domain the agent arm builds under, part, assembly or bim; assembly builds from the STEP parts in --attach and bim from the floor plan in --attach")
    parser.add_argument("--base-document", default=None,
                        help="An editing run under the Assembly Domain: the saved Document the agent opens and changes")
    parser.add_argument("--base-ifc", default=None,
                        help="An editing run under the BIM Domain: the native IFC the agent opens in nativeifc and changes")
    parser.add_argument("--base-step", default=None,
                        help="An editing run: the part to edit, the BaseFeature of the Document's Body")
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
    seed = base_document(args.base_step) if args.base_step else args.base_document
    if args.base_ifc:
        scratch = tempfile.mkdtemp(prefix="fcc-native-")
        seed = sdk.native_document(args.base_ifc, os.path.join(scratch, "work.ifc"), os.path.join(scratch, "seed.FCStd"))
    client = Client(document=seed, llm=llm)
    client.events.log_to(args.log)
    if seed:
        client.kernel.check("pass", document=seed)

    spent = {"usd": 0.0}

    def track(event, spent=spent):
        if event.kind == COST:
            spent["usd"] += event.data.get("usd") or 0.0

    client.watch(track)
    save_each_step(client, args.save, "Body" if args.base_step else None)

    cancelled = (lambda: spent["usd"] >= args.budget_usd) if args.budget_usd is not None else None
    result = client.agent(args.intent, attachments=list(args.attach), cancelled=cancelled,
                          base=bool(seed), domain=args.domain,
                          budget_usd=args.budget_usd)

    print("ok            :", result.ok)
    print("reason        :", result.reason)
    print("steps applied :", result.steps_applied)
    print("spent         : $%.2f" % spent["usd"])
    print("event log     :", args.log)

    code = finish(client, result, args.save, "Body" if args.base_step else None)
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
