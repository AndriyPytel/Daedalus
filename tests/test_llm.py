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
    ProviderRefused,
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


def truncated_chunks():
    """Recorded OpenAI SSE that stops on the output cap: finish_reason 'length'."""
    from litellm.types.utils import ModelResponseStream

    return [ModelResponseStream(**event) for event in _load("openai_length_stream.json")]


def client_replaying(chunks):
    def completion(**kwargs):
        completion.kwargs = kwargs
        return iter(chunks)

    return LiteLLMClient(model="m", api_key="k", completion=completion), completion


def client_refused_by(exc, after=()):
    """A client whose provider raises *exc*, optionally after streaming *after*."""
    def completion(**kwargs):
        for chunk in after:
            yield chunk
        raise exc

    return LiteLLMClient(model="gemini/gemini-2.5-pro", api_key="k", completion=completion)


def mapped(status_code, body, provider="gemini", model="gemini/gemini-2.5-pro"):
    """A provider payload through LiteLLM's own exception mapping, as in a real call."""
    from litellm.litellm_core_utils.exception_mapping_utils import exception_type
    from litellm.llms.gemini.common_utils import GeminiError

    raw = GeminiError(status_code=status_code, message=json.dumps(body))
    try:
        exception_type(model=model, original_exception=raw, custom_llm_provider=provider)
    except Exception as exc:
        return exc
    raise AssertionError("LiteLLM did not classify that payload as an error at all")


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


# ── A provider that would not answer ──────────────────────────────────────────


def test_an_exhausted_google_quota_is_named_as_one_with_the_model_and_the_wait():
    recorded = _load("google_429_quota.json")
    client = client_refused_by(mapped(recorded["status_code"], recorded["body"]))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    said = str(refusal.value)
    assert "gemini/gemini-2.5-pro" in said
    assert "quota" in said and "exhausted" in said
    assert "26s" in said, "the provider said when it resets and the user was not told"
    assert "JSON" not in said


def test_the_provider_s_own_words_are_kept_beside_the_translation():
    recorded = _load("google_429_quota.json")
    client = client_refused_by(mapped(recorded["status_code"], recorded["body"]))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "RESOURCE_EXHAUSTED" in refusal.value.said
    assert "generate_content_free_tier_requests" in refusal.value.said


def test_a_rate_limit_and_an_exhausted_quota_do_not_read_the_same():
    """
    Google sends the same RESOURCE_EXHAUSTED body for both; only the violated
    metric says whether the remedy is waiting seconds or waiting a day.
    """
    recorded = _load("google_429_rate_limit.json")
    limited = client_refused_by(mapped(recorded["status_code"], recorded["body"]))

    with pytest.raises(ProviderRefused) as refusal:
        collect(limited.stream([{"role": "user", "content": "hi"}]))

    said = str(refusal.value)
    assert "rate limited" in said
    assert "quota" not in said, "waiting seconds and waiting a day must not read alike"
    assert "20s" in said


def test_the_status_the_provider_answered_with_is_kept_too():
    recorded = _load("google_429_quota.json")
    client = client_refused_by(mapped(recorded["status_code"], recorded["body"]))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "429" in refusal.value.said


def test_being_out_of_credit_is_named_as_a_billing_problem():
    import litellm

    broke = client_refused_by(litellm.RateLimitError(
        message="You exceeded your current quota. Error code: insufficient_quota",
        llm_provider="openai", model="openai/gpt-4o"))

    with pytest.raises(ProviderRefused) as refusal:
        collect(broke.stream([{"role": "user", "content": "hi"}]))

    assert "out of credit" in str(refusal.value)
    assert "billing" in str(refusal.value)


def test_an_oversized_request_says_the_context_window_not_a_bad_reply():
    client = client_refused_by(mapped(400, {"error": {
        "code": 400,
        "message": "The input token count (2000000) exceeds the maximum number of tokens "
                   "allowed (1048576).",
    }}))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "context window" in str(refusal.value)
    assert "smaller pieces" in str(refusal.value)


def test_a_rejected_key_says_so_in_the_same_words_the_providers_page_would():
    client = client_refused_by(mapped(401, {"error": {
        "code": 401, "message": "API key not valid. Please pass a valid API key.",
    }}))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "key was rejected" in str(refusal.value)
    assert "Providers page" in str(refusal.value)


def test_a_dropped_local_endpoint_is_a_transport_failure_not_an_account_limit():
    import litellm

    client = client_refused_by(litellm.APIConnectionError(
        message="Connection error.", llm_provider="openai", model="openai/llama3"))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "connection failed" in str(refusal.value)
    assert "not a limit on your account" in str(refusal.value)


def test_a_reply_cut_off_by_the_output_cap_raises_instead_of_looking_complete():
    client, _ = client_replaying(truncated_chunks())

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "ran out of output tokens" in str(refusal.value)


def test_the_output_cap_is_named_so_the_user_knows_what_to_raise():
    client, _ = client_replaying(truncated_chunks())
    client.thinking_budget = 2048

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "8192-token" in str(refusal.value), "the implicit budget * 4 cap stayed invisible"


def test_a_refusal_halfway_through_a_stream_is_still_a_refusal():
    import litellm

    client = client_refused_by(
        litellm.RateLimitError(message="quota exceeded", llm_provider="gemini",
                               model="gemini/gemini-2.5-pro"),
        after=openai_chunks()[:2],
    )

    with pytest.raises(ProviderRefused):
        collect(client.stream([{"role": "user", "content": "hi"}]))


def test_a_failure_that_is_not_the_provider_refusing_is_left_alone():
    client = client_refused_by(ZeroDivisionError("a bug in our own mapping"))

    with pytest.raises(ZeroDivisionError):
        collect(client.stream([{"role": "user", "content": "hi"}]))


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
