"""
`run` stays disarmed for Design and Assertion (BRIEFING Question 5).

`core/agent.py :: ask` executes `run`'s code through `Namespace.run` -- a
bare `exec` with no import allowlist, no Preflight, none of `NOT_A_LOOK`'s
own narrowing of `look`. Execute already applies model-written Python to the
Document on every Step through that same gate-less path
(`core/apply.py`), so `run` there mirrors a capability the architecture
already accepts. Design and Assertion never had that acceptance -- Design
never reaches a FreeCAD API (ADR-0019), and Assertion is read-only by
construction (`check_assertions` is its one path). The refusal
(`core/agent.py :: _run_code`) is checked against
`core/loop.py :: NOT_ARMED_FOR_RUN`, not against a per-call-site convention,
so a phase cannot be armed with `run` again by a future change to
`core/loop.py`'s call sites without this test noticing.
"""
from core.loop import ASSERTION_PHASE, DESIGN_PHASE, EXECUTE_PHASE, NOT_ARMED_FOR_RUN


def test_design_and_assertion_are_named_and_nothing_else_is():
    assert set(NOT_ARMED_FOR_RUN) == {DESIGN_PHASE, ASSERTION_PHASE}


def test_execute_is_not_named():
    assert EXECUTE_PHASE not in NOT_ARMED_FOR_RUN
