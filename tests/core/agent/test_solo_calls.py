"""
Level 1 -- a call that answers `code` is run as a `code` call of the loop; a call that answers anything else is answered as it was.
"""
import json
from types import SimpleNamespace

from core.agent.domain import Domain
from core.agent.solo import run_solo
from core.events import EventStream
from core.llm import ScriptedLLMClient


class Kernel:
    def __init__(self):
        self.ran = []

    def check(self, code, **_):
        self.ran.append(code)
        return SimpleNamespace(ok=True, created=["Box"], modified=[], deleted=[], objects=1, names={},
                               signature={}, traceback="", stdout="", checkpoint=None, render=None,
                               geometry={}, volume=None, rolled_back=[])


class Tools:
    def call(self, name, arguments=None):
        return {"status": "applied"}


def _run(call):
    kernel = Kernel()
    domain = Domain("bim", "", [{"type": "text", "text": "go"}], lambda: None, lambda k: None, {"place": call})
    llm = ScriptedLLMClient([json.dumps({"place": {}}), json.dumps({"finish": "done"})])
    result = run_solo("go", domain=domain, llm=llm, kernel=kernel, tools=Tools(), events=EventStream(), max_turns=4)
    return kernel, llm, result


def test_a_call_that_answers_code_is_run_as_a_code_turn_and_landed():
    kernel, llm, result = _run(lambda argument: {"code": "box = 1"})

    assert kernel.ran == ["box = 1"] and result.ok and result.steps_applied == 1
    assert json.loads(llm.calls[1][-1]["content"])["ok"] is True


def test_a_call_that_answers_without_code_is_answered_as_it_was_and_runs_nothing():
    summary = {"ok": True, "name": "Plate", "volume": 500.0}

    kernel, llm, _ = _run(lambda argument: summary)

    assert kernel.ran == [] and json.loads(llm.calls[1][-1]["content"]) == summary


class RefusingTools:
    def call(self, name, arguments=None):
        if name == "apply_step":
            return {"status": "failed", "gate": "ownership", "traceback": "a rebuild, not approved"}
        return {"path": "/nowhere"}


def test_the_same_deletion_refused_twice_ends_the_run_with_a_reason():
    kernel = Kernel()
    domain = Domain("part", "", [{"type": "text", "text": "go"}], lambda: None, lambda k: None, {})
    code = json.dumps({"code": "wipe()"})
    llm = ScriptedLLMClient([code, code, code, json.dumps({"finish": "done"})])

    result = run_solo("go", domain=domain, llm=llm, kernel=kernel, tools=RefusingTools(),
                      events=EventStream(), max_turns=6)

    assert len(llm.calls) == 2
    assert not result.ok
    assert "refused" in result.reason and "rebuild, not approved" in result.reason
