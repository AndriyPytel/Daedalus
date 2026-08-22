"""
The trap store — seed traps, proposals, and the propose-and-approve gate
`prompt(phase)` reads from (FCC-184).
"""
import json

import pytest

from core import loop as loop_module
from core import traps as trap_store
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
