"""
Build `tests/recorded/datum_satisfies_steps.json` from `logs/sessions/*.jsonl`
(gitignored, hence the tracked fixture this writes — FCC-231's established
substitute) for FCC-242, which needs a Step's own prose *and* the acceptance
entries its `satisfies` cites, together. `tests/recorded/vocabulary_steps.json`
cannot serve this: it was built for FCC-233's op-verb replay, which had the
`(satisfies: ...)` suffix stripped from every Step on purpose (op verbs were
the signal, citations were noise for it) — its 66 sessions carry no citation
at all. This is a second, sibling fixture, not a replacement; it does not
touch `vocabulary_steps.json`, whose residue count `test_op_vocabulary_
replay.py` pins.

Run from the repo root with `logs/` present: `python tools/
extract_satisfies_fixture.py`. It reads every session under `logs/sessions/`
that logged at least one `plan` event, takes that session's *last* `plan`
event (a revised Plan supersedes the one before it), and keeps `acceptance`
and `steps` verbatim — the `steps` a `plan` event logs are already `core.
loop._steps_of`'s output, so each Step's prose already carries its own
`(satisfies: address, address (flag), ...)` suffix exactly as the Gate shows
it; nothing here re-derives or reformats it.

68 sessions logged a `plan` event as of this run, including
`480b9995421e443d97b439a4f55bc3fe` — the session FCC-242 itself is about,
where Step 8 cites `hole-right -> axis` and `counterbore-right -> position ->
the bottom face` while its own prose calls the entry point "the lower ear's
underside" and never writes either cited datum term — and
`afa0142baef847b0a9cd9139390af993`, the stalled session FCC-239 measured.
Neither is in `vocabulary_steps.json`, which predates both.
"""
import glob
import json
import os

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "logs", "sessions")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "tests", "recorded", "datum_satisfies_steps.json")


def _last_plan_event(path):
    last = None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("kind") == "plan":
                last = event
    return last


def main():
    plans = []
    for path in sorted(glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl"))):
        session = os.path.splitext(os.path.basename(path))[0]
        event = _last_plan_event(path)
        if not event:
            continue
        data = event.get("data") or {}
        steps = [str(step) for step in (data.get("steps") or ()) if str(step).strip()]
        acceptance = [str(entry) for entry in (data.get("acceptance") or ())]
        if not steps:
            continue
        plans.append({"session": session, "acceptance": acceptance, "steps": steps})

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"plans": plans}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print("%d sessions written to %s" % (len(plans), OUT_PATH))


if __name__ == "__main__":
    main()
