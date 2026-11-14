"""
A call that answers nothing for too long is cut and sent again, and the run gives up only after a long wait and one more try.

No provider is called: `ScriptedLLMClient` plays the stalls, and the clock is the `sleep` the module calls.
"""
import json

import pytest

from core.agent import agent, reader
from core.events import FAILED, EventStream
from core.llm import CallStalled, ScriptedLLMClient, Session
from core.llm.effort_policy import STALL_LIMIT, STALL_WAIT
from event_helpers import first_event

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f"
       b"\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(agent.time, "sleep", waits.append)
    return waits


class StallWatching(ScriptedLLMClient):
    """A scripted client that notes the stall limit it was given at each call."""

    stall = None

    def stream(self, messages, **overrides):
        self.limits = getattr(self, "limits", []) + [self.stall]
        return super().stream(messages, **overrides)


def test_a_stalled_call_is_sent_again_at_once_with_the_conversation_as_it_was(slept):
    client = ScriptedLLMClient([CallStalled("no reply"), "the answer"])
    session = Session(system="rules")

    assert agent._say(session, client, "question") == "the answer"

    assert slept == []
    assert client.calls[0] == client.calls[1]
    assert [m["role"] for m in session.messages] == ["system", "user", "assistant"]


def test_two_stalls_in_a_row_wait_before_the_third_try(slept):
    client = ScriptedLLMClient([CallStalled("a"), CallStalled("b"), "the answer"])

    assert agent._say(Session(), client, "question") == "the answer"

    assert slept == [STALL_WAIT]
    assert STALL_WAIT == 600


def test_a_third_stall_fails_the_call(slept):
    client = ScriptedLLMClient([CallStalled("a"), CallStalled("b"), CallStalled("c"), "never"])

    with pytest.raises(CallStalled):
        agent._say(Session(), client, "question")

    assert slept == [STALL_WAIT]
    assert len(client.turns) == 1


def test_only_the_first_call_of_a_conversation_has_the_shorter_limit(slept):
    client = StallWatching(["one", "two"])
    session = Session()

    agent._say(session, client, "first")
    agent._say(session, client, "second")

    assert client.limits == [STALL_LIMIT, None]
    assert client.stall is None
    assert STALL_LIMIT == 300


def test_a_drawing_that_could_not_be_read_twice_ends_the_run_instead_of_building_blind(tmp_path, monkeypatch):
    overview = tmp_path / "overview.png"
    overview.write_bytes(PNG)
    sheet = type("Sheet", (), {"overview": str(overview), "crops": []})()
    monkeypatch.setattr(reader.sheet_reader, "read", lambda path: sheet)
    llm = ScriptedLLMClient(["I cannot read it.", "Still nothing.", "never asked"])
    events = EventStream()

    result = reader.run_reader("Build it.", llm=llm, kernel=None, tools=None, events=events,
                               start=lambda rules: None, attachments=["drawing.pdf"])

    assert not result.ok
    assert result.reason == "drawing not read"
    assert result.steps_applied == 0
    assert first_event(events, FAILED).data["message"] == "drawing not read"
    assert llm.turns == ["never asked"]
