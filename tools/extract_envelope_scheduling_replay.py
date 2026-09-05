"""
Build `tests/recorded/envelope_check_scheduling_replay.json` from
`logs/sessions/*.jsonl` and `logs/ab-rlm-agentic/**/*.jsonl` (gitignored,
hence the tracked fixture -- FCC-231's established substitute), answering the
one question that decides whether FCC-247's free half (the deterministic
envelope check at `core/loop.py:2356`, `the_envelope_measured`) should be
scheduled per Step: after the Step whose own `satisfies` completes the
envelope's addresses, does any later Step in the same Plan change the built
part's bounding box?

**Method.** For every session whose last Plan has exactly 3 envelope-type
acceptance entries (the count `the_envelope_measured` requires to run at
all) and at least one Step after the last Step citing an envelope address,
replay every one of that session's own successfully-`applied` Step codes in
one FreeCAD document (`core.kernel.Kernel`, real FreeCAD via
`freecadcmd`) and measure the built solid's `optimalBoundingBox` after each
Step. Restricted to sessions with exactly one Plan revision, where the
recorded `applied` count matches the last Plan's own Step count one-for-one
-- a session with a mid-run replan needs its carried-over Steps reconciled
against the final Plan's numbering first, which this tool does not attempt.

**What it found, across the 13 sessions that qualify.** In 12, the bounding
box measured right after the Step that completes the envelope's own
citations never moves again -- the base Pad sets the overall footprint and
nothing later touches it. In one, `f1f13395f7454a53afb790366e03a7ea` (a
latched lid), the lid Pad (Step 1) completes the two envelope citations at
`[114, 216, 300]` mm, but the front and rear latch Steps (4-6) -- which do
not cite any envelope address in their own `satisfies`, because they are
described relative to faces, not overall size -- stand proud of the lid and
grow the sorted bounding box to `[114, 228, 306]` mm, a 12 mm move, only
after the envelope check's own trigger condition was already met. A
deterministic per-Step check gated on "this Step's own citations complete
the envelope" would have compared the lid's own Y/Z against the final
target and failed a part that was still correctly under construction: the
false-fire this tool exists to rule in or out.

**The other side, `a7b95d14ea20491c8dd6c654b3285bf3`.** This is the one
session in the whole corpus where the deterministic envelope check itself
failed at the end (`tests/recorded/assertion_scheduling_cost.json` already
carries the 46-Step cost/benefit count; this session isn't in it, because
the envelope check's own failure text is a whole-part sentence, not an
address, so it can never match a Step's own citation the way a feature-level
`unmet` entry does). Its envelope citations complete at Step 0 (a hex prism
Pad, `Length = 9.3`), and nothing later in that 7-Step Plan touches height.
Replaying Step 0 alone measures the built solid at `[9.3, 16.0, 18.475]` mm
against a `[9.30, 16.00, 17.77]` mm target -- already wrong, 6 Steps before
the run's own `assertion()` ever looked.

Live-replaying every candidate here would make this tool itself require
FreeCAD; the two decisive sessions the replay found (kept live as
`tests/test_envelope_check_scheduling_disproven.py`'s fixtures) are the ones
whose codes this fixture carries in full, alongside a summary row for every
other candidate session for the audit trail.

Run from the repo root with `logs/` present:
    python tools/extract_envelope_scheduling_replay.py
"""

from __future__ import annotations

import glob
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT = os.path.join(_ROOT, "tests", "recorded", "envelope_check_scheduling_replay.json")

_SATISFIES_RE = re.compile(r"\(satisfies:\s*(.*?)\)\s*$")


def _split_step(step):
    m = _SATISFIES_RE.search(step)
    if not m:
        return step, []
    cited = [c.strip() for c in m.group(1).split(",") if c.strip()]
    return step[: m.start()].rstrip(), cited


def _norm(address):
    return address.split(":")[0].strip().lower()


def _address_of(acceptance_line):
    return acceptance_line.split(":")[0].strip().lower()


def _load_events(path):
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _successful_step_codes(events):
    codes = []
    for i, e in enumerate(events):
        if e.get("kind") == "tool_call" and e["data"].get("name") == "apply_step":
            code = e["data"]["arguments"].get("code", "")
            for j in range(i + 1, len(events)):
                if events[j].get("kind") == "tool_result" and events[j]["data"].get("name") == "apply_step":
                    result = events[j]["data"].get("result") or {}
                    if result.get("status") == "applied":
                        codes.append(code)
                    break
    return codes


def _candidate_sessions(all_paths):
    out = []
    for path in all_paths:
        events = _load_events(path)
        plans = [e for e in events if e.get("kind") == "plan"]
        if len(plans) != 1:
            continue
        session = os.path.splitext(os.path.basename(path))[0]
        steps = plans[0]["data"].get("steps") or []
        acc = plans[0]["data"].get("acceptance") or []
        envelope_entries = [a for a in acc if _address_of(a).startswith("envelope")]
        if len(envelope_entries) != 3:
            continue
        env_citing_idx = [
            idx for idx, step in enumerate(steps)
            if any(_norm(c).startswith("envelope") for _, c in [_split_step(step)] for c in c)
        ]
        if not env_citing_idx:
            continue
        env_complete_idx = max(env_citing_idx)
        if env_complete_idx >= len(steps) - 1:
            continue
        codes = _successful_step_codes(events)
        if len(codes) != len(steps):
            continue
        out.append({
            "session": session,
            "path": path,
            "n_steps": len(steps),
            "env_complete_idx": env_complete_idx,
            "codes": codes,
        })
    return out


def main():
    logs_root = os.path.join(_ROOT, "logs")
    all_paths = sorted(glob.glob(os.path.join(logs_root, "sessions", "*.jsonl")))
    all_paths += sorted(glob.glob(os.path.join(logs_root, "ab-rlm-agentic", "**", "*.jsonl"), recursive=True))

    candidates = _candidate_sessions(all_paths)

    decisive = {"a7b95d14ea20491c8dd6c654b3285bf3", "f1f13395f7454a53afb790366e03a7ea"}

    summary = []
    step_codes = {}
    for c in candidates:
        row = {
            "session": c["session"],
            "n_steps": c["n_steps"],
            "env_complete_idx": c["env_complete_idx"],
        }
        summary.append(row)
        if c["session"] in decisive:
            step_codes[c["session"]] = {
                "n_steps": c["n_steps"],
                "env_complete_idx": c["env_complete_idx"],
                "codes": c["codes"],
            }

    payload = {
        "candidate_sessions": summary,
        "step_codes": step_codes,
    }
    with open(_OUT, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"wrote {_OUT} ({len(summary)} candidate sessions, {len(step_codes)} with full step codes)")


if __name__ == "__main__":
    main()
