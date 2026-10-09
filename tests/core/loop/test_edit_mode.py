"""
With a base part present, Plan, Execute and Assertion are told so: the request
carries `the_base_part`, the prompts carry one edit-mode paragraph that keys on it,
and Assertion always runs the collateral check. Without a base nothing changes.
"""
import json

from core.agent import agent as agent_module
from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop import execute as execute_module
from core.loop import plan as plan_module
from core.loop.assertion import assertion
from core.prompts import read_prompt
from core.tools import ToolRegistry

BASE = "The part before the edit, in mm:\nvolume: 10000 mm3, solids: 1"


def _tools(calls):
    registry = ToolRegistry()

    def check(code):
        calls.append(code)
        return {"ok": True, "held": [], "geometry": {}}

    registry.tool("check_assertions", "Measure.", {
        "type": "object", "properties": {"code": {"type": "string"}}})(check)
    registry.tool("document_copy", "Copy.")(lambda: {"path": "/tmp/none.FCStd"})
    return registry


def _assert_with(monkeypatch, base):
    seen = {"checks": [], "task": []}

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        seen["task"].append(task)
        return {"code": "assert True"}, None, []

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)
    assertion(["the bore is 8 mm"], {}, _tools(seen["checks"]), ScriptedLLMClient([]),
              EventStream(), base=base)
    return seen


def test_assertion_with_a_base_runs_the_collateral_check_and_shows_the_base(monkeypatch):
    seen = _assert_with(monkeypatch, BASE)

    assert any("no_collateral(doc)" in code for code in seen["checks"])
    assert BASE in json.loads(seen["task"][0])["the_base_part"]


def test_assertion_with_a_base_does_not_check_self_intersection(monkeypatch):
    """The base carries its own self-intersections into every edit, and the check alone outlasts the kernel's 30 s on a 2000-face part."""
    seen = _assert_with(monkeypatch, BASE)

    assert not any("BOPAlgo_ArgumentAnalyzer" in code for code in seen["checks"])


def test_assertion_without_a_base_is_as_it_was(monkeypatch):
    seen = _assert_with(monkeypatch, None)

    assert not any("no_collateral" in code for code in seen["checks"])
    assert any("BOPAlgo_ArgumentAnalyzer" in code for code in seen["checks"])
    assert "the_base_part" not in json.loads(seen["task"][0])


def test_plan_request_carries_the_base_only_when_there_is_one(monkeypatch):
    tasks = []

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        tasks.append(json.loads(task))
        return {"question": "which?"}, None, []

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)
    plan_module.plan("edit", {}, ScriptedLLMClient([]), EventStream(), base=BASE)
    plan_module.plan("make", {}, ScriptedLLMClient([]), EventStream())

    assert tasks[0]["the_base_part"] == BASE
    assert "the_base_part" not in tasks[1]


def test_execute_signature_takes_the_base():
    import inspect

    assert "base" in inspect.signature(execute_module.execute).parameters


def test_each_edit_prompt_has_its_paragraph_keyed_on_the_base():
    for name in ("plan", "execute", "assertion"):
        text = read_prompt(name + ".md")
        assert "`the_base_part`" in text, name
    assert "pick_faces" in read_prompt("execute.md")
    assert "already checked" in read_prompt("assertion.md")
    assert "never rebuild" in read_prompt("plan.md").lower()
