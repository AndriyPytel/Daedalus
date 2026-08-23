"""
FCC-191 — wiring one `TrapStore` into `core/loop.py :: run`, so the
trap-proposal metric `core/measure.py` reports is no longer a structural
zero. `core/traps.py :: TrapStore` and `core/agent.py :: ask`'s `trap`
action already existed (FCC-184, FCC-185); nothing built one for a run.

No FreeCAD anywhere in this file: `tests.test_loop.drive` and its
`ScriptedLLMClient`/`FakeTools` stand in for the provider and the Document.
"""
import json

from core.events import TRAP_PROPOSED
from tests.test_loop import (
    A_CUBE, assertion_reply, code_reply, design_reply, drive, everything, plan_reply,
    verdict_reply,
)

_TRAP = json.dumps({"trap": {
    "description": "Draft cube() twice loses the second body silently",
    "evidence": "Step 2 log: two Part::Box named Box, only one visible",
}})


def test_a_trap_proposed_during_a_run_names_the_phase_that_proposed_it():
    result, events, _, _ = drive([
        _TRAP,
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])

    assert result.ok
    proposed = events.of(TRAP_PROPOSED)
    assert len(proposed) == 1
    assert proposed[0].data["phase"] == "plan"
    assert proposed[0].data["description"] == (
        "Draft cube() twice loses the second body silently")


def test_one_store_is_shared_across_phases_a_later_proposal_is_a_duplicate():
    """
    Plan proposes the trap first and it lands; Execute proposes the same
    text and is refused, because both phases share the one store this run
    opened rather than each getting a store of its own.
    """
    result, events, _, _ = drive([
        _TRAP,
        plan_reply("Add a box"),
        _TRAP,
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], agentic=True)

    assert result.ok
    proposed = events.of(TRAP_PROPOSED)
    assert len(proposed) == 1
    assert proposed[0].data["phase"] == "plan"


def test_a_run_that_proposes_nothing_has_the_same_event_kinds_as_before():
    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ])

    assert [event.kind for event in events] == [
        "intent", "call_started", "cost", "design", "part", "call_started", "cost",
        "plan", "gate", "gate", "call_started", "cost", "step_started", "tool_call",
        "tool_result", "applied", "call_started", "cost", "tool_call", "tool_result",
        "assertion", "call_started", "cost", "review_verdict",
    ]
