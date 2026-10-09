"""One expression that outlives the kernel timeout is that entry's defect alone; the entries before it keep their result and the ones after it are evaluated in a new call."""
import json

from core.events import EventStream
from core.kernel.kernel import TIMED_OUT
from core.kernel.kernel_worker import check_each
from core.llm import ScriptedLLMClient
from core.loop import assertion as assertion_module
from core.loop.assertion import assertion
from core.tools import ToolRegistry

_ENTRIES = ["entry %d" % n for n in range(5)]


class _Killed(BaseException):
    pass


def _tools(tmp_path):
    scripts = []
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})

    def hang():
        raise _Killed

    def check(code):
        if "check_each" not in code:
            return {"ok": True, "geometry": {}}
        scripts.append(code)
        namespace = {"d": 20.0, "hang": hang}
        namespace["check_each"] = check_each(namespace)
        try:
            exec(code, namespace)
        except _Killed:
            return {"ok": False, "traceback": TIMED_OUT + " after 30.0s -- the Step did not finish.",
                    "geometry": {}}
        return {"ok": True, "held": namespace["held"], "failed": namespace["failed"],
                "defects": namespace["defects"], "geometry": {}}

    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(check)
    return registry, scripts


def _model(monkeypatch, *replies):
    asked = []
    remaining = list(replies)

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        asked.append(json.loads(task))
        return remaining.pop(0), None, []

    monkeypatch.setattr(assertion_module, "run_phase", fake_run_phase)
    return asked


def _answer(*expressions):
    return {"checks": [{"entry": n, "expr": text} for n, text in enumerate(expressions)]}


def _run(tmp_path):
    tools, scripts = _tools(tmp_path)
    return assertion(_ENTRIES, {}, tools, ScriptedLLMClient([]), EventStream()), scripts


def test_an_expression_that_never_finishes_costs_its_own_entry_only(monkeypatch, tmp_path):
    asked = _model(monkeypatch, _answer("d == 20.0", "hang()", "d == 99", "d > 0", "d == 20.0"),
                   {"checks": [{"entry": 1, "expr": "d == 20.0"}]})

    checked, scripts = _run(tmp_path)

    assert len(scripts) == 3
    assert "check_each({1: 'd == 20.0'})" in scripts[2]
    assert "hang()" not in scripts[1] and "d == 20.0" in scripts[1] and "d == 99" in scripts[1]
    assert "d > 0" in scripts[1] and scripts[1].count("d == 20.0") == 1
    assert [item["entry"] for item in asked[1]["acceptance"]] == [1]
    assert "longer than the kernel allows" in asked[1]["your_checks_that_raised"][0]["error"]
    assert checked.unmet == [_ENTRIES[2]]
    assert sorted(entry for entry in checked.confirmed if entry in _ENTRIES) == [
        _ENTRIES[0], _ENTRIES[1], _ENTRIES[3], _ENTRIES[4]]
    assert checked.defects == []


def test_the_entry_in_flight_stays_a_defect_when_it_never_finishes_on_the_retry(monkeypatch, tmp_path):
    reply = _answer("d == 20.0", "hang()", "d == 99")
    _model(monkeypatch, reply, {"checks": [{"entry": 1, "expr": "hang()"}]},
           {"checks": [{"entry": 1, "expr": "hang()"}]})

    tools, _ = _tools(tmp_path)
    checked = assertion(_ENTRIES[:3], {}, tools, ScriptedLLMClient([]), EventStream())

    assert checked.defects == [_ENTRIES[1]]
    assert checked.unmet == [_ENTRIES[2]]
    assert checked.confirmed[-1:] == [_ENTRIES[0]]


def test_a_call_that_times_out_before_any_entry_starts_is_not_sent_again(monkeypatch, tmp_path):
    calls = []
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})

    def check(code):
        if "check_each" not in code:
            return {"ok": True, "geometry": {}}
        calls.append(code)
        return {"ok": False, "traceback": TIMED_OUT + " after 30.0s -- the Step did not finish.", "geometry": {}}

    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(check)
    reply = {"checks": [{"entry": n, "expr": "True"} for n in range(3)]}
    _model(monkeypatch, reply, reply, reply)

    checked = assertion(_ENTRIES[:3], {}, registry, ScriptedLLMClient([]), EventStream())

    assert len(calls) == 3
    assert checked.defects == _ENTRIES[:3]
