#!/usr/bin/env python
"""
One run of one sheet, through `copilot.sdk.Client`.

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
import json
import os
import sys
import time

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(_SRC))


ARMS = ("pipeline", "agent", "oneshot", "nopreflight")


def client_for(provider, model_id, effort):
    """
    The client for *model_id* at *provider*, in a process no panel ran in.

    `core.model_registry` is filled by the panel's own fetch, which never runs
    here, so the provider is named on the command line and recorded before
    `client_from_settings` is asked to resolve it.
    """
    from core import model_registry
    from core.llm.factory import client_from_settings

    model_registry.remember(model_id, provider, model_id)
    return client_from_settings(model_id, effort)


def parser():
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
    parser.add_argument("--arm", choices=ARMS, default="pipeline",
                        help="pipeline is the fixed phase loop; nopreflight is it with every "
                             "Step's code going straight to Apply; agent is one model choosing "
                             "its own route; oneshot is one call executed once")
    parser.add_argument("--max-turns", type=int, default=60,
                        help="The agent arm's turn ceiling")
    parser.add_argument("--base-step", default=None,
                        help="An editing run: the part to edit, the BaseFeature of the Document's Body")
    return parser


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
    body = doc.addObject("PartDesign::Body", "Body")
    body.BaseFeature = base
    doc.recompute()
    handle, path = tempfile.mkstemp(prefix="fcc-edit-", suffix=".FCStd")
    os.close(handle)
    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)
    return path


def header(args):
    """What a log needs to say to reproduce its run: the arm, the model dials and the commit."""
    import subprocess

    root = os.path.dirname(_SRC)
    commit = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip())
    return {"arm": args.arm, "model": args.model, "provider": args.provider,
            "effort": args.effort, "max_turns": args.max_turns,
            "budget_usd": args.budget_usd, "conflicts": args.conflicts,
            "commit": commit, "dirty": dirty, "argv": sys.argv[1:]}


def write_header(path, args):
    """The log's first line: a `run` record in the events' own shape, for `replay` to carry through."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "run", "at": time.time(), "data": header(args)},
                                default=str, sort_keys=True) + "\n")


def main():
    """
    One run, headless.

    FreeCAD is imported before anything of the addon's: importing it is what
    puts its `AdditionalPythonPackages` on `sys.path`, and that is where
    litellm, httpx and pymupdf are installed.
    """
    args = parser().parse_args()

    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import FreeCAD  # noqa: F401

    from copilot.sdk import Client
    from core.events import COST
    from core.loop import nobody_to_ask
    from core.loop.budgets import Budgets

    write_header(args.log, args)
    llm = client_for(args.provider, args.model, args.effort) if args.model else None
    seed = base_document(args.base_step) if args.base_step else None
    client = Client(document=seed, llm=llm, confirm=lambda kinds: args.allow_user_objects is True)
    client.events.log_to(args.log)
    if seed and args.arm == "agent":
        client.kernel.check("pass", document=seed)

    spent = {"usd": 0.0}

    def track(event, spent=spent):
        if event.kind == COST:
            spent["usd"] += event.data.get("usd") or 0.0

    client.watch(track)

    cancelled = (lambda: spent["usd"] >= args.budget_usd) if args.budget_usd is not None else None
    if args.arm == "agent":
        result = client.agent(args.intent, attachments=list(args.attach), cancelled=cancelled,
                              max_turns=args.max_turns)
    elif args.arm == "oneshot":
        result = client.oneshot(args.intent, attachments=list(args.attach))
    else:
        budgets = Budgets(record_conflicts=args.conflicts != "off",
                          preflight=args.arm != "nopreflight")
        result = client.send(args.intent, attachments=list(args.attach), cancelled=cancelled,
                             ask=nobody_to_ask, budgets=budgets)

    print("ok            :", result.ok)
    print("verdict       :", result.verdict, "-", result.reason)
    print("steps applied :", result.steps_applied)
    print("unverified    :", ", ".join(getattr(result, "unmet", None) or ()) or "none")
    print("spent         : $%.2f" % spent["usd"])
    print("event log     :", args.log)

    if args.save and args.arm in ("agent", "oneshot") and not client.has_geometry():
        print("saved         : nothing, the Document holds no geometry")
    elif args.save:
        if args.save.lower().endswith((".step", ".stp")):
            client.export_step(args.save)
        else:
            client.save(args.save)
        print("saved         :", args.save)

    client.close()
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
