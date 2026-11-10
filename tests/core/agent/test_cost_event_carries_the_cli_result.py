"""
The `cost` event of a call carries what the CLI's `result` event said about it.

No provider is called: a scripted client with a `last_cost` stands in for the one that parsed the result.
"""
from core.agent import agent
from core.events import COST, EventStream
from core.llm import ScriptedLLMClient, Session
from event_helpers import first_event


class Priced(ScriptedLLMClient):
    last_cost = {"usd": 0.5, "model": "opus", "stop_reason": "end_turn", "duration_api_ms": 1234,
                 "reasoning_tokens": 77, "num_turns": 1, "api_error_status": 529,
                 "terminal_reason": "model_error"}


def test_the_cost_event_carries_stop_reason_thinking_tokens_and_api_time():
    stream = EventStream()

    agent._say(Session(), Priced(["answer"]), "question", phase="read", stream=stream)

    cost = first_event(stream, COST).data
    assert cost["stop_reason"] == "end_turn"
    assert cost["duration_api_ms"] == 1234
    assert cost["reasoning_tokens"] == 77
    assert cost["num_turns"] == 1
    assert cost["api_error_status"] == 529
    assert cost["terminal_reason"] == "model_error"
