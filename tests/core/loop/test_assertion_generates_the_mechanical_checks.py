"""Assertion runs the generated checks first and asks the model only for the entries code could not settle."""
import json

from core.agent import agent as agent_module
from core.events import ASSERTION, EventStream
from core.llm import ScriptedLLMClient
from core.loop.assertion import assertion
from core.tools import ToolRegistry
from tests.event_helpers import first_event

_SIZE = "features \u2192 hole-a \u2192 diameter: \u00d86"
_COUNT = "features \u2192 hole-a \u2192 count: 1"
_POSITION = "features \u2192 hole-a \u2192 position \u2192 the left end face: 20 mm"


def _tools(tmp_path, reply):
    calls = []
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})

    def check(code):
        calls.append(code)
        return reply(code)

    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(check)
    return registry, calls


def _holds(code):
    return {"ok": True, "held": [], "geometry": {}}


def _spy_on_the_model(monkeypatch):
    asked = []

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        asked.append(json.loads(task))
        return {"checks": [{"entry": 0, "expr": "True"}]}, None, []

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)
    return asked


def test_the_model_is_asked_only_for_the_entries_the_generated_checks_did_not_cover(monkeypatch, tmp_path):
    tools, calls = _tools(tmp_path, _holds)
    asked = _spy_on_the_model(monkeypatch)

    checked = assertion([_SIZE, _COUNT, _POSITION], {}, tools, ScriptedLLMClient([]), EventStream())

    assert len(asked) == 1
    assert [item["text"] for item in asked[0]["acceptance"]] == [_POSITION]
    assert "_faces.measured" in calls[0]
    assert _SIZE in checked.confirmed and _COUNT in checked.confirmed


def test_when_the_generated_checks_cover_everything_no_model_is_called(monkeypatch, tmp_path):
    tools, calls = _tools(tmp_path, _holds)
    asked = _spy_on_the_model(monkeypatch)
    events = EventStream()

    checked = assertion([_SIZE, _COUNT], {}, tools, ScriptedLLMClient([]), events)

    assert asked == []
    assert len(calls) == 1
    assert not checked.failed and checked.uncovered == []
    assert set(checked.confirmed) >= {_SIZE, _COUNT}
    assert first_event(events, ASSERTION).data["ok"] is True


def test_a_generated_check_that_fails_names_the_entry_and_stops_before_any_model_is_called(monkeypatch, tmp_path):
    def fails(code):
        return {"ok": False, "traceback": "AssertionError: these did not hold: " + _SIZE, "geometry": {}}

    tools, calls = _tools(tmp_path, fails)
    asked = _spy_on_the_model(monkeypatch)

    checked = assertion([_SIZE, _POSITION], {}, tools, ScriptedLLMClient([]), EventStream())

    assert asked == []
    assert checked.failed and checked.unmet == [_SIZE]


def test_the_checks_the_model_writes_are_confirmed_beside_the_ones_code_wrote(monkeypatch, tmp_path):
    tools, _ = _tools(tmp_path, lambda code: {"ok": True, "held": [0], "failed": [], "defects": [], "geometry": {}})
    _spy_on_the_model(monkeypatch)

    checked = assertion([_SIZE, _POSITION], {}, tools, ScriptedLLMClient([]), EventStream())

    assert checked.confirmed == [_SIZE, _POSITION]


def test_an_explicit_volume_claim_reaches_no_collateral_with_its_arguments(monkeypatch, tmp_path):
    claim = "acceptance_note: about 540 mm3 less than the base part; bounding box unchanged"
    tools, calls = _tools(tmp_path, _holds)
    asked = _spy_on_the_model(monkeypatch)

    checked = assertion([claim], {}, tools, ScriptedLLMClient([]), EventStream(), base={"volume": 1.0})

    assert asked == [] and claim in checked.confirmed
    assert "no_collateral(doc, volume_change=-540.0, moved=())" in calls[0]


def test_an_unchanged_entry_with_a_second_claim_still_reaches_the_model(monkeypatch, tmp_path):
    claim = "features \u2192 pad-top \u2192 direction: moves 5 mm up in the height, carrying the flange unchanged"
    tools, _ = _tools(tmp_path, _holds)
    asked = _spy_on_the_model(monkeypatch)

    assertion([claim], {}, tools, ScriptedLLMClient([]), EventStream(), base={"volume": 1.0})

    assert [item["text"] for item in asked[0]["acceptance"]] == [claim]


def test_the_prompt_offers_the_helpers_and_asks_for_one_expression_an_entry():
    from core.loop.prompt import ASSERTION_PHASE, prompt

    text = prompt(ASSERTION_PHASE)
    for name in ("face_at", "distance", "cylinder"):
        assert name + "(" in text
    assert "one expression per entry" in text
