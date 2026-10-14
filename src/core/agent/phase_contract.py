"""
The Design -> Plan -> Execute -> Assertion -> Review visibility contract, as
one declared table rather than five hand-written spawn cases: Plan sees only
Design; Execute sees Plan and Design; Assertion sees only Design and a
read-only Document, never an Execute artifact; Review sees nothing that
would let it grade its own work. This is enforced
structurally -- a phase's kernel is spawned with a working directory
`spawn()` builds from `PHASE_CONTRACT` alone, containing exactly what that
phase's row declares, and an artefact `_WRITERS` does not cover raises
rather than dropping silently. `design`, `intent` and `outcome` are never
disk artefacts: `design` is the part-file YAML `run_phase` carries into the
kernel's namespace, and `intent` and `outcome` are variables of the same
namespace on every call of the phases `NAMESPACE_VALUES` lists, taken from
`run_phase`'s `sources`, so `PHASE_CONTRACT` says nothing about them.
"""
import contextlib
import contextvars
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

ARTIFACTS = ("plan", "execute", "document")

PHASE_CONTRACT = {
    DESIGN: {},
    PLAN: {},
    EXECUTE: {"plan": RO, "document": RW},
    ASSERTION: {"document": RO},
    REVIEW_SUSPECT: {},
    REVIEW: {},
}

NAMESPACE_VALUES = {
    DESIGN: ("intent",),
    REVIEW_SUSPECT: ("intent", "outcome"),
    REVIEW: ("intent", "outcome"),
}


BASE_FACES = contextvars.ContextVar("base_faces", default=None)


@contextlib.contextmanager
def the_base_faces(faces):
    """The address -> area map of the base part, which the kernel hands Design and Plan code as `base_faces` for every call made inside; None when the run has no base part."""
    token = BASE_FACES.set(faces or None)
    try:
        yield
    finally:
        BASE_FACES.reset(token)


def the_base_faces_of(tools):
    """`the_base_faces` read off *tools*: the base part's addresses once the Document has one, nothing otherwise."""
    has_base = tools.call("base_inventory")["inventory"]
    return the_base_faces(tools.call("base_addresses")["base"] if has_base else None)


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
    "plan": _write_json,
    "execute": _write_json,
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
