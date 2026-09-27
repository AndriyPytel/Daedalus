"""
The Design -> Plan -> Execute -> Assertion -> Review visibility contract, as
one declared table rather than five hand-written spawn cases: Plan sees only
Design; Execute sees Plan and Design; Assertion sees only Design and a
read-only Document, never an Execute artifact; Review sees intent and
outcome and nothing that would let it grade its own work. This is enforced
structurally -- a phase's kernel is spawned with a working directory
`spawn()` builds from `PHASE_CONTRACT` alone, containing exactly what that
phase's row declares, and an artefact `_WRITERS` does not cover raises
rather than dropping silently. Design is never a disk artefact -- `design`
is the part-file YAML `run_phase` carries straight into the kernel's own
namespace, read-only there because `step_namespace` hands each Step a deep
copy, so `PHASE_CONTRACT` says nothing about it.
"""
import json
import os
import shutil
import stat

from core.loop.prompt import (ASSERTION_PHASE as ASSERTION, DESIGN_PHASE as DESIGN,
                              EXECUTE_PHASE as EXECUTE, PLAN_PHASE as PLAN,
                              REVIEW_PHASE as REVIEW,
                              REVIEW_SUSPECT_PHASE as REVIEW_SUSPECT)

RO = "ro"
RW = "rw"

ARTIFACTS = ("intent", "plan", "execute", "document", "outcome")

PHASE_CONTRACT = {
    DESIGN: {"intent": RO},
    PLAN: {},
    EXECUTE: {"plan": RO, "document": RW},
    ASSERTION: {"document": RO},
    REVIEW_SUSPECT: {"intent": RO, "outcome": RO},
    REVIEW: {"intent": RO, "outcome": RO},
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
    A phase's kernel working directory, built from `PHASE_CONTRACT` alone;
    `sources` is whatever the caller has on hand, keyed by `ARTIFACTS`' own
    names, and only the ones `PHASE_CONTRACT[phase]` declares are ever
    written. A `RO` artefact is made read-only on disk once written, `RW` is
    left writable, an unnamed phase raises `KeyError`, and `design` is
    never written here -- it reaches a phase only through `run_phase`'s own
    `design` argument. Returns `working_dir`, created if it does not exist yet.
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
