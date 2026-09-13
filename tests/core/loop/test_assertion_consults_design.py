"""
Assertion reads Design's own prose, not the Document apply.py rendered, so a
check can be written against what the description asked for rather than only
the flattened acceptance list and what got built.
"""
import json
import os

from core.agent import phase_contract as pc
from core.agent.phase_contract import spawn
from core.events import EventStream
from core.loop import assertion as assertion_module
from core.loop.assertion import assertion
from core.llm import ScriptedLLMClient
from core.tools import ToolRegistry


_DESCRIPTION = {
    "features": [{"about": "a hole", "value": "Ø8, blind, 12mm deep"}],
    "not_defined": [],
}


def _assertion_tools(tmp_path):
    registry = ToolRegistry()
    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(lambda code: {"ok": True, "held": [], "geometry": {}})
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})
    return registry


def test_assertion_is_handed_designs_own_prose_as_the_design_artifact(monkeypatch, tmp_path):
    registry = _assertion_tools(tmp_path)
    written = {}

    def fake_run_phase(kind, working_dir, sources, *args, **kwargs):
        working = spawn(kind, working_dir, **sources)
        with open(os.path.join(working, "design.json")) as handle:
            written["design"] = json.load(handle)
        return {"code": "assert True"}, None, []

    monkeypatch.setattr(assertion_module, "run_phase", fake_run_phase)

    assertion(["the hole is 8mm through"], {"built": "a plain hole"}, registry,
              ScriptedLLMClient([]), EventStream(), description=_DESCRIPTION)

    assert written["design"] == _DESCRIPTION
    assert written["design"] != {"built": "a plain hole"}


def test_no_description_leaves_the_design_artifact_unwritten(monkeypatch, tmp_path):
    registry = _assertion_tools(tmp_path)
    listed = {}

    def fake_run_phase(kind, working_dir, sources, *args, **kwargs):
        working = spawn(kind, working_dir, **sources)
        listed["files"] = os.listdir(working)
        return {"code": "assert True"}, None, []

    monkeypatch.setattr(assertion_module, "run_phase", fake_run_phase)

    assertion(["the hole is 8mm through"], {"built": "a plain hole"}, registry,
              ScriptedLLMClient([]), EventStream())

    assert "design.json" not in listed["files"]


def test_the_consult_is_on_the_stream_once_and_costs_no_call(monkeypatch, tmp_path):
    from core.events import CONSULT_DESIGN, CONSULT_DESIGN_ANSWERED

    registry = _assertion_tools(tmp_path)

    def fake_run_phase(*args, **kwargs):
        return {"code": "assert True"}, None, []

    monkeypatch.setattr(assertion_module, "run_phase", fake_run_phase)

    events = EventStream()
    llm = ScriptedLLMClient([])
    assertion(["the hole is 8mm through"], {}, registry, llm, events,
              description=_DESCRIPTION)

    assert len(events.of(CONSULT_DESIGN)) == 1
    answered = events.of(CONSULT_DESIGN_ANSWERED)
    assert len(answered) == 1
    assert answered[0].data["description"] == _DESCRIPTION
    assert llm.calls == []


def test_the_boundary_holds_evidence_and_attachments_never_reach_assertion(monkeypatch, tmp_path):
    """
    A description carrying `from`/`seen_in` evidence and a `not_defined` gap
    already answered would only be reachable by re-running Design's own
    evidence-gathering; `distilled()` is what strips that before this ever
    runs, and this test is what would catch a caller that stopped calling it.
    """
    from core.design.report import distilled

    raw = {
        "features": [{"about": "a hole", "value": "8mm", "from": "sheet3.pdf",
                      "seen_in": "view 2"}],
        "not_defined": ["a fillet radius"],
    }
    safe = distilled(raw)

    registry = _assertion_tools(tmp_path)
    written = {}

    def fake_run_phase(kind, working_dir, sources, *args, **kwargs):
        working = spawn(kind, working_dir, **sources)
        with open(os.path.join(working, "design.json")) as handle:
            written["design"] = json.load(handle)
        return {"code": "assert True"}, None, []

    monkeypatch.setattr(assertion_module, "run_phase", fake_run_phase)

    assertion(["a hole"], {}, registry, ScriptedLLMClient([]), EventStream(),
              description=safe)

    dumped = json.dumps(written["design"])
    assert "from" not in dumped
    assert "seen_in" not in dumped
    assert "sheet3.pdf" not in dumped
    assert written["design"] == safe
