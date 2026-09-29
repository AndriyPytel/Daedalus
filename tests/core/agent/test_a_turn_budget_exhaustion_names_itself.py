"""
The run's own turn ceiling names itself when it fires, once per run no
matter how many phase continuations ask -- `_work_through` restarting
Execute after a plan revision must not buy the run a second 30-turn
allowance.
"""
import json

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.agent.subagent import SessionTreeSupervisor
from core.events import EventStream, TURN_BUDGET_EXHAUSTED
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient


class OneKernel:
    def __init__(self, result):
        self._result = result

    def check(self, code, **kwargs):
        return self._result


def test_a_turn_budget_exhaustion_names_itself_with_the_count_it_reached(tmp_path):
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2')", "step": 1}),
        json.dumps({"declare": {"unbuildable": "out of turns"}}),
    ])
    stream = EventStream()
    supervisor = SessionTreeSupervisor(turn_budget=1)

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
              kernel=OneKernel(KernelResult(ok=True)), stream=stream,
              supervisor=supervisor)

    fired = stream.of(TURN_BUDGET_EXHAUSTED)
    assert len(fired) == 1
    assert fired[0].data == {"phase": pc.EXECUTE, "turns": 2, "budget": 1}


def test_a_second_continuation_on_the_same_exhausted_supervisor_fires_no_second_event(tmp_path):
    """
    `execute()` restarting Execute after a plan revision, sharing the run's
    one supervisor, asks it again -- the ceiling already told its story once
    and stays quiet the second time.
    """
    supervisor = SessionTreeSupervisor(turn_budget=1)
    stream = EventStream()

    first_llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2')", "step": 1}),
        json.dumps({"declare": {"unbuildable": "out of turns"}}),
    ])
    run_phase(pc.EXECUTE, str(tmp_path / "execute1"), {}, first_llm, "pad a box",
              kernel=OneKernel(KernelResult(ok=True)), stream=stream,
              supervisor=supervisor)
    assert len(stream.of(TURN_BUDGET_EXHAUSTED)) == 1

    second_llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box3')"}),
        json.dumps({"declare": {"unbuildable": "still out of turns"}}),
    ])
    run_phase(pc.EXECUTE, str(tmp_path / "execute2"), {}, second_llm, "pad a box again",
              kernel=OneKernel(KernelResult(ok=True)), stream=stream,
              supervisor=supervisor)

    assert len(stream.of(TURN_BUDGET_EXHAUSTED)) == 1, (
        "the recorded smoke's two exhaustions must replay as one once the "
        "continuations share a supervisor")
