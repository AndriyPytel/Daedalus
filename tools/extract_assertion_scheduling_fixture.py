"""
Build `tests/recorded/assertion_scheduling_cost.json` from `logs/sessions/*.jsonl`
and `logs/ab-rlm-agentic/**/*.jsonl` (gitignored, hence the tracked fixture --
FCC-231's established substitute) for FCC-247, which asks whether `check_
assertions` should run per Step rather than once at the end.

Run from the repo root with `logs/` present:
    python tools/extract_assertion_scheduling_fixture.py

Three things it measures, each answering one of FCC-247's own questions:

**Q1 -- what does an acceptance line cost to evaluate?** Session
`480b9995421e443d97b439a4f55bc3fe`'s own Assertion phase: the model's `cost`
event for writing the check script (`phase: "assertion"`) against the
`tool_call`/`tool_result` pair immediately after it, which is `check_
assertions` actually running that script against the Document. Writing costs
485.6 s; running the already-written script costs 1.2 s -- a Step evaluating
its own claims once written would be cheap. It is the *writing* that is not.

**Q3's cost side -- how many Steps would need a written check, and how many
of them would ever find anything?** For every session that logged a `plan`
event, the *last* Plan's Steps, split the way `core/loop.py`'s own `(satisfies:
...)` suffix already carries them (the same parse FCC-242's own fixture
extractor uses) -- a Step with a non-empty `satisfies` is a candidate for a
per-Step check, whether or not anything it claims ever turns out false. For
every session whose last `assertion` event carries `unmet` entries, each
unmet acceptance address is matched to the *last* Step whose own `satisfies`
names it (address only, value stripped, the way FCC-242's fixture reads
`plan.acceptance` back into a dict) -- when a match is found, every Step after
it was applied before the run ever found out that address was wrong, and is
counted toward `downstream_steps_after_earliest_failure`. Roughly 40% of
`unmet` addresses in this corpus resolve to no owning Step at all -- pre-
`language.md` sessions restate a datum's claim across an entry's `position`,
`radius_centres` and narrative sub-entries the acceptance list carries as
separate list items, and only some of those literally repeat the Step's own
`(satisfies: ...)` address string -- so this is a lower bound on how often a
Step's own defect surfaces only at the end, not an exact count.

**The cost estimate.** Every `execute`-phase `cost` event across the corpus
stands in for what ONE additional per-Step check-writing call would cost --
a Step's own build code and a check of that Step's own claims are the same
kind of task for the model that has to write either. Multiplying the
corpus-wide mean, median, and cheapest observed `execute` call each against
`qualifying_steps_with_satisfies` (every Step that would need a written
check, whether or not it ever fails) gives the added-cost side; multiplying
the same three figures against `downstream_steps_after_earliest_failure`
gives the saved-cost side -- the Steps that would not have been applied had
the earliest resolvable failure been caught when its own Step landed instead
of at the end. The added side is roughly an order of magnitude larger than
the saved side at every one of the three cost figures, cheapest included --
see `test_per_step_assertion_scheduling_disproven.py`.
"""
import glob
import json
import os
import re
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS_GLOBS = (
    os.path.join(ROOT, "logs", "sessions", "*.jsonl"),
    os.path.join(ROOT, "logs", "ab-rlm-agentic", "**", "*.jsonl"),
)
OUT_PATH = os.path.join(ROOT, "tests", "recorded", "assertion_scheduling_cost.json")
EVIDENCE_SESSION = "480b9995421e443d97b439a4f55bc3fe"

SATISFIES = re.compile(r"^(?P<do>.*?)\s*\(satisfies:\s*(?P<sat>.*)\)\s*$", re.DOTALL)
FLAG = re.compile(r"\s*\((?:read|derived|assumed)\)$")


def _paths():
    found = []
    for pattern in SESSIONS_GLOBS:
        found.extend(glob.glob(pattern, recursive=True))
    return sorted(found)


def _events(path):
    events = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def _split_step(step):
    matched = SATISFIES.match(step)
    if not matched:
        return step.strip(), []
    do = matched.group("do").strip()
    cited = [c.strip() for c in matched.group("sat").split(", ") if c.strip()]
    return do, cited


