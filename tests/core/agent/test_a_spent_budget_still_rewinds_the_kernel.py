"""
A Step whose retry budget runs out is abandoned by `on_declaration`, not
retried -- but it already ran once in the kernel's own Preflight session,
which committed its checkpoint there before Apply ever saw it. Giving up on
the Step without rewinding the kernel leaves that Preflight run's objects
sitting in the kernel's live Document, so a later declaration of the same
Step -- the model's next attempt, or a later Step reusing the same names --
collides with debris nothing rolled back.
"""
import json
import tempfile

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient


class RecordingKernel:
    """A double that hands back one canned result and remembers every call."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def check(self, code, **kwargs):
        self.calls.append(dict(kwargs, code=code))
        return self._result


def _checkpoint_file():
    handle, path = tempfile.mkstemp(suffix=".FCStd")
    import os

    os.close(handle)
    return path


def test_a_step_abandoned_on_a_spent_budget_still_rewinds_the_kernel(tmp_path):
    """
    `on_declaration` giving up on a Step -- no `retry`, no `drift`, just
    `delivered: False` -- is exactly as much a non-delivery as either of
    those, and has to rewind the kernel the same way: the Step's own
    Preflight run committed a checkpoint the real Document never landed.
    """
    kernel = RecordingKernel(KernelResult(ok=True, checkpoint=_checkpoint_file()))

    def gave_up(declaration):
        return {"delivered": False, "traceback": "would not land"}

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1}),
        json.dumps({"declare": {"built": "nothing"}}),
    ])

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
             kernel=kernel, on_declaration=gave_up)

    rewinds = [call for call in kernel.calls if call["code"] == "pass"]
    assert rewinds, (
        "a Step whose retry budget ran out left its Preflight debris in the "
        "kernel's own session -- the next declaration of it starts from a "
        "Document that still has what this attempt created")
