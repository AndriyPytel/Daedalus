"""
The Design -> Plan -> Execute -> Assertion -> Review visibility contract, as
one declared table -- not five hand-written spawn cases.

ADR-0003's fixed five-phase pipeline is a visibility contract, not a tool
format: Plan sees only Design; Execute sees Plan and Design; Assertion sees
only Design and a read-only Document, never an Execute artifact; Review is
isolated exactly as today, seeing intent and outcome and nothing that would
let it grade its own work. The orchestrator alone sees everything, because it
is what hands each phase its slice of it.

This is enforced structurally: a phase's kernel is spawned with a working
directory `spawn()` builds from `PHASE_CONTRACT` alone, containing exactly
the artefacts that phase's row declares and nothing a caller happens to pass
beside them. A row is `{artifact: mode}`, `mode` one of `RO`/`RW`; an
artefact this module does not know how to write, `_WRITERS` does not cover,
raises rather than silently dropping it, the way an unknown phase does.

Adding a new phase's visibility rule is editing `PHASE_CONTRACT`; `spawn()`
itself never changes for it.
"""
import json
import os
import shutil
import stat

DESIGN = "design"
PLAN = "plan"
EXECUTE = "execute"
ASSERTION = "assertion"
REVIEW = "review"
ORCHESTRATOR = "orchestrator"

RO = "ro"
RW = "rw"

ARTIFACTS = ("intent", "design", "plan", "execute", "document", "outcome")

PHASE_CONTRACT = {
    DESIGN: {"intent": RO},
    PLAN: {"design": RO},
    EXECUTE: {"design": RO, "plan": RO, "document": RW},
    ASSERTION: {"design": RO, "document": RO},
    REVIEW: {"intent": RO, "outcome": RO},
    ORCHESTRATOR: {artifact: RW for artifact in ARTIFACTS},
}


def _write_json(base, value):
    path = base + ".json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, default=str)
    return path


def _write_document(base, value):
    path = base + ".FCStd"
    shutil.copyfile(value, path)
    return path


_WRITERS = {
    "intent": _write_json,
    "design": _write_json,
    "plan": _write_json,
    "execute": _write_json,
    "outcome": _write_json,
    "document": _write_document,
}


def _made_read_only(path):
    mode = stat.S_IMODE(os.stat(path).st_mode)
    os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def spawn(phase, working_dir, **sources):
    """
    A phase's kernel working directory, built from `PHASE_CONTRACT` alone.

    `sources` is whatever the caller has on hand -- intent, design, plan,
    execute, document, outcome -- keyed by `ARTIFACTS`' own names. Only the
    ones `PHASE_CONTRACT[phase]` declares are ever written; a source passed
    for an artefact the phase's row omits (an Execute artifact handed to
    Assertion's spawn, say) is silently not written, which is what keeps the
    contract structural rather than a promise the caller keeps.

    A `RO` artefact is made read-only on disk once written; `RW` is left
    writable. Raises `KeyError` for a phase `PHASE_CONTRACT` does not name.

    Returns `working_dir`, created if it does not exist yet.
    """
    row = PHASE_CONTRACT[phase]
    os.makedirs(working_dir, exist_ok=True)
    for artifact, mode in row.items():
        if artifact not in sources:
            continue
        written = _WRITERS[artifact](os.path.join(working_dir, artifact), sources[artifact])
        if mode == RO:
            _made_read_only(written)
    return working_dir
