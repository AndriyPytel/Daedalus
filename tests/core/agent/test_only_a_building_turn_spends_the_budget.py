"""
A code turn that only looks -- no Step, no fix -- never touches the
supervisor; a Step turn and a fix of a delivered Step each spend one turn.
"""
import json

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.agent.subagent import SessionTreeSupervisor
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient


class OneKernel:
    def __init__(self, result):
        self._result = result
        self.exploring = []

    def check(self, code, **kwargs):
        self.exploring.append(kwargs.get("exploring"))
        return self._result


def _run(tmp_path, replies, ok=True, on_declaration=None):
    supervisor = SessionTreeSupervisor(turn_budget=100)
    checkpoint = tmp_path / "cp.FCStd"
    checkpoint.write_bytes(b"")
    kernel = OneKernel(KernelResult(ok=ok, checkpoint=str(checkpoint) if ok else None))
    llm = ScriptedLLMClient([json.dumps(reply) for reply in replies]
                            + [json.dumps({"declare": {"done": True}})])
    _, _, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "task", kernel=kernel,
        supervisor=supervisor, on_declaration=on_declaration)
    return supervisor, kernel, declarations


def test_exploration_turns_leave_the_supervisor_alone(tmp_path):
    supervisor, _, _ = _run(tmp_path, [{"code": "print(1)"}, {"code": "print(2)"}])
    assert supervisor.turns == 0


def test_a_failing_exploration_turn_leaves_the_supervisor_alone(tmp_path):
    supervisor, _, _ = _run(tmp_path, [{"code": "boom"}], ok=False)
    assert supervisor.turns == 0


def test_a_step_turn_spends_one_turn(tmp_path):
    supervisor, _, _ = _run(tmp_path, [{"code": "x", "step": 1, "label": "a"}])
    assert supervisor.turns == 1


def test_a_fix_of_a_delivered_step_reaches_on_declaration_and_is_not_a_declaration(tmp_path):
    seen = []

    def on_declaration(declaration):
        seen.append(declaration)
        return {"delivered": True}

    supervisor, kernel, declarations = _run(tmp_path, [
        {"code": "x", "step": 1, "label": "a"},
        {"fix": 1, "code": "y"},
    ], on_declaration=on_declaration)
    assert [(d["step"], d.get("fix"), d["label"]) for d in seen] == [
        (1, None, "a"), (None, 1, "Fix Step 1")]
    assert [d["step"] for d in declarations] == [1]
    assert supervisor.turns == 2
    assert kernel.exploring == [False, False]


def test_a_fix_of_an_undelivered_step_is_refused(tmp_path):
    seen = []
    supervisor, kernel, _ = _run(
        tmp_path, [{"fix": 1, "code": "y"}],
        on_declaration=lambda declaration: seen.append(declaration))
    assert seen == []
    assert kernel.exploring == []
