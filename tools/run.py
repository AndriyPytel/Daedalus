#!/usr/bin/env python
"""
One run of one sheet, through `copilot.sdk.Client`.

The useful half of the old `tools/ab_run.py`: the RLM_AGENTIC arm and its
retry/`state.json` bookkeeping move to `tools/lane.py`, the one caller that
walks more than one of these. `run_slice.py`'s `--allow-user-objects` survives
as the same flag here, answering the Owned Object prompt yes -- safe only
because a script pointed at a scratch Document is the only caller that
should ever say so.

    .venv/bin/python tools/run.py --intent "Build the Phase 1 model" \
        --attach tests/modelmania/mm25-p1-1.pdf --budget-usd 20
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", required=True)
    parser.add_argument("--attach", nargs="*", default=[])
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--budget-usd", type=float, default=None,
                        help="Ceiling on what this run may spend; unset runs to completion")
    parser.add_argument("--log", default="/tmp/run_events.jsonl")
    parser.add_argument("--save", default="")
    parser.add_argument("--allow-user-objects", action="store_true",
                        help="Answer yes to the Owned Object prompt instead of refusing")
    args = parser.parse_args()

    for lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from copilot.sdk import Client
    from core.events import COST
    from core.llm import ClaudeCodeClient

    llm = ClaudeCodeClient(model=args.model, effort=args.effort) if args.model else None
    client = Client(llm=llm, confirm=lambda kinds: args.allow_user_objects is True)
    client.events.log_to(args.log)

    spent = {"usd": 0.0}

    def track(event, spent=spent):
        if event.kind == COST:
            spent["usd"] += event.data.get("usd") or 0.0

    client.watch(track)

    cancelled = (lambda: spent["usd"] >= args.budget_usd) if args.budget_usd is not None else None
    result = client.send(args.intent, attachments=list(args.attach), cancelled=cancelled)

    print("ok            :", result.ok)
    print("verdict       :", result.verdict, "-", result.reason)
    print("steps applied :", result.steps_applied)
    print("spent         : $%.2f" % spent["usd"])
    print("event log     :", args.log)

    if args.save:
        client.save(args.save)
        print("saved         :", args.save)

    client.close()
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
