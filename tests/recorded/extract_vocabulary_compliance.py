"""
FCC-241: extracts, per session and per phase, how many raw LLM replies
carried the vocabulary marker `language.md`'s "Naming from a closed
vocabulary" and "The operation is one of 36" sections declare every reply
of that phase carries -- `kind_vocabulary` for Design, `op_vocabulary` for
Plan (the two phases `tests/test_language_prompt.py::test_a_reply_shows_
the_marker_language_md_declares_for_its_own_phase` already checks the
reply *skeleton* for; this measures the same two markers in the replies
actually sent).

**Sessions.** `afa0142baef847b0a9cd9139390af993` -- the session the ticket's
own by-hand table was measured over, run 2026-08-27 22:03, before FCC-236.
`480b9995421e443d97b439a4f55bc3fe` -- run 2026-08-28 00:36, after the
FCC-234/FCC-236 epic was written but 14 minutes before FCC-236's commit
(`90113ab`, 2026-08-28 00:50:07) reached the branch this session ran on --
so, functionally, also pre-FCC-236. No session in `logs/` at extraction
time postdates that commit: FCC-241 was written and measured in the same
sitting FCC-236 landed, so a genuinely post-FCC-236 rate does not exist yet
to extract. Both sessions extracted here predate the fix; this fixture is
the pre-fix baseline FCC-236's own effect gets checked against once a
session runs after it.

**Method.** A raw reply is reconstructed the way `core.loop._ask` builds it
for the caller: every `writing` event between a `call_started` and the
`cost` that ends it, in the order logged, joined into one string (this
matches `core.loop.OP_VERBS`... no -- matches the streaming accumulation
`core.loop._ask` itself performs; not reimplemented, only replayed off the
log). Each reconstructed reply is parsed with `core.loop._object_of` --
the exact function the loop uses to find the JSON object in a reply,
whether fenced, bare, or trailing prose (FCC-*, see its own docstring) --
and `phase` is read off `call_started`/`cost`'s own `data.phase` field:
`"design: page 1"` and `"design"` both count as Design, `"plan"` counts as
Plan, everything else is skipped. A reply that does not parse as JSON at
all is counted as not carrying the marker, not excluded.

Regenerate with:

    .venv/bin/python tests/recorded/extract_vocabulary_compliance.py
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from core.loop import _object_of

SESSIONS = {
    "afa0142baef847b0a9cd9139390af993": "before FCC-236",
    "480b9995421e443d97b439a4f55bc3fe": ("after the epic, before FCC-236 reached "
                                         "the branch it ran on"),
}

MARKER_BY_PHASE = {"design": "kind_vocabulary", "plan": "op_vocabulary"}

SESSIONS_DIR = os.path.join(REPO_ROOT, "logs", "sessions")


def _raw_replies(session_id):
    path = os.path.join(SESSIONS_DIR, session_id + ".jsonl")
    calls = []
    active = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            kind = record["kind"]
            data = record["data"]
            if kind == "call_started":
                active = {"phase": data["phase"], "buf": []}
            elif kind == "writing" and active is not None:
                active["buf"].append(data["text"])
            elif kind == "cost" and active is not None:
                calls.append({"phase": active["phase"], "text": "".join(active["buf"])})
                active = None
    return calls


def _phase_of(raw_phase):
    if raw_phase == "plan":
        return "plan"
    if raw_phase == "design" or raw_phase.startswith("design:"):
        return "design"
    return None


def measure(session_id):
    counts = {phase: {"replies": 0, "carried_marker": 0} for phase in MARKER_BY_PHASE}
    for call in _raw_replies(session_id):
        phase = _phase_of(call["phase"])
        if phase is None:
            continue
        counts[phase]["replies"] += 1
        parsed = _object_of(call["text"])
        marker = MARKER_BY_PHASE[phase]
        if isinstance(parsed, dict) and parsed.get(marker) == "closed":
            counts[phase]["carried_marker"] += 1
    return counts


def build_fixture():
    return {
        "markers_by_phase": MARKER_BY_PHASE,
        "sessions": {
            session_id: {"when": when, "phases": measure(session_id)}
            for session_id, when in SESSIONS.items()
        },
    }


if __name__ == "__main__":
    fixture = build_fixture()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "vocabulary_marker_compliance.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2)
        f.write("\n")
    print("wrote", out_path)
