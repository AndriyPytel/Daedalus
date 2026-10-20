"""
`Budgets.preflight=False` reaches Execute: the Step goes to `apply_step` with no
`kernel_result`, the kernel never rehearses it, and the live Document's copy
resyncs the kernel.
"""
import json
import os
import tempfile

from core.events import EventStream
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient
from core.loop.budgets import Budgets
from core.loop.execute import execute
from core.tools import ToolRegistry


class RecordingKernel:
    def __init__(self):
        self.calls = []

    def check(self, code, **kwargs):
        self.calls.append(dict(kwargs, code=code))
        handle, path = tempfile.mkstemp(suffix=".FCStd")
        os.close(handle)
        return KernelResult(ok=True, checkpoint=path)


def _tools(applied):
    registry = ToolRegistry()

    @registry.tool("snapshot", "State.")
    def snapshot():
        return {}

    @registry.tool("document_copy", "A copy.")
    def document_copy():
        handle, path = tempfile.mkstemp(suffix=".FCStd")
        os.close(handle)
        return {"path": path}

    @registry.tool("apply_step", "Apply.", {
        "type": "object",
        "properties": {"code": {"type": "string"}, "label": {"type": "string"},
                       "op": {"type": "string"}, "kernel_result": {"type": "object"},
                       "design": {"type": "object"}},
        "required": ["code"]})
    def apply_step(code, **kwargs):
        applied.append(kwargs)
        return {"status": "applied", "created": ["Box"], "description": {}}

    return registry


def _run(budgets):
    applied, kernel = [], RecordingKernel()
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1, "label": "box"}),
        json.dumps({"declare": {}}),
    ])
    execute(["Add a box"], _tools(applied), llm, EventStream(), kernel=kernel, budgets=budgets)
    return applied, kernel


def test_the_default_still_preflights_every_step():
    applied, kernel = _run(Budgets())

    assert applied[0]["kernel_result"] is not None
    assert [c for c in kernel.calls if c.get("builds")]


def test_without_preflight_the_step_reaches_apply_unrehearsed():
    applied, kernel = _run(Budgets(preflight=False))

    assert applied[0].get("kernel_result") is None
    assert not [c for c in kernel.calls if c.get("builds")]
    assert kernel.calls[-1].get("document") is not None
