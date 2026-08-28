"""
Build `tests/recorded/evidence_kind_ratio.json` from `logs/sessions/*.jsonl` and
the evidence sidecars beside `logs/parts/*.yaml` (both gitignored -- FCC-231's
established substitute) for FCC-248, whose AC2 threshold is "the evidence file
holds at least one entry the model itself wrote", i.e. one `design.grouped_
conflicts` puts in the `conflict` bucket rather than `gap`/`check hazard`.

FCC-244 measured that bucket at 2 of 47 recorded *descriptions*
(`tests/recorded/vocabulary_kinds.json`, run through `merge()` alone) and
called the ratio "close to noise, but not there". This script measures the
same bucket a second way, over real runs rather than one merge() call each:
of 26 sessions under `logs/sessions/` that reached Design, wrote a reading and
still have that reading's evidence sidecar on disk, 23 hold at least one
`conflict`-kind entry -- 88%, not 4%.

`logs/sessions/` and `logs/parts/` are the user's own, live and growing while
this was measured (an A/B run in progress), and `part_file.logged()` writes
the evidence sidecar to a path derived from the *reading's* name -- a second
run of the same sheet overwrites the first run's sidecar in place rather than
writing a second one beside it, the way `part_file.saved()` does for the
reading itself. Session `480b9995421e443d97b439a4f55bc3fe` was rewritten by
the live app between this ticket being filed (23 entries, 22 gap/1 conflict)
and this script being run against it (29 entries, 12 gap/17 conflict) --
caught mid-measurement, not reconstructed after the fact. That silent
overwrite is its own defect, worth its own ticket, and is reported rather
than fixed here.

The fixture this writes is therefore a snapshot of a moving target, not a
stable corpus: re-running this script tomorrow will almost certainly produce
different counts, because the sessions themselves will have changed, not
because `crosswise`/`_every_number_is_the_sheets` did. `captured_at` in the
output records when this particular run was taken, and the pinned test that
reads it back cites that timestamp rather than claiming the number as a law
of the code.

The gap between the two numbers is not sampling noise. `core.design.crosswise`
and `core.loop._every_number_is_the_sheets` are machine checks that run only
in the full pipeline `merge()` alone never reaches (`crosswise` needs the
reader's per-feature `seen_in`; `_every_number_is_the_sheets` needs a drawing
to hold numbers against), and neither sets a `kind` on the entries it appends
-- so `design._which_kind_it_is`'s default (undefined kind reads as
`A_CONFLICT`) puts them in the same bucket a model-written disagreement falls
into. `crosswise` alone accounts for 45 of the entries behind the 23 sessions
counted here. Excluding both checks' output still leaves 19 of 26 sessions
with a `conflict`-kind entry, so the gap is not explained by those two checks
alone either.

Session ids behind the 23-with, 3-without split, for whoever re-runs this:
with a conflict-kind entry -- 069d21dbac10416392533fe7c06b62d4,
0aed73b0953642fd8e53f2476bc71677, 247c2aaed0734866a8aa9d169f7303bd,
3fdc7475dbda4d50b57b157cd67dea59, 45ee20f1f9a34c60a79bfd62d2dace8c,
480b9995421e443d97b439a4f55bc3fe, 4eebde83eaa74822a268716cb5b1a729,
51d7a2f16e224230b5b74f126c7cf8f5, 5d9249424bce45d2b25485002ba1c630,
66a15a679b574ef49848d47632258c78, 7330dead9b7b47a7a32005ad4ebf2760,
78471005550b4105b7446fa2f5605ba2, 7cbde8a3e04d4b40b88933e6dc5bbe02,
8f70ae7d93154154ada691ee2fc52a1c, 98a5ead873c94103ba8a2baf653dd436,
afa0142baef847b0a9cd9139390af993, b70b6931ea9e4434bb2ba37e23c6222f,
b83650955ecd41c89a54907fe84ea8ba, cd594cb0281244f7b366d94dd8740714,
cffbc01e488b45499b996ece0765b47f, f1f13395f7454a53afb790366e03a7ea,
f30e802ef400453e8d9a8c7e476c0d73, f5d8007284aa4a57845782918305d6f0.
Gaps only -- 0de34f88d1274dcebb02ac67e0e52fb0, 1b6cd765da1e4304a6215035d1ec5a56,
a7b95d14ea20491c8dd6c654b3285bf3.

Run from the repo root with `logs/` present:
`python tools/extract_evidence_kind_ratio.py`. It reads every session under
`logs/sessions/` that logged a `part` event, and where that event's own path
still resolves to an evidence sidecar (`core.part_file.evidence`) on disk,
records only whether the sidecar holds a `conflict`-kind entry and whether it
still does once `crosswise`'s and `_every_number_is_the_sheets`'s own output is
filtered back out -- never the sidecar's contents themselves, which is what
`tests/recorded` never carries (they are a user's own drawings' numbers).
"""
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import part_file
from core.design import A_CONFLICT, _which_kind_it_is

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "logs", "sessions")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "tests", "recorded", "evidence_kind_ratio.json")


def _last_part_event(path):
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
            if event.get("kind") == "part":
                last = event
    return last


def _is_crosswise_or_untraced(entry):
    about = entry.get("about")
    stands = entry.get("stands") or ""
    if isinstance(about, str) and about.startswith("the value ") and "on this part" in about:
        return True
    return "has the one axis" in stands or "two axes cross" in stands


def main():
    sessions = []
    for path in sorted(glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl"))):
        session = os.path.splitext(os.path.basename(path))[0]
        event = _last_part_event(path)
        if not event:
            continue
        reading_path = (event.get("data") or {}).get("path", "")
        evidence_path = part_file.evidence(reading_path)
        if not os.path.isfile(evidence_path):
            continue
        gathered = part_file.read(evidence_path)
        conflicts = gathered.get("conflicts") or []
        has_conflict = any(_which_kind_it_is(c) == A_CONFLICT for c in conflicts)
        has_conflict_excl = any(_which_kind_it_is(c) == A_CONFLICT
                                and not _is_crosswise_or_untraced(c) for c in conflicts)
        sessions.append({"session": session, "has_conflict_kind": has_conflict,
                         "has_conflict_kind_excluding_crosswise_and_untraced": has_conflict_excl})

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "sessions": sessions}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print("%d sessions written to %s" % (len(sessions), OUT_PATH))


if __name__ == "__main__":
    main()
