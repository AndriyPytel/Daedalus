#!/usr/bin/env python
"""
One run of one sheet, through `sdk.Client`.

The useful half of the old `tools/ab_run.py`: the RLM_AGENTIC arm and its
retry/`state.json` bookkeeping move to `tools/lane.py`, the one caller that
walks more than one of these. `run_slice.py`'s `--allow-user-objects` survives
as the same flag here, answering the Owned Object prompt yes -- safe only
because a script pointed at a scratch Document is the only caller that
should ever say so, and `nobody_to_ask` answers a question the loop raises,
since nobody is watching a headless run and declining is what sends it down
the decide-for-yourself path.

    .venv/bin/python src/tools/run.py --intent "Build the Phase 1 model" \
        --attach tests/modelmania/mm25-p1-1.pdf --budget-usd 20
"""
import argparse
import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(_SRC))


def client_for(provider, model_id, effort):
    """
    The client for *model_id* at *provider*, in a process no panel ran in.

    `core.model_registry` is filled by the panel's own fetch, which never runs
    here, so the provider is named on the command line and recorded before
    `client_from_settings` is asked to resolve it. The service worker solves the same problem through `sdk.remember_model`.
    """
    import sdk

    sdk.remember_model(model_id, provider)
    return sdk.client_from_settings(model_id, effort)


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


def finish(client, result, save, only=None):
    """
    Write *save* and give the process exit code: 0 for a passing run, 1 for a failed one.

    A Document with no solid writes nothing, since an empty STEP is a file a
    scorer reads as a candidate, and exits `NO_SOLID_EXIT` whatever the verdict was.
    *only* names the one object a STEP carries, which an edit run sets to its Body.
    """
    if not client.has_solid():
        return NO_SOLID_EXIT
    if save:
        if save.lower().endswith((".step", ".stp")):
            client.export_step(save, only) if only else client.export_step(save)
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
    parser.add_argument("--allow-user-objects", action="store_true",
                        help="Answer yes to the Owned Object prompt instead of refusing")
    parser.add_argument("--conflicts", choices=("on", "off"), default="on",
                        help="The append-only model's own A/B toggle: 'off' drops the "
                             "conflict record at the Gate, everything else about the run "
                             "unchanged")
    parser.add_argument("--base-step", default=None,
                        help="An editing run: the part to edit, the BaseFeature of the Document's Body")
    args = parser.parse_args()

    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import FreeCAD  # noqa: F401

    from sdk import COST, Budgets, Client, nobody_to_ask

    llm = client_for(args.provider, args.model, args.effort) if args.model else None
    seed = base_document(args.base_step) if args.base_step else None
    client = Client(document=seed, llm=llm, confirm=lambda kinds: args.allow_user_objects is True)
    client.events.log_to(args.log)
    if seed:
        client.kernel.check("pass", document=seed)

    spent = {"usd": 0.0}

    def track(event, spent=spent):
        if event.kind == COST:
            spent["usd"] += event.data.get("usd") or 0.0

    client.watch(track)

    budgets = Budgets(record_conflicts=False) if args.conflicts == "off" else None
    cancelled = (lambda: spent["usd"] >= args.budget_usd) if args.budget_usd is not None else None
    result = client.send(args.intent, attachments=list(args.attach), cancelled=cancelled,
                         ask=nobody_to_ask, budgets=budgets)

    print("ok            :", result.ok)
    print("verdict       :", result.verdict, "-", result.reason)
    print("steps applied :", result.steps_applied)
    print("unverified    :", ", ".join(getattr(result, "unmet", None) or ()) or "none")
    print("spent         : $%.2f" % spent["usd"])
    print("event log     :", args.log)

    code = finish(client, result, args.save, "Body" if args.base_step else None)
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
