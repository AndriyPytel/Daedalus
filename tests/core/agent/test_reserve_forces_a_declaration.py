"""
The reserve forces the same last-call `run_phase` already gives a truly
exhausted supervisor, but ahead of `outcome` itself -- while turns still
remain against `turn_budget`, not only once none do.
"""
import json

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.agent.agent import _FORCED_BY_BUDGET
from core.agent.subagent import RESERVE, SessionTreeSupervisor
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient


class OneKernel:
    def __init__(self, result):
        self._result = result

    def check(self, code, **kwargs):
        return self._result


def _turns_short_of_the_reserve(turn_budget, short_by):
    """A supervisor pre-seeded to *short_by* turns before `reserved()` trips."""
    supervisor = SessionTreeSupervisor(turn_budget=turn_budget)
    for _ in range(turn_budget - RESERVE - short_by):
        supervisor.record_turn()
    return supervisor


def test_a_supervisor_crossing_the_reserve_forces_a_declaration_before_exhaustion(tmp_path):
    """AC1: the next turn once the reserve is crossed must declare or hand
    back, while `outcome` is still `None` -- the choice happens before the
    hard ceiling, not only once it has already fired."""
    supervisor = _turns_short_of_the_reserve(turn_budget=10, short_by=1)
    assert supervisor.reserved() is False

    llm = ScriptedLLMClient([
        json.dumps({"code": "print('probe')"}),
        json.dumps({"declare": {"unbuildable": "out of room"}}),
    ])

    declared, _, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look around",
        kernel=OneKernel(KernelResult(ok=True)), supervisor=supervisor,
    )

    assert supervisor.outcome is None, "the reserve fires before the hard ceiling, not after"
    assert declared.get(_FORCED_BY_BUDGET) is True


def test_a_phase_below_the_reserve_keeps_probing_unforced(tmp_path):
    """AC2: a phase whose supervisor has not crossed the reserve behaves
    exactly as it always has -- probing keeps running without a forced
    declaration."""
    supervisor = SessionTreeSupervisor(turn_budget=30)

    llm = ScriptedLLMClient([
        json.dumps({"code": "print('probe 1')"}),
        json.dumps({"code": "print('probe 2')"}),
        json.dumps({"declare": {"unbuildable": "the model chose to stop"}}),
    ])

    declared, _, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look around",
        kernel=OneKernel(KernelResult(ok=True)), supervisor=supervisor,
    )

    assert supervisor.reserved() is False
    assert _FORCED_BY_BUDGET not in declared


def test_a_replay_of_the_recorded_probe_stretch_declares_before_the_ceiling_fires(tmp_path):
    """AC3: attempt 1's 14 consecutive read-only Execute probes, replayed
    against a supervisor already close to its ceiling from the phases
    before it -- the reserve must force a declaration inside that stretch,
    not let all 14 run unchallenged the way they did in the recorded run."""
    supervisor = _turns_short_of_the_reserve(turn_budget=30, short_by=5)
    probes = [json.dumps({"code": "print('probe %d')" % i}) for i in range(14)]
    llm = ScriptedLLMClient(probes + [json.dumps({"declare": {"unbuildable": "out of room"}})])

    declared, _, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look around",
        kernel=OneKernel(KernelResult(ok=True)), supervisor=supervisor,
    )

    assert 1 < len(llm.calls) < 15, "the reserve must cut the 14-probe stretch short"
    assert declared.get(_FORCED_BY_BUDGET) is True
