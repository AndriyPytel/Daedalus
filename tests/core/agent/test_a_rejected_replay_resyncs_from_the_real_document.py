"""
A rejected replay leaves the kernel holding a Step the real Document does
not -- the kernel's own checkpoint says the Step landed, the Document says
it did not. The kernel is resynced from the real Document itself, not from
its own last checkpoint, so the divergence does not carry into the next
Step's rehearsal.
"""
import json
import os
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
    os.close(handle)
    return path


def test_a_rejected_replay_resyncs_from_the_real_document_not_the_kernels_own_checkpoint(tmp_path):
    kernel = RecordingKernel(KernelResult(ok=True, checkpoint=_checkpoint_file()))
    real_document = _checkpoint_file()

    def rejected(declaration):
        return {"delivered": False, "drift": True, "traceback": "signature mismatch",
                "resync": real_document}

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1}),
        json.dumps({"declare": {"built": "nothing"}}),
    ])

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
             kernel=kernel, on_declaration=rejected)

    rewinds = [call for call in kernel.calls if call["code"] == "pass"]
    assert rewinds, "a rejected replay did not resync the kernel at all"
    assert rewinds[-1]["document"] == real_document, (
        "the resync reopened the kernel's own checkpoint instead of the real "
        "Document `on_declaration` handed back: %r" % rewinds[-1])
    assert not os.path.exists(real_document), (
        "the real Document snapshot used to resync was never cleaned up")
