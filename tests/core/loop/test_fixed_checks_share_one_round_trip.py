"""The envelope and self-intersection checks cost one round trip."""
import json

from core.events import EventStream, TOOL_CALL
from core.llm import ScriptedLLMClient
from core.loop.assertion import assertion
from core.tools import ToolRegistry


def test_the_two_fixed_checks_are_one_check_assertions_call(tmp_path):
    document = tmp_path / "live.FCStd"
    document.write_bytes(b"not a real Document, only bytes to copy")

    calls = []
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(document)})
    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(lambda code: calls.append(code) or {"ok": True, "held": [], "geometry": {}})

    acceptance = ["envelope \u2192 length between the ends: 108 mm (read)",
                 "envelope \u2192 depth front to rear: 30 mm (read)",
                 "envelope \u2192 height of the right lug: 30 mm (read)"]
    llm = ScriptedLLMClient([json.dumps({"declare": {"code": "assert True"}})])
    events = EventStream()

    assertion(acceptance, {}, registry, llm, events)

    fixed_check_calls = [code for code in calls
                         if "optimalBoundingBox" in code or "BOPAlgo_SelfIntersect" in code]
    assert len(fixed_check_calls) == 1, \
        "the envelope and self-intersection checks cost %d round trips" % len(fixed_check_calls)
    assert "optimalBoundingBox" in fixed_check_calls[0]
    assert "BOPAlgo_SelfIntersect" in fixed_check_calls[0]
    assert len([e for e in events if e.kind == TOOL_CALL
               and e.data["name"] == "check_assertions"]) == 2, \
        "one fixed-checks call and one for the model's own declared check"
