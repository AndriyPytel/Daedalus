"""
`run_phase(preflight=False)`: a Step's code is handed to `on_declaration`
without the kernel having run it, and the kernel is resynced from the live
Document afterwards. Exploration turns, which declare nothing, still use the kernel.
"""
import json
import os
import tempfile

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient


class RecordingKernel:
    def __init__(self):
        self.calls = []

    def check(self, code, **kwargs):
        self.calls.append(dict(kwargs, code=code))
        handle, path = tempfile.mkstemp(suffix=".FCStd")
        os.close(handle)
        return KernelResult(ok=True, checkpoint=path, created=["Box"])


def _resync_file():
    handle, path = tempfile.mkstemp(suffix=".FCStd")
    os.close(handle)
    return path


def test_a_step_is_declared_to_apply_without_the_kernel_running_it(tmp_path):
    kernel = RecordingKernel()
    seen = []

    def lands(declaration):
        seen.append(declaration)
        return {"delivered": True, "resync": _resync_file()}

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1, "label": "box"}),
        json.dumps({"declare": {"built": "a box"}}),
    ])

    _, _, declarations = run_phase(pc.EXECUTE, str(tmp_path / "x"), {}, llm, "a box",
                                   kernel=kernel, on_declaration=lands, preflight=False)

    assert [d["step"] for d in seen] == [1]
    assert not seen[0]["signature"]
    assert not [c for c in kernel.calls if c.get("builds")]
    assert [d["step"] for d in declarations] == [1]
    assert kernel.calls[-1]["document"] is not None, "the kernel was not resynced to the live Document"


def test_the_model_is_told_the_step_landed_and_what_it_created(tmp_path):
    def lands(declaration):
        declaration["created"] = ["Box"]
        return {"delivered": True, "resync": _resync_file()}

    llm = ScriptedLLMClient([
        json.dumps({"code": "x()", "step": 1}),
        json.dumps({"declare": {}}),
    ])
    run_phase(pc.EXECUTE, str(tmp_path / "x"), {"plan": ["one", "two"]}, llm, "t",
              kernel=RecordingKernel(), on_declaration=lands, preflight=False)

    told = json.loads(llm.calls[1][-1]["content"][0]["text"]
                      if isinstance(llm.calls[1][-1]["content"], list)
                      else llm.calls[1][-1]["content"])
    assert told["ok"] is True and told["created"] == ["Box"] and told["step"] == 1
    assert told["next_step"] == 2


def test_an_apply_failure_is_handled_like_any_rejected_replay(tmp_path):
    outcomes = iter([{"delivered": False, "retry": True, "traceback": "NameError: nope",
                      "resync": _resync_file()},
                     {"delivered": True, "resync": _resync_file()}])
    llm = ScriptedLLMClient([
        json.dumps({"code": "bad()", "step": 1}),
        json.dumps({"code": "good()", "step": 1}),
        json.dumps({"declare": {}}),
    ])

    _, _, declarations = run_phase(pc.EXECUTE, str(tmp_path / "x"), {}, llm, "t",
                                   kernel=RecordingKernel(),
                                   on_declaration=lambda d: next(outcomes), preflight=False)

    assert "NameError: nope" in json.dumps(llm.calls[1][-1]["content"])
    assert [d["code"] for d in declarations] == ["good()"]


def test_a_turn_that_declares_no_step_still_goes_through_the_kernel(tmp_path):
    kernel = RecordingKernel()
    llm = ScriptedLLMClient([
        json.dumps({"code": "print(1)"}),
        json.dumps({"declare": {}}),
    ])
    run_phase(pc.EXECUTE, str(tmp_path / "x"), {}, llm, "t", kernel=kernel, preflight=False)

    assert [c["code"] for c in kernel.calls] == ["print(1)"]
