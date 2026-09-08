
def test_call_started_event_is_emitted_before_stream_opens():
    """
    call_started carries phase, model, and ceiling before the stream opens.

    ceiling is absent for clients without a timeout.
    """
    from core.llm import ScriptedLLMClient
    from core.loop.prompt import _streamed
    from core.events import EventStream, CALL_STARTED, COST

    # Test with ScriptedLLMClient (no timeout)
    llm_scripted = ScriptedLLMClient(["test reply"])
    events_scripted = EventStream()

    _streamed(llm_scripted, events_scripted, "design", "system", "user")

    started_events = events_scripted.of(CALL_STARTED)
    assert len(started_events) == 1
    started_data = started_events[0].data
    assert started_data["phase"] == "design"
    assert started_data["model"] == "scripted"
    assert "ceiling" not in started_data


def test_cost_event_always_has_seconds_field():
    """
    cost is emitted for every provider call with seconds always present.

    The usd field is absent when the client reported no price.
    """
    from core.llm import ScriptedLLMClient
    from core.loop.prompt import _streamed
    from core.events import EventStream, COST
    import time

    llm = ScriptedLLMClient(["test reply"])
    events = EventStream()

    start = time.time()
    _streamed(llm, events, "design", "system", "user")
    elapsed = time.time() - start

    cost_events = events.of(COST)
    assert len(cost_events) == 1
    cost_data = cost_events[0].data
    assert "seconds" in cost_data
    assert isinstance(cost_data["seconds"], float)
    assert abs(cost_data["seconds"] - elapsed) < 0.2
    # ScriptedLLMClient has no last_cost, so usd should be absent
    assert "usd" not in cost_data
    assert "prompt_tokens" not in cost_data


def test_cost_event_includes_usd_when_client_reports_price():
    """
    When the client reports a price, cost carries both usd and seconds.
    """
    from litellm.llms.anthropic.chat.handler import ModelResponseIterator
    from core.llm import LiteLLMClient
    from core.loop.prompt import _streamed
    from core.events import EventStream, COST
    import json
    import os

    recorded_path = os.path.join(os.path.dirname(__file__), "recorded",
                                 "anthropic_thinking_stream.json")
    with open(recorded_path, encoding="utf-8") as f:
        recorded = json.load(f)
    it = ModelResponseIterator(streaming_response=None, sync_stream=True)
    chunks = [it.chunk_parser(event) for event in recorded]

    llm = LiteLLMClient(model="claude-3-7-sonnet-20250219", api_key="k",
                        completion=lambda **kwargs: iter(chunks))
    events = EventStream()

    _streamed(llm, events, "design", "system", "user")

    cost_events = events.of(COST)
    assert len(cost_events) == 1
    cost_data = cost_events[0].data
    assert "seconds" in cost_data
    assert isinstance(cost_data["seconds"], float)
    assert cost_data["seconds"] > 0
    assert "usd" in cost_data
    assert cost_data["usd"] == 0.004521
    assert cost_data["model"] == "claude-3-7-sonnet-20250219"
    assert cost_data["phase"] == "design"
    assert cost_data["prompt_tokens"] == 1066
    assert cost_data["completion_tokens"] == 37

def test_format_elapsed_under_one_hour():
    """M:SS format when under an hour."""
    from ui.copilot_panel import _format_elapsed

    assert _format_elapsed(90) == "1:30"
    assert _format_elapsed(45) == "0:45"
    assert _format_elapsed(0) == "0:00"
    assert _format_elapsed(134) == "2:14"
    assert _format_elapsed(3599) == "59:59"


def test_format_elapsed_over_one_hour():
    """H:MM:SS format when >= 1 hour."""
    from ui.copilot_panel import _format_elapsed

    assert _format_elapsed(3600) == "1:00:00"
    assert _format_elapsed(3660) == "1:01:00"
    assert _format_elapsed(7384) == "2:03:04"
