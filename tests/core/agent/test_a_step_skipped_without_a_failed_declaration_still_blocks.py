"""
A Step whose own code never lands -- neither delivered nor declared a
failure, because the kernel refused it outright rather than accepting a
declaration `on_declaration` then rejected -- must not let a later Step
apply while it is still missing.
"""
import json

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient


class _StepAwareKernel:
    """Fails the first Step's code outright; every other Step's code lands."""

    def __init__(self, checkpoint, fails_step_code):
        self._checkpoint = checkpoint
        self._fails = fails_step_code

    def check(self, code, **kwargs):
        if code == self._fails:
            return KernelResult(ok=False, traceback="geometry refused")
        return KernelResult(ok=True, checkpoint=self._checkpoint)


def test_a_step_that_never_landed_or_declared_blocks_the_one_after_it(tmp_path):
    checkpoint = tmp_path / "checkpoint.FCStd"
    checkpoint.write_text("not really an FCStd")

    llm = ScriptedLLMClient([
        json.dumps({"code": "box1()", "step": 1, "label": "first box"}),
        json.dumps({"code": "box2()", "step": 2, "label": "second box"}),
        json.dumps({"declare": {"steps": [{"step": 2, "label": "second box"}]}}),
    ])

    _, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes",
        kernel=_StepAwareKernel(str(checkpoint), fails_step_code="box1()"),
    )

    assert declarations == [], (
        "Step 2 must not land while Step 1 has neither delivered code nor a "
        "declaration -- the kernel refusing Step 1 outright left no trace "
        "for the existing declared_failure check to catch")
    feedback = json.loads(session.messages[-2]["content"])
    assert feedback["ok"] is False
    assert "Step 1" in feedback["traceback"]
