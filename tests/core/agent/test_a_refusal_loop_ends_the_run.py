"""
Level 1 -- three finishes in a row refused with nothing built end the run, and any other turn starts the count again.
"""
import json
from types import SimpleNamespace

from core.agent.domain import Domain
from core.agent.solo import run_solo
from core.events import FAILED, EventStream
from core.llm import ScriptedLLMClient
from tests.event_helpers import events_of

FINISH = json.dumps({"finish": "done"})


class Kernel:
    def check(self, code, **_):
        return SimpleNamespace(ok=True, created=["Box"], modified=[], deleted=[], objects=1, names={},
                               signature={}, traceback="", stdout="", checkpoint=None, render=None,
                               geometry={}, volume=None, rolled_back=[])


class Tools:
    def call(self, name, arguments=None):
        return {"status": "applied"}


def _run(replies):
    domain = Domain("part", "", [{"type": "text", "text": "go"}], lambda: None, lambda k: "no solid")
    llm = ScriptedLLMClient(replies)
    events = EventStream()
    result = run_solo("go", domain=domain, llm=llm, kernel=Kernel(), tools=Tools(), events=events,
                      max_turns=10)
    return llm, events, result


def test_three_refused_finishes_with_nothing_built_end_the_run():
    llm, events, result = _run([FINISH] * 5)

    assert len(llm.calls) == 3
    assert not result.ok and result.reason == "model declined or could not start"
    assert events_of(events, FAILED)[0].data["message"] == "model declined or could not start"


def test_a_turn_between_the_refusals_starts_the_count_again():
    llm, _, _ = _run([FINISH, FINISH, json.dumps({"probe": "pass"}), FINISH, FINISH, FINISH, FINISH])

    assert len(llm.calls) == 6
