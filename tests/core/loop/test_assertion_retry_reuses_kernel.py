"""FCC-389: an Assertion retry reuses the kernel and the document it already has."""
import json

from core.events import EventStream
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient
from core.loop import assertion as assertion_module
from core.loop.assertion import assertion
from core.tools import ToolRegistry


class _CountingKernel:
    """Stands in for `core.kernel.Kernel`; counts how many times one is booted."""

    instances = 0

    def __init__(self):
        _CountingKernel.instances += 1

    def check(self, code, **kwargs):
        return KernelResult(ok=True, checkpoint="stub")

    def kill(self):
        pass


def _registry(copies, checks, document_path):
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: copies.append(1) or {"path": document_path})
    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(lambda code: checks.pop(0))
    return registry


def _drive_a_retrying_assertion(monkeypatch, tmp_path):
    """
    One attempt fails on a script error (not a failed acceptance check), the
    second attempt's checks hold. Every attempt writes one exploratory `code`
    turn before it declares, so the kernel that turn runs against is booted,
    and the retry drives it a second time.
    """
    monkeypatch.setattr(assertion_module, "Kernel", _CountingKernel)
    _CountingKernel.instances = 0

    document = tmp_path / "live.FCStd"
    document.write_bytes(b"not a real Document, only bytes to copy")
    copies = []
    checks = [
        {"ok": True, "held": [], "geometry": {}},
        {"ok": False, "traceback": "NameError: name 'Edge99' is not defined"},
        {"ok": True, "held": ["length: 10 mm"], "geometry": {}},
    ]
    tools = _registry(copies, checks, str(document))
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('looking, attempt 1')"}),
        json.dumps({"declare": {"code": "assert doc.getObject('Edge99')"}}),
        json.dumps({"code": "print('looking, attempt 2')"}),
        json.dumps({"declare": {"code": "assert True"}}),
    ])
    events = EventStream()

    checked = assertion(["length: 10 mm"], {}, tools, llm, events)
    return checked, copies, llm


def test_a_second_attempt_boots_no_second_kernel(monkeypatch, tmp_path):
    checked, copies, llm = _drive_a_retrying_assertion(monkeypatch, tmp_path)

    assert checked.failed is False and checked.confirmed == ["length: 10 mm"]
    assert _CountingKernel.instances == 1, "a retry booted a second kernel"
    assert copies == [1], "a retry re-copied the document"


def test_the_failing_attempts_traceback_reaches_the_next_attempts_first_turn(monkeypatch, tmp_path):
    _, _, llm = _drive_a_retrying_assertion(monkeypatch, tmp_path)

    second_attempt_task = json.loads(llm.calls[2][-1]["content"])
    told = second_attempt_task["your_failed_attempts_at_the_checks"]

    assert len(told) == 1
    assert "Edge99" in told[0]["traceback"]
