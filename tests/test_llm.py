"""
Level 1 — the LLM client seam against recorded provider responses.

The recordings are raw provider SSE events. They are replayed through LiteLLM's
own parsers, so what these tests assert is our mapping of LiteLLM's real output,
not a mock of LiteLLM.
"""
import json
import os

import pytest

from core.llm import (
    REASONING,
    TEXT,
    Chunk,
    LiteLLMClient,
    ScriptedLLMClient,
    collect,
    litellm_model_name,
)

RECORDED = os.path.join(os.path.dirname(__file__), "recorded")


def _load(name):
    with open(os.path.join(RECORDED, name), encoding="utf-8") as f:
        return json.load(f)


def anthropic_chunks():
    """Recorded Anthropic SSE, through LiteLLM's Anthropic stream parser."""
    from litellm.llms.anthropic.chat.handler import ModelResponseIterator

    it = ModelResponseIterator(streaming_response=None, sync_stream=True)
    return [it.chunk_parser(event) for event in _load("anthropic_thinking_stream.json")]


def openai_chunks():
    """Recorded OpenAI SSE — already LiteLLM's own chunk shape."""
    from litellm.types.utils import ModelResponseStream

    return [ModelResponseStream(**event) for event in _load("openai_stream.json")]


def client_replaying(chunks):
    def completion(**kwargs):
        completion.kwargs = kwargs
        return iter(chunks)

    return LiteLLMClient(model="m", api_key="k", completion=completion), completion


# ── Provider adaptation ───────────────────────────────────────────────────────


def test_anthropic_reasoning_and_text_arrive_separately():
    client, _ = client_replaying(anthropic_chunks())

    reply = collect(client.stream([{"role": "user", "content": "hi"}]))

    assert reply.reasoning == "The user wants a 10mm cube. Part::Box is the primitive for that."
    assert reply.text == "I'll add a 10mm box."


def test_anthropic_reasoning_streams_before_the_answer():
    client, _ = client_replaying(anthropic_chunks())

    kinds = [c.kind for c in client.stream([{"role": "user", "content": "hi"}])]

    assert kinds == [REASONING, REASONING, TEXT, TEXT]


def test_openai_has_no_reasoning_and_still_streams_text():
    client, _ = client_replaying(openai_chunks())

    reply = collect(client.stream([{"role": "user", "content": "hi"}]))

    assert reply.reasoning == ""
    assert reply.text == "I'll add a 10mm box."


def test_stream_is_lazy_so_the_panel_sees_tokens_as_they_arrive():
    chunks = anthropic_chunks()
    seen = []

    def completion(**kwargs):
        for chunk in chunks:
            seen.append(chunk)
            yield chunk

    client = LiteLLMClient(model="m", api_key="k", completion=completion)
    stream = client.stream([{"role": "user", "content": "hi"}])

    assert seen == []          # nothing consumed before the caller pulls
    next(stream)
    assert len(seen) < len(chunks)


# ── Request shaping ───────────────────────────────────────────────────────────


def test_thinking_budget_is_requested_when_configured():
    client, completion = client_replaying(anthropic_chunks())
    client.thinking_budget = 1024

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert completion.kwargs["max_tokens"] >= 1024


def test_no_thinking_key_when_the_provider_has_no_reasoning():
    client, completion = client_replaying(openai_chunks())

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "thinking" not in completion.kwargs
    assert completion.kwargs["stream"] is True


def test_local_endpoint_is_passed_as_api_base():
    client, completion = client_replaying(openai_chunks())
    client.api_base = "http://localhost:11434/v1"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["api_base"] == "http://localhost:11434/v1"


def test_cache_control_on_a_message_is_passed_through_untouched():
    client, completion = client_replaying(anthropic_chunks())
    messages = [
        {"role": "system", "content": [
            {"type": "text", "text": "Document state",
             "cache_control": {"type": "ephemeral"}},
        ]},
    ]

    collect(client.stream(messages))

    assert completion.kwargs["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


# ── Model naming ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider,model_id,expected", [
    ("Anthropic", "claude-3-7-sonnet-20250219", "anthropic/claude-3-7-sonnet-20250219"),
    ("OpenAI", "gpt-4o", "openai/gpt-4o"),
    ("Google", "gemini-2.5-pro", "gemini/gemini-2.5-pro"),
    ("Custom/Local", "llama3", "openai/llama3"),
    ("Anthropic", "anthropic/claude-3-opus-20240229", "anthropic/claude-3-opus-20240229"),
    ("Something The User Typed", "vendor/model", "vendor/model"),
])
def test_model_names_get_the_provider_prefix_litellm_wants(provider, model_id, expected):
    assert litellm_model_name(provider, model_id) == expected


# ── The scripted client, which every other Level 1 test leans on ──────────────


def test_scripted_client_replays_turns_in_order_and_records_what_it_was_asked():
    client = ScriptedLLMClient(["first", {"reasoning": "hmm", "text": "second"}])

    assert collect(client.stream([{"role": "user", "content": "a"}])).text == "first"
    second = collect(client.stream([{"role": "user", "content": "b"}]))

    assert (second.reasoning, second.text) == ("hmm", "second")
    assert [m[0]["content"] for m in client.calls] == ["a", "b"]


def test_scripted_client_accepts_explicit_chunks():
    client = ScriptedLLMClient([[Chunk(TEXT, "a"), Chunk(TEXT, "b")]])

    assert collect(client.stream([])).text == "ab"


def test_scripted_client_running_dry_is_a_loud_test_failure():
    client = ScriptedLLMClient([])

    with pytest.raises(AssertionError, match="ran out of turns"):
        client.stream([])
