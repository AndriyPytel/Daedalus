"""
`execute.py`'s own `on_declaration` closure -- not `run_phase`'s use of
whatever it hands back -- must be the one calling `document_copy` and
carrying the result as `resync`, on every one of its three rejection
returns: the free first refusal, a budgeted retry, and the Step abandoned
once the budget is spent.
"""
import json
import os
import tempfile

from core.events import EventStream, PREFLIGHT_FAILED
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient
from core.loop.budgets import Budgets
from core.loop.execute import execute
from core.tools import ToolRegistry
from tests.loop_helpers import retried_code_reply


def _tmp_file():
    handle, path = tempfile.mkstemp(suffix=".FCStd")
    os.close(handle)
    return path


class _RecordingKernel:
    """Preflight always lands -- this defect is only about what Apply does
    with a Step the kernel already accepted."""

    def __init__(self):
        self.checks = []

    def check(self, code, **kwargs):
        self.checks.append(dict(kwargs, code=code))
        return KernelResult(ok=True, checkpoint=_tmp_file())


class _RejectingTools:
    """`apply_step` always drifts; `document_copy` hands back a fresh,
    distinguishable path every call."""

    def __init__(self):
        self.document_copies = []
        self.registry = self._build()

    def _build(self):
        registry = ToolRegistry()

        @registry.tool("snapshot", "State right now.")
        def snapshot():
            return {}

        @registry.tool("document_copy", "The Document saved for a phase kernel.")
        def document_copy():
            path = _tmp_file()
            self.document_copies.append(path)
            return {"path": path}

        @registry.tool("apply_step", "Apply one Step.", {
            "type": "object",
            "properties": {"code": {"type": "string"}, "label": {"type": "string"},
                          "op": {"type": "string"}, "kernel_result": {"type": "object"},
                          "design": {"type": "object"}},
            "required": ["code"],
        })
        def apply_step(code, **_kwargs):
            return {"status": "failed", "drift": True, "traceback": "signature mismatch"}

        return registry


def test_every_rejected_replay_carries_the_document_copy_as_its_resync():
    tools = _RejectingTools()
    kernel = _RecordingKernel()
    llm = ScriptedLLMClient(retried_code_reply(
        "one()", "two()", "three()", "four()", step=1))

    execute(["Pad a rib"], tools.registry, llm, EventStream(),
           kernel=kernel, budgets=Budgets(attempts_per_step=3))

    rewinds = [call for call in kernel.checks if call["code"] == "pass"]
    assert len(rewinds) == 4, (
        "expected one resync per rejected replay (free refusal, two budgeted "
        "retries, the exhausted handback) -- got %d" % len(rewinds))
    assert [call["document"] for call in rewinds] == tools.document_copies[1:], (
        "a resync did not reopen the document_copy taken right after its own "
        "rejection: %r" % [call["document"] for call in rewinds])
