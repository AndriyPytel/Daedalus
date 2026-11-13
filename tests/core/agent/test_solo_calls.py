"""
Level 1 -- a `code` reply is one `apply_step` call and nothing else; a call that answers `code` is run as a `code` call of the loop; a call that answers anything else is answered as it was.
"""
import json

from core.agent.domain import Domain
from core.agent.solo import run_solo
from core.events import PREFLIGHT_FAILED, EventStream
from core.llm import ScriptedLLMClient
from tests.event_helpers import events_of


class Kernel:
    def __init__(self):
        self.asked = []

    def check(self, code, **_):
        self.asked.append(code)
        raise AssertionError("the loop asked the kernel to run %r itself" % code)


class Tools:
    def __init__(self, answers=()):
        self.calls = []
        self.answers = list(answers)

    def call(self, name, arguments=None):
        self.calls.append((name, arguments))
        return self.answers.pop(0) if self.answers else {"status": "applied", "created": ["Box"],
                                                         "modified": [], "description": {"big": 1}}


def _run(replies, calls=None, tools=None, max_turns=4):
    tools = tools or Tools()
    kernel = Kernel()
    domain = Domain("bim", "", [{"type": "text", "text": "go"}], lambda: None, lambda k: None,
                    calls or {})
    llm = ScriptedLLMClient(replies)
    events = EventStream()
    result = run_solo("go", domain=domain, llm=llm, kernel=kernel, tools=tools, events=events,
                      max_turns=max_turns)
    return kernel, tools, llm, events, result


FINISH = json.dumps({"finish": "done"})


def test_a_code_call_is_one_apply_step_call():
    kernel, tools, _, _, result = _run([json.dumps({"code": "box = 1"}), FINISH])

    assert tools.calls == [("apply_step", {"code": "box = 1"})]
    assert kernel.asked == []
    assert result.ok and result.steps_applied == 1


def test_the_model_is_answered_with_what_apply_said_without_the_description():
    _, _, llm, _, _ = _run([json.dumps({"code": "box = 1"}), FINISH])

    answer = json.loads(llm.calls[1][-1]["content"])
    assert answer["status"] == "applied" and answer["created"] == ["Box"]
    assert "description" not in answer


def test_a_refusal_is_answered_as_it_came_and_said_on_the_stream():
    refusal = {"status": "failed", "gate": "changed_nothing", "traceback": "changed nothing",
               "volume": {"before": 1}}
    _, _, llm, events, result = _run([json.dumps({"code": "print(1)"}), FINISH],
                                     tools=Tools([refusal]))

    assert json.loads(llm.calls[1][-1]["content"]) == refusal
    assert events_of(events, PREFLIGHT_FAILED)[0].data["gate"] == "changed_nothing"
    assert result.steps_applied == 0


def test_a_call_that_answers_code_is_run_as_a_code_turn_and_landed():
    kernel, tools, _, _, result = _run([json.dumps({"place": {}}), FINISH],
                                       calls={"place": lambda argument: {"code": "box = 1"}})

    assert tools.calls == [("apply_step", {"code": "box = 1"})]
    assert kernel.asked == [] and result.ok and result.steps_applied == 1


def test_a_call_that_answers_without_code_is_answered_as_it_was_and_runs_nothing():
    summary = {"ok": True, "name": "Plate", "volume": 500.0}

    kernel, tools, llm, _, _ = _run([json.dumps({"place": {}}), FINISH],
                                    calls={"place": lambda argument: summary})

    assert kernel.asked == [] and tools.calls == []
    assert json.loads(llm.calls[1][-1]["content"]) == summary


def test_the_same_deletion_refused_twice_ends_the_run_with_a_reason():
    refusal = {"status": "failed", "gate": "ownership", "traceback": "a rebuild, not approved"}
    code = json.dumps({"code": "wipe()"})

    _, _, llm, _, result = _run([code, code, code, FINISH], tools=Tools([refusal, refusal]),
                                max_turns=6)

    assert len(llm.calls) == 2
    assert not result.ok
    assert "refused" in result.reason and "rebuild, not approved" in result.reason
