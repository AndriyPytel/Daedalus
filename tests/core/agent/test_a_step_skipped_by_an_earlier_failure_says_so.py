"""
FCC-291: a Step declared after an earlier Step failed to apply is not
silently dropped -- the model is told which Step to fix before it keeps
declaring Steps that will never land.
"""
import json

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient


class OneKernel:
    def __init__(self, result):
        self._result = result

    def check(self, code, **kwargs):
        return self._result


def test_a_step_skipped_by_an_earlier_failure_says_so(tmp_path):
    checkpoint = tmp_path / "checkpoint.FCStd"
    checkpoint.write_text("not really an FCStd")

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box1')",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2')",
                    "step": 2, "label": "second box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "first box"},
                                          {"step": 2, "label": "second box"}]}}),
    ])

    def on_declaration(declaration):
        return {"delivered": False, "traceback": "the kernel changed nothing"}

    result = KernelResult(ok=True, checkpoint=str(checkpoint))

    _, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes",
        kernel=OneKernel(result), on_declaration=on_declaration,
    )

    assert [d["step"] for d in declarations] == [1]
    assert declarations[0]["delivered"] is False
    feedback = json.loads(session.messages[-2]["content"])
    assert feedback["ok"] is False
    assert "Step 1" in feedback["traceback"]
    assert "failed" in feedback["traceback"]
