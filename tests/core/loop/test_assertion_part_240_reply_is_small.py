"""AC6: on the part 240 run shape the per-entry form needs no whole-script rewrite; its reply costs a fraction of the 3k tokens a call may spend."""
import ast
import json
import os

from core.events import ASSERTION, EventStream
from core.llm import ScriptedLLMClient
from core.loop.assertion import assertion
from core.tools import ToolRegistry

_RECORDED = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "part_240_assertion.json"),
                           encoding="utf-8"))
_CHARACTERS_PER_TOKEN = 3
_TOKENS_PER_CALL = 3000


def _tools(tmp_path, scripts):
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})

    def check(code):
        if "check_each" not in code:
            return {"ok": True, "geometry": {}}
        scripts.append(code)
        asked = ast.literal_eval(code.split("check_each(", 1)[1].rsplit(")", 1)[0])
        return {"ok": True, "held": sorted(asked), "failed": [], "defects": [], "geometry": {}}

    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(check)
    return registry


def test_the_recorded_entries_are_answered_in_one_call_well_under_the_token_ceiling(tmp_path):
    (tmp_path / "live.FCStd").write_bytes(b"only bytes to copy")
    reply = json.dumps({"declare": _RECORDED["reply"]})
    llm = ScriptedLLMClient([reply])
    scripts = []

    checked = assertion(_RECORDED["entries"], {}, _tools(tmp_path, scripts), llm, EventStream())

    assert len(llm.calls) == 1
    assert len(reply) / _CHARACTERS_PER_TOKEN < _TOKENS_PER_CALL
    assert len(_RECORDED["reply"]["checks"]) == len(_RECORDED["entries"])
    assert len(scripts) == 1 and checked.defects == []
    assert sorted([e for e in checked.confirmed if e in _RECORDED["entries"]] + checked.uncovered) == sorted(_RECORDED["entries"])


def test_the_recorded_script_calls_each_wrote_more_than_the_ceiling():
    heavy = [call for call in _RECORDED["recorded_script_calls"] if call["completion_tokens"] > _TOKENS_PER_CALL]
    assert len(heavy) == 9
