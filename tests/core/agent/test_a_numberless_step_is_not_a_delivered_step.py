"""
A Step declared without a number does not poison the set of delivered Steps.

A declaration carrying `"step": null` used to land `None` in `delivered_steps`,
and the next code turn that named no Step at all matched it and died on
`message["step"]` -- a KeyError that ended the run at Step 6 of 20.
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


def test_a_numberless_declaration_does_not_match_a_later_numberless_turn(tmp_path):
    checkpoint = tmp_path / "checkpoint.FCStd"
    checkpoint.write_text("not really an FCStd")

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box1')", "step": None}),
        json.dumps({"code": "print(doc.Objects)"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "first box"}]}}),
    ])

    _, session, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad one box",
        kernel=OneKernel(KernelResult(ok=True, checkpoint=str(checkpoint))),
        on_declaration=lambda declaration: {"delivered": True},
    )

    assert [json.loads(m["content"]).get("status")
            for m in session.messages if m["role"] == "user"
            and m["content"].startswith("{")] != ["refused"]
