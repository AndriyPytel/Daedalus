"""
The trap store: the seed layer plus proposals waiting on a human.

`core/freecad_tools.py :: TRAPS` is proven knowledge, shipped and read-only —
it seeds the store and is never written to from here. A proposal is a trap
candidate raised by a phase; it stays "pending" (invisible to `prompt()`,
FCC-170's whole point — the agent proposes, the human approves) until
`TrapStore.approve` moves it into the active set, where the next run's
`prompt()` call picks it up. `TrapStore.decline` removes it from the pending
list for good.

A proposal is judged a duplicate — of a seed trap, or of a proposal this
store already holds — by substring, not a model call: normalize both texts
(casefold, collapse whitespace) and compare — equal, or either one a prefix of
the other. The prefix leg exists because a proposal can restate only the
first sentence of a multi-sentence trap, which is still the same trap (see
FCC-184's own worked example: "Sketch attachment goes through
AttachmentSupport + MapMode, not Placement" against the seed trap that
continues ". Setting Placement on an attached sketch is silently overridden
on recompute."). This is intentionally strict rather than fuzzy: a paraphrase
that shares no prefix is treated as a new proposal, on the theory that a
missed duplicate costs one approval click and a false "already exists" costs
a lost trap.

Checked against this store's own proposals too because `core/loop.py :: run`
opens one `TrapStore` per run and hands it to every phase (FCC-191): a quirk
Plan already raised is not a second trap because Execute met it as well, and
without this check the same text would land twice, once from each phase.


Given an `EventStream`, every proposal, approval and decline emits the
matching `core/events.py` kind, so FCC-191's measurement of a Session log can
report a trap-proposal rate: the store itself stays optional, since FCC-184's
own tests build one bare, with no stream at all.
"""
import json
import os
import time
import uuid

from core.events import TRAP_APPROVED, TRAP_DECLINED, TRAP_PROPOSED
from core.freecad_tools import TRAPS

PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"


class DuplicateTrap(Exception):
    """Raised by `propose_trap` when the text already exists in the seed
    layer or among this store's own proposals."""


def _normalized(text):
    return " ".join(text.split()).casefold()


def _duplicates(description, others):
    normalized = _normalized(description)
    for other in others:
        normalized_other = _normalized(other)
        if normalized == normalized_other:
            return True
        if normalized_other.startswith(normalized) or normalized.startswith(normalized_other):
            return True
    return False


def _duplicates_seed(description):
    return _duplicates(description, TRAPS)


class TrapStorage:
    """
    Where proposals live on disk, resolved the way `Sessions`' `SettingsIndex`
    resolves the session index — lazily, and only when read or written, so
    importing this module never requires FreeCAD.

    Given no path, the real default sits under FreeCAD's user directory,
    beside the session logs. Outside FreeCAD (the test suite, a plain
    interpreter) that resolution fails; reads then come back empty and
    writes are dropped, since the addon only ever runs inside FreeCAD in
    production and a test that cares about persistence passes its own path.
    """

    def __init__(self, path=None):
        self._path = path

    def _resolved_path(self):
        if self._path:
            return self._path
        import FreeCAD
        directory = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "FreeCadCopilot", "logs")
        os.makedirs(directory, exist_ok=True)
        self._path = os.path.join(directory, "trap_proposals.json")
        return self._path

    def read(self):
        try:
            path = self._resolved_path()
        except ImportError:
            return []
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, proposals):
        try:
            path = self._resolved_path()
        except ImportError:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(proposals, handle, indent=2)


class TrapStore:
    """
    The seed layer (read-only, from TRAPS) plus the proposals this instance
    has loaded from `storage`. `active_traps()` is what a system prompt
    should carry; `pending_proposals()` is what a human still owes a
    decision.
    """

    def __init__(self, storage=None, events=None):
        self._storage = storage if storage is not None else TrapStorage()
        self._events = events
        self._proposals = self._storage.read()

    def _emit(self, kind, proposal, **extra):
        if self._events is None:
            return
        self._events.emit(kind, id=proposal["id"], description=proposal["description"],
                          phase=proposal["phase"], **extra)

    def seed_traps(self) -> list:
        return list(TRAPS)

    def active_traps(self) -> list:
        approved = [proposal["description"] for proposal in self._proposals
                    if proposal["status"] == APPROVED]
        return self.seed_traps() + approved

    def pending_proposals(self) -> list:
        return [dict(proposal) for proposal in self._proposals
                if proposal["status"] == PENDING]

    def propose_trap(self, description, evidence, phase) -> dict:
        if _duplicates_seed(description):
            raise DuplicateTrap("This trap already exists (seed layer).")
        if _duplicates(description, (p["description"] for p in self._proposals)):
            raise DuplicateTrap("This trap has already been proposed in this run.")
        proposal = {
            "id": uuid.uuid4().hex,
            "description": description,
            "evidence": evidence,
            "phase": phase,
            "status": PENDING,
            "proposed_at": time.time(),
        }
        self._proposals.append(proposal)
        self._storage.write(self._proposals)
        self._emit(TRAP_PROPOSED, proposal, evidence=evidence)
        return dict(proposal)

    def approve(self, proposal_id) -> dict:
        return self._set_status(proposal_id, APPROVED)

    def decline(self, proposal_id) -> dict:
        return self._set_status(proposal_id, DECLINED)

    def _set_status(self, proposal_id, status) -> dict:
        for proposal in self._proposals:
            if proposal["id"] == proposal_id:
                proposal["status"] = status
                self._storage.write(self._proposals)
                kind = TRAP_APPROVED if status == APPROVED else TRAP_DECLINED
                self._emit(kind, proposal)
                return dict(proposal)
        raise KeyError(proposal_id)


_default_store = None


def _store() -> TrapStore:
    global _default_store
    if _default_store is None:
        _default_store = TrapStore()
    return _default_store


def seed_traps() -> list:
    return _store().seed_traps()


def active_traps() -> list:
    """What `core/loop.py :: prompt(phase)` substitutes at the traps placeholder."""
    return _store().active_traps()


def pending_proposals() -> list:
    return _store().pending_proposals()


def propose_trap(description, evidence, phase) -> dict:
    return _store().propose_trap(description, evidence, phase)


def approve(proposal_id) -> dict:
    return _store().approve(proposal_id)


def decline(proposal_id) -> dict:
    return _store().decline(proposal_id)