def _norm(address):
    return FLAG.sub("", address).strip()


def _address_of(entry):
    address, _, _value = entry.partition(": ")
    return " ".join(address.split())


def main():
    execute_seconds = []
    execute_usd = []
    assertion_seconds = []
    assertion_usd = []
    total_steps = 0
    qualifying_steps = 0
    n_plan_sessions = 0
    failing_sessions = []
    evidence_write_seconds = None
    evidence_check_seconds = None

    for path in _paths():
        events = _events(path)
        plans = [e for e in events if e.get("kind") == "plan"]
        assertions = [e for e in events if e.get("kind") == "assertion"]
        for event in events:
            if event.get("kind") != "cost":
                continue
            data = event["data"]
            if data.get("phase") == "execute" and data.get("seconds") is not None:
                execute_seconds.append(data["seconds"])
                if data.get("usd") is not None:
                    execute_usd.append(data["usd"])
            if data.get("phase") == "assertion" and data.get("seconds") is not None:
                assertion_seconds.append(data["seconds"])
                if data.get("usd") is not None:
                    assertion_usd.append(data["usd"])

        session = os.path.splitext(os.path.basename(path))[0]
        if session == EVIDENCE_SESSION:
            for index, event in enumerate(events):
                if event.get("kind") == "cost" and event["data"].get("phase") == "assertion":
                    evidence_write_seconds = event["data"]["seconds"]
                    for j in range(index, len(events)):
                        if events[j].get("kind") == "tool_call" \
                                and events[j]["data"].get("name") == "check_assertions":
                            call_at = events[j]["at"]
                            for k in range(j, len(events)):
                                if events[k].get("kind") == "tool_result":
                                    evidence_check_seconds = events[k]["at"] - call_at
                                    break
                            break
                    break

        if not plans:
            continue
        n_plan_sessions += 1
        steps = plans[-1]["data"].get("steps") or []
        total_steps += len(steps)
        step_owner = {}
        for idx, step in enumerate(steps):
            _do, cited = _split_step(step)
            if cited:
                qualifying_steps += 1
            for citation in cited:
                step_owner[_norm(citation)] = idx

        if not assertions:
            continue
        unmet = assertions[-1]["data"].get("unmet") or []
        if not unmet:
            continue
        resolved = [step_owner.get(_norm(_address_of(entry))) for entry in unmet]
        resolved = [index for index in resolved if index is not None]
        earliest = min(resolved) if resolved else None
        steps_after = (len(steps) - 1 - earliest) if earliest is not None else None
        failing_sessions.append({
            "session": session,
            "n_steps": len(steps),
            "unmet_count": len(unmet),
            "earliest_owning_step": earliest,
            "downstream_steps_after_earliest_failure": steps_after,
        })

    out = {
        "measured_from": {
            "sessions_with_plan_event": n_plan_sessions,
            "total_steps": total_steps,
            "qualifying_steps_with_satisfies": qualifying_steps,
            "sessions_with_assertion_failure": len(failing_sessions),
            "sessions_with_resolvable_owning_step":
                sum(1 for s in failing_sessions
                    if s["downstream_steps_after_earliest_failure"] is not None),
            "downstream_steps_after_earliest_failure":
                sum(s["downstream_steps_after_earliest_failure"] or 0
                    for s in failing_sessions),
        },
        "cost": {
            "execute_calls": len(execute_seconds),
            "execute_mean_seconds": statistics.mean(execute_seconds),
            "execute_median_seconds": statistics.median(execute_seconds),
            "execute_min_seconds": min(execute_seconds),
            "execute_mean_usd": statistics.mean(execute_usd) if execute_usd else None,
            "assertion_calls": len(assertion_seconds),
            "assertion_mean_seconds": statistics.mean(assertion_seconds)
                if assertion_seconds else None,
            "assertion_mean_usd": statistics.mean(assertion_usd) if assertion_usd else None,
            "evidence_session": EVIDENCE_SESSION,
            "evidence_write_seconds": evidence_write_seconds,
            "evidence_check_execution_seconds": evidence_check_seconds,
        },
        "failing_sessions": failing_sessions,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, sort_keys=True)
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()
