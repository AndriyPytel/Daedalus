"""
The trap store — seed traps, proposals, and the propose-and-approve gate
`prompt(phase)` reads from (FCC-184). FCC-191 makes each proposal, approval
and decline visible on an `EventStream`, when the store is built with one.
"""
import json

import pytest

from core import loop as loop_module
from core import traps as trap_store
from core.events import TRAP_APPROVED, TRAP_DECLINED, TRAP_PROPOSED, EventStream
from core.freecad_tools import TRAPS
from core.traps import DuplicateTrap, TrapStorage, TrapStore


@pytest.fixture
def store(tmp_path):
    return TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")))


def test_the_seed_layer_is_exactly_the_traps_list(store):
    assert store.seed_traps() == list(TRAPS)
    assert len(store.seed_traps()) == 15


def test_a_pending_proposal_is_absent_from_the_prompt_until_approved(store, monkeypatch):
    proposal = store.propose_trap(
        "Sketch.Placement is overridden on recompute if the sketch is attached",
        evidence="Step 3 failed with AttributeError: 'Sketcher::SketchObject' "
                 "object has no attribute 'Placement'",
        phase="execute",
    )
    monkeypatch.setattr(trap_store, "_default_store", store)

    assert proposal["description"] not in loop_module.prompt("execute")

    store.approve(proposal["id"])

    assert proposal["description"] in loop_module.prompt("execute")


def test_a_declined_proposal_is_absent_from_pending_and_the_prompt(store, monkeypatch):
    proposal = store.propose_trap(
        "A Chamfer applied before a Fillet on the same edge silently drops the Fillet",
        evidence="Step 5 log: recompute left the Fillet feature Touched",
        phase="review",
    )
    monkeypatch.setattr(trap_store, "_default_store", store)

    store.decline(proposal["id"])

    assert proposal["id"] not in [p["id"] for p in store.pending_proposals()]
    assert proposal["description"] not in loop_module.prompt("execute")


def test_a_proposal_duplicating_a_seed_trap_is_refused(store):
    before = len(store.pending_proposals())

    with pytest.raises(DuplicateTrap):
        store.propose_trap(
            "Sketch attachment goes through AttachmentSupport + MapMode, not Placement",
            evidence="repeated across three runs",
            phase="design",
        )

    assert len(store.pending_proposals()) == before


def test_a_proposal_keeps_its_evidence_and_the_phase_that_raised_it(store):
    proposal = store.propose_trap(
        "A Draft Array on a PartDesign feature does not update the Body's Tip",
        evidence="Step 7: feature tree shows the Array outside the Body",
        phase="assertion",
    )
    [pending] = store.pending_proposals()

    assert pending["evidence"] == "Step 7: feature tree shows the Array outside the Body"
    assert pending["phase"] == "assertion"
    assert pending["description"] == proposal["description"]


def test_the_store_survives_a_round_trip_through_disk(tmp_path):
    path = str(tmp_path / "trap_proposals.json")
    first = TrapStore(storage=TrapStorage(path=path))
    proposal = first.propose_trap(
        "A Pocket through a datum plane offset from the sketch cuts nothing",
        evidence="Step 2: Cut feature Valid but geometry unchanged",
        phase="plan",
    )
    first.approve(proposal["id"])

    with open(path, encoding="utf-8") as handle:
        on_disk = json.load(handle)
    assert on_disk[0]["status"] == "approved"

    reloaded = TrapStore(storage=TrapStorage(path=path))

    assert proposal["description"] in reloaded.active_traps()
    assert reloaded.pending_proposals() == []


def test_a_store_built_with_no_stream_emits_nothing(store):
    proposal = store.propose_trap(
        "A Sweep along a datum line loses its Twist setting on recompute",
        evidence="Step 4: Sweep feature Touched after Twist was set to 90 deg",
        phase="execute",
    )
    store.approve(proposal["id"])


def test_a_proposal_emits_trap_proposed_with_its_evidence_and_phase(tmp_path):
    events = EventStream()
    store = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")),
                      events=events)

    proposal = store.propose_trap(
        "A Sweep along a datum line loses its Twist setting on recompute",
        evidence="Step 4: Sweep feature Touched after Twist was set to 90 deg",
        phase="execute",
    )

    [event] = events.of(TRAP_PROPOSED)
    assert event.data["id"] == proposal["id"]
    assert event.data["description"] == proposal["description"]
    assert event.data["evidence"] == proposal["evidence"]
    assert event.data["phase"] == "execute"


def test_an_approval_emits_trap_approved_and_a_decline_emits_trap_declined(tmp_path):
    events = EventStream()
    store = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")),
                      events=events)

    approved = store.propose_trap(
        "A Sweep along a datum line loses its Twist setting on recompute",
        evidence="Step 4: Sweep feature Touched after Twist was set to 90 deg",
        phase="execute",
    )
    declined = store.propose_trap(
        "A Draft Mirror across a non-planar face silently produces no shape",
        evidence="Step 6: Mirror feature Valid but Shape.isNull() is True",
        phase="review",
    )

    store.approve(approved["id"])
    store.decline(declined["id"])

    assert len(events.of(TRAP_PROPOSED)) == 2
    [approval] = events.of(TRAP_APPROVED)
    assert approval.data["id"] == approved["id"]
    assert approval.data["description"] == approved["description"]
    [decline] = events.of(TRAP_DECLINED)
    assert decline.data["id"] == declined["id"]
    assert decline.data["description"] == declined["description"]


def test_a_duplicate_proposal_is_refused_before_anything_is_emitted(tmp_path):
    events = EventStream()
    store = TrapStore(storage=TrapStorage(path=str(tmp_path / "trap_proposals.json")),
                      events=events)

    with pytest.raises(DuplicateTrap):
        store.propose_trap(
            "Sketch attachment goes through AttachmentSupport + MapMode, not Placement",
            evidence="repeated across three runs",
            phase="design",
        )

    assert len(events) == 0


def test_a_proposal_duplicating_one_already_made_this_run_is_refused(store):
    """
    The dedupe `_duplicates_seed` does against `TRAPS` has to hold against
    this store's own proposals too, not only the seed layer — a run's Plan
    and Execute share one `TrapStore` (FCC-191), and a quirk Plan already
    raised is not a second trap because Execute met it too.
    """
    store.propose_trap("Draft cube() twice loses the second body silently",
                       evidence="Plan's own reading of the log", phase="plan")

    with pytest.raises(DuplicateTrap):
        store.propose_trap("Draft cube() twice loses the second body silently",
                           evidence="Execute hit it too", phase="execute")

    assert len(store.pending_proposals()) == 1
