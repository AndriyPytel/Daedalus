"""
Claude's own tool-call markup, spoken as plain text instead of the JSON
protocol -- excerpts of `logs/cgb-opus-medium-s1/opus/{102,107}.*.jsonl`,
the run that stayed silent 2,349 turns of it until the 900s ceiling.
"""
import json
import os

from core.agent import agent
from core.events import CALL_STARTED, EventStream, INVOKE_MARKUP
from core.llm import Session
from core.llm.types import TEXT, Chunk
from tests.event_helpers import events_of

MARKUP = os.path.join(os.path.dirname(__file__), "invoke_markup")


def excerpt(name):
    with open(os.path.join(MARKUP, name + ".txt")) as handle:
        return handle.read()


class _AbortTrackingLLM:
    """
    A scripted LLM whose stream is closed rather than drained, so a test can
    tell an early abort from a reply that simply ended: `aborted[call]` is
    `True` exactly when the generator serving that call never ran to its
    own end.
    """

    def __init__(self, turns):
        self.model = "fake"
        self._turns = list(turns)
        self.calls = []
        self.aborted = []

    def stream(self, messages, **overrides):
        self.calls.append(messages)
        return self._chunks(self._turns[len(self.calls) - 1])

    def _chunks(self, text):
        ran_to_completion = False
        try:
            for start in range(0, len(text), 40):
                yield Chunk(TEXT, text[start:start + 40])
            ran_to_completion = True
        finally:
            self.aborted.append(not ran_to_completion)


def test_a_code_invoke_is_recovered_and_the_call_ends_early():
    llm = _AbortTrackingLLM([excerpt("fcc_turn_code")])
    session = Session()

    text = agent._say(session, llm, "do the thing")

    assert llm.aborted == [True]
    assert len(llm.calls) == 1
    message = agent._parsed(text)
    assert "code" in message
    assert message["code"].startswith("import math")


def test_a_declare_invoke_is_recovered_whole():
    llm = _AbortTrackingLLM([excerpt("fcc_turn_declare")])
    session = Session()

    text = agent._say(session, llm, "do the thing")

    assert llm.aborted == [True]
    message = agent._parsed(text)
    assert message["declare"]["op_vocabulary"] == "closed"
    assert len(message["declare"]["steps"]) == 2


def test_a_bash_invoke_has_no_protocol_key_and_is_corrected():
    llm = _AbortTrackingLLM([excerpt("bash_command"),
                             json.dumps({"declare": {"step": "done"}})])
    session = Session()

    text = agent._say(session, llm, "do the thing")

    assert llm.aborted == [True, False]
    assert len(llm.calls) == 2
    corrected_with = llm.calls[1][-1]["content"]
    assert "JSON protocol" in corrected_with
    assert "no Bash" in corrected_with
    assert agent._parsed(text) == {"declare": {"step": "done"}}


def test_recovering_an_invoke_emits_an_event():
    llm = _AbortTrackingLLM([excerpt("fcc_turn_code")])
    session = Session()
    stream = EventStream()

    agent._say(session, llm, "do the thing", phase="execute", stream=stream)

    invoked = stream.last(INVOKE_MARKUP)
    assert invoked is not None
    assert invoked.data["name"] == "fcc_turn"
    assert invoked.data["recovered"] is True
    assert events_of(stream, CALL_STARTED)
