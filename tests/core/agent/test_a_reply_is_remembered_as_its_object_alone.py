"""
A reply that writes past its own object -- a made-up tool result and its own
analysis after the JSON -- is read for the object alone, and only the object
is remembered. Replayed whole, that text is what the provider's reasoning
extraction filter refused a 135-minute run over.
"""
import json

from core.agent import agent
from core.llm import Session
from core.llm.types import TEXT, Chunk


class _Says:
    model = "fake"

    def __init__(self, text):
        self._text = text

    def stream(self, messages, **overrides):
        yield Chunk(TEXT, self._text)


def test_text_after_the_object_is_not_remembered():
    reply = json.dumps({"code": "print(1)"}) + '\n{"stdout": "9"}\n◁ANALYSIS▷ my reasoning'
    session = Session()

    text = agent._say(session, _Says(reply), "go")

    assert agent._parsed(text) == {"code": "print(1)"}
    assert json.loads(session.messages[-1]["content"]) == {"code": "print(1)"}


def test_a_reply_that_is_no_object_is_remembered_as_written():
    session = Session()

    agent._say(session, _Says("not json at all"), "go")

    assert session.messages[-1]["content"] == "not json at all"
