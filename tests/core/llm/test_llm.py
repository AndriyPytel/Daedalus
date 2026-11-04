"""
Level 1 — the LLM client seam against recorded provider responses.

The recordings are raw provider SSE events. They are replayed through LiteLLM's
own parsers, so what these tests assert is our mapping of LiteLLM's real output,
not a mock of LiteLLM.
"""
import json
import os

import pytest

from conftest import collect
from tests import modelmania_harness

from core.llm import (
    REASONING,
    TEXT,
    Chunk,
    LiteLLMClient,
    ProviderRefused,
    ScriptedLLMClient,
    StreamCutOff,
    accepts_pdf,
    cache_control_supported,
    litellm_model_name,
    unusable_attachments,
)

RECORDED = modelmania_harness.RECORDED_DIR


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


def cut_off_chunks():
    """
    Recorded OpenAI SSE with its terminal chunk removed: what a stream the
    provider closes mid-reply arrives as — no finish_reason, no usage, no
    [DONE], and no exception either.
    """
    return openai_chunks()[:-1]


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
    """Nothing is consumed from the provider before the caller pulls."""
    chunks = anthropic_chunks()
    seen = []

    def completion(**kwargs):
        for chunk in chunks:
            seen.append(chunk)
            yield chunk

    client = LiteLLMClient(model="m", api_key="k", completion=completion)
    stream = client.stream([{"role": "user", "content": "hi"}])

    assert seen == []
    next(stream)
    assert len(seen) < len(chunks)


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


def test_a_model_that_cannot_accept_images_says_so_and_names_itself():
    """
    An attachment on a text-only model must read as "switch models", not as a
    crash.
    """
    import litellm

    blind = client_refused_by(litellm.BadRequestError(
        message="Invalid content type. image_url is only supported by certain models.",
        llm_provider="openai", model="openai/gpt-3.5-turbo"))

    with pytest.raises(ProviderRefused) as refusal:
        collect(blind.stream([{"role": "user", "content": "hi"}]))

    assert blind.model in str(refusal.value), "the refusal did not name the model"
    assert "cannot accept images" in str(refusal.value)
    assert "image support" in str(refusal.value)


def test_an_ordinary_bad_request_is_still_not_a_refusal():
    """Only the image case is translated; a malformed request stays a bug."""
    import litellm

    client = client_refused_by(litellm.BadRequestError(
        message="Unknown parameter: 'temperture'.",
        llm_provider="openai", model="openai/gpt-4o"))

    with pytest.raises(litellm.BadRequestError):
        collect(client.stream([{"role": "user", "content": "hi"}]))


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


def test_a_provider_that_times_out_mid_stream_is_a_refusal_not_a_traceback():
    """
    OpenRouter gave up on a stealth model after 311814ms and LiteLLM raised
    `MidStreamFallbackError`, a `ServiceUnavailableError` rather than a
    `Timeout`, so it reached the panel as a traceback.
    """
    import litellm
    from litellm.exceptions import MidStreamFallbackError

    client = client_refused_by(MidStreamFallbackError(
        message="Provider timed out after 311814ms", llm_provider="openai",
        model="openai/stealth/union-alpha",
        original_exception=litellm.APIConnectionError(
            message="Provider timed out", llm_provider="openai",
            model="openai/stealth/union-alpha")),
        after=openai_chunks()[:2])

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "did not answer in time" in str(refusal.value)


def test_a_provider_that_times_out_before_a_word_is_tried_again():
    """
    A cloaked model on OpenRouter times out at the gateway's own 300s ceiling
    with nothing sent yet, and the whole run is lost to a wait the user was
    willing to sit through. Nothing has reached the caller, so the call can be
    made again without repeating text.
    """
    from litellm.exceptions import MidStreamFallbackError

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise MidStreamFallbackError(
                message="Provider timed out after 311814ms", llm_provider="openai",
                model="openai/stealth/union-alpha")
        return iter(openai_chunks())

    client = LiteLLMClient(model="m", api_key="k", completion=completion)

    text = "".join(chunk.text for chunk in client.stream([{"role": "user", "content": "hi"}])
                   if chunk.kind == TEXT)

    assert len(calls) == 2
    assert text


def test_a_provider_that_keeps_timing_out_refuses_rather_than_retrying_forever():
    from litellm.exceptions import MidStreamFallbackError

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        raise MidStreamFallbackError(
            message="Provider timed out after 311814ms", llm_provider="openai",
            model="openai/stealth/union-alpha")
        yield

    client = LiteLLMClient(model="m", api_key="k", completion=completion)

    with pytest.raises(ProviderRefused):
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert len(calls) == 3


def test_a_retry_says_so_where_the_user_is_watching():
    """A silent retry is a panel that looks dead for as long as three timeouts last."""
    from litellm.exceptions import MidStreamFallbackError

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise MidStreamFallbackError(
                message="Provider timed out after 311814ms", llm_provider="openai",
                model="openrouter/stealth/union-alpha")
        return iter(openai_chunks())

    client = LiteLLMClient(model="m", api_key="k", completion=completion)

    said = [chunk.text for chunk in client.stream([{"role": "user", "content": "hi"}])
            if chunk.kind == REASONING]

    assert any("again" in text for text in said), said


def test_an_account_that_cannot_afford_the_output_cap_is_told_what_to_do():
    """
    OpenRouter refuses the whole call rather than trimming: "This request
    requires more credits, or fewer max_tokens. You requested up to 65536
    tokens, but can only afford 16000."
    """
    import litellm

    client = client_refused_by(litellm.APIError(
        status_code=402, message="This request requires more credits, or fewer max_tokens. "
        "You requested up to 65536 tokens, but can only afford 16000.",
        llm_provider="openrouter", model="openrouter/x-ai/grok-4.1"))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "credit" in str(refusal.value)


def test_a_failure_that_is_not_the_provider_refusing_is_left_alone():
    client = client_refused_by(ZeroDivisionError("a bug in our own mapping"))

    with pytest.raises(ZeroDivisionError):
        collect(client.stream([{"role": "user", "content": "hi"}]))


def test_thinking_budget_is_requested_when_configured():
    client, completion = client_replaying(anthropic_chunks())
    client.thinking_budget = 1024

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert completion.kwargs["max_tokens"] >= 1024


def test_a_reasoning_model_is_told_how_hard_to_think():
    """
    Uncapped, kimi-k3 spent ten minutes circling one dimension and never left
    its own reasoning. What it is told is the dial, which is
    `medium` until the user moves it.
    """
    client, completion = client_replaying(openai_chunks())
    client.model = "openai/kimi-k3"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["reasoning_effort"] == "medium"


def test_a_model_that_does_not_reason_is_not_told_how_hard_to_think():
    """gpt-4o has no reasoning to spend, and rejects what it was not asked for."""
    client, completion = client_replaying(openai_chunks())
    client.model = "openai/gpt-4o"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "reasoning_effort" not in completion.kwargs


def test_a_thinking_budget_is_how_hard_to_think_where_there_is_one():
    """Anthropic takes a budget in tokens; the two ways of saying it never both go."""
    client, completion = client_replaying(anthropic_chunks())
    client.model = "anthropic/claude-3-7-sonnet-20250219"
    client.thinking_budget = 2048

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "reasoning_effort" not in completion.kwargs
    assert completion.kwargs["thinking"]["budget_tokens"] == 2048


def test_a_caller_s_own_reasoning_effort_wins():
    client, completion = client_replaying(openai_chunks())
    client.model = "openai/kimi-k3"

    collect(client.stream([{"role": "user", "content": "hi"}], reasoning_effort="high"))

    assert completion.kwargs["reasoning_effort"] == "high"


def test_a_reply_gets_an_output_cap_even_where_no_thinking_budget_is_configured():
    """A reasoning model with no cap spends the gateway default on thought alone."""
    client, completion = client_replaying(openai_chunks())

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["max_tokens"] >= 16000


def test_the_cap_never_exceeds_what_the_model_can_write():
    """A model asked for more output than it has is rejected outright."""
    client, completion = client_replaying(openai_chunks())
    client.model = "openai/gpt-4o"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["max_tokens"] == 16384


def test_a_caller_s_own_output_cap_wins():
    client, completion = client_replaying(openai_chunks())

    collect(client.stream([{"role": "user", "content": "hi"}], max_tokens=64))

    assert completion.kwargs["max_tokens"] == 64


def truncated_chunks_with_usage():
    """
    A cap-hit stream whose trailing usage chunk still arrives, as
    `stream_options: {"include_usage": True}` asks for on every OpenAI-compatible
    stream this route makes -- a gateway that answers that flag sends usage after
    `finish_reason: length` exactly as after `stop`.
    """
    from litellm.types.utils import ModelResponseStream, Usage

    return truncated_chunks() + [ModelResponseStream(
        choices=[], usage=Usage(prompt_tokens=17205, completion_tokens=32768,
                                completion_tokens_details={"reasoning_tokens": 30000}))]


def test_a_truncated_reply_still_records_the_usage_the_provider_sent():
    """
    A call that hit the output cap is priced from whatever usage travelled with
    it, not left at `seconds` alone -- the gap that made a whole sweep price at
    $0.00 while it burned tens of thousands of tokens.
    """
    client, _ = client_replaying(truncated_chunks_with_usage())
    client.model = "openai/kimi-k3"

    with pytest.raises(ProviderRefused):
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert client.last_cost["prompt_tokens"] == 17205
    assert client.last_cost["completion_tokens"] == 32768
    assert client.last_cost["reasoning_tokens"] == 30000


def test_a_truncated_reply_with_no_usage_at_all_says_so_rather_than_nothing(monkeypatch):
    """
    `_billed` itself has nothing to reassemble a price or a token count from --
    checked directly, LiteLLM's `stream_chunk_builder` still estimates prompt
    tokens off the sent `messages` even where a truncated reply carries no
    usage of its own, so the one way a truncated call ends up with neither is
    the reassembly itself failing.
    """
    import core.llm.client as client_module

    monkeypatch.setattr(client_module, "_billed", lambda model, chunks, messages: (None, None))
    client, _ = client_replaying(truncated_chunks())
    client.model = "openai/kimi-k3"

    with pytest.raises(ProviderRefused):
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert client.last_cost == {"model": "openai/kimi-k3", "usage_unavailable": True}


def test_a_clean_reply_still_reports_reasoning_tokens_separately():
    """
    Reasoning is billed as completion tokens and counted against the same cap
    as the rest of the reply, but priced separately so it can be told apart
    from a run that used its whole budget on the final answer.
    """
    client, _ = client_replaying(anthropic_chunks())
    client.model = "claude-3-7-sonnet-20250219"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert client.last_cost["reasoning_tokens"] == 18


def test_the_default_output_cap_is_65536_for_a_model_litellm_has_no_ceiling_for():
    """
    kimi-k2.7-code, mimo-v2.5 and deepseek-v4-flash-vision-exp all died at the
    old 32768-token cap doing genuine, non-repeating drawing analysis
    (logs/model-sweep-20260913/analysis-thinking-tokens.md) -- none of them is
    a model LiteLLM has a ceiling on file for, so the default is what they get.
    """
    client, completion = client_replaying(openai_chunks())
    client.model = "openai/kimi-k2.7-code"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["max_tokens"] == 65536


def test_the_ceiling_the_provider_listed_caps_a_model_litellm_does_not_know():
    client, completion = client_replaying(openai_chunks())
    client.model = "openai/qwen/qwen3.8-27b"
    client.output_ceiling = 16384

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["max_tokens"] == 16384


def test_an_openai_compatible_gateway_is_asked_to_report_what_it_spent():
    """Without usage the gateway's stream ends silently and a cut-off looks complete."""
    client, completion = client_replaying(openai_chunks())
    client.model = "openai/kimi-k3"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert completion.kwargs["stream_options"] == {"include_usage": True}


def test_a_gateway_that_closes_the_stream_mid_reply_says_so():
    """
    Measured against the opencode gateway with raw HTTP and no LiteLLM in the
    call: four of seven long streams closed with no terminal chunk at all
    (anomalyco/opencode#44148). Read as a finished answer, that is a phase
    silently missing its reply.
    """
    client, _ = client_replaying(cut_off_chunks())
    client.model = "openai/kimi-k3"

    with pytest.raises(StreamCutOff) as cut:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "closed the stream" in str(cut.value)


def test_a_cut_off_stream_does_not_blame_the_model_for_thinking():
    """A plain text reply with no reasoning in it is cut off just as readily."""
    client, _ = client_replaying(cut_off_chunks())
    client.model = "openai/kimi-k3"

    with pytest.raises(StreamCutOff) as cut:
        collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "effort" not in str(cut.value) and "thinks less" not in str(cut.value)


def test_a_stream_that_ends_on_its_own_terminal_chunk_is_left_alone():
    client, _ = client_replaying(openai_chunks())
    client.model = "openai/kimi-k3"

    assert collect(client.stream([{"role": "user", "content": "hi"}]))


def test_a_provider_that_reports_no_finish_reason_at_all_is_left_alone():
    """The check reads the terminal chunk an OpenAI-compatible endpoint owes; no other route promises one."""
    client, _ = client_replaying(anthropic_chunks()[:-1])
    client.model = "anthropic/claude-3-7-sonnet-20250219"

    assert collect(client.stream([{"role": "user", "content": "hi"}]))


def test_a_provider_that_is_not_openai_compatible_is_not_sent_stream_options():
    client, completion = client_replaying(anthropic_chunks())
    client.model = "anthropic/claude-3-7-sonnet-20250219"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "stream_options" not in completion.kwargs


def test_a_priced_streaming_call_sets_last_cost():
    """
    `anthropic_thinking_stream.json` is the one recording in this file whose
    `usage` survives to a priceable `ModelResponse`: `message_start` carries
    Anthropic's input and cache counts and the closing `message_delta` carries
    the output count, and LiteLLM's own `ModelResponseIterator.chunk_parser`
    turns both into a `Usage` on the chunk (checked directly against the
    installed litellm 1.95.0 before writing this — `openai_stream.json` has
    none at all). The dollar figure is `litellm.completion_cost` computed
    offline from that recording's fixed token counts against the installed
    price table, so it is pinned rather than merely asserted positive, the
    house style `tests/test_loop.py:3780` sets for a subscription call.
    """
    client, _ = client_replaying(anthropic_chunks())
    client.model = "claude-3-7-sonnet-20250219"

    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert client.last_cost == {
        "usd": 0.004521, "model": "claude-3-7-sonnet-20250219",
        "prompt_tokens": 1066, "completion_tokens": 37, "reasoning_tokens": 18,
        "cache_creation_tokens": 1024,
    }


def test_an_unpriced_model_still_reports_tokens_and_delivers_the_answer():
    """
    `gemini/` carries no fallback: checked directly, `completion_cost` prices
    an unmapped `openai/`-prefixed model at a default $0.0001 rather than
    raising, where an unmapped `gemini/` or `anthropic/` one raises "This
    model isn't mapped yet." `gemini/totally-unknown-model` is picked to reach
    that raise, which `_billed` must swallow without disturbing the reply
    already streamed or the token counts the same reassembled `ModelResponse`
    carries regardless of price.
    """
    client, _ = client_replaying(openai_chunks())
    client.model = "gemini/totally-unknown-model"

    reply = collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "usd" not in client.last_cost
    assert client.last_cost["prompt_tokens"] == 8
    assert reply.text == "I'll add a 10mm box."


def test_last_cost_does_not_carry_the_previous_calls_price_over():
    """
    A cleared `usd` is the same guarantee `ClaudeCodeClient` gives, on the
    keyed route — held over, a call priced at nothing would be billed the
    previous one's number.
    """
    client, _ = client_replaying(anthropic_chunks())
    client.model = "claude-3-7-sonnet-20250219"
    collect(client.stream([{"role": "user", "content": "hi"}]))
    assert "usd" in client.last_cost

    client._completion = lambda **kwargs: iter(openai_chunks())
    client.model = "gemini/totally-unknown-model"
    collect(client.stream([{"role": "user", "content": "hi"}]))

    assert "usd" not in client.last_cost


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


@pytest.mark.parametrize("model,supported", [
    ("anthropic/claude-3-7-sonnet-20250219", True),
    ("openai/kimi-k3", False),
    ("gemini/gemini-2.5-pro", False),
    ("sonnet", False),
    ("scripted", False),
    (None, False),
])
def test_cache_control_is_only_claimed_supported_on_the_route_the_spike_measured(model, supported):
    assert cache_control_supported(model) is supported


@pytest.mark.parametrize("provider,model_id,expected", [
    ("Anthropic", "claude-3-7-sonnet-20250219", "anthropic/claude-3-7-sonnet-20250219"),
    ("OpenAI", "gpt-4o", "openai/gpt-4o"),
    ("Google", "gemini-2.5-pro", "gemini/gemini-2.5-pro"),
    ("Home server", "llama3", "openai/llama3"),
    ("OpenCode Go", "kimi-k3", "openai/kimi-k3"),
    ("OpenRouter", "x-ai/grok-4.1", "openrouter/x-ai/grok-4.1"),
    ("Anthropic", "anthropic/claude-3-opus-20240229", "anthropic/claude-3-opus-20240229"),
    ("Groq", "moonshotai/kimi-k2", "openai/moonshotai/kimi-k2"),
    ("Groq", "openai/gpt-oss-120b", "openai/openai/gpt-oss-120b"),
])
def test_model_names_get_the_provider_prefix_litellm_wants(provider, model_id, expected):
    assert litellm_model_name(provider, model_id) == expected


def test_a_session_has_its_own_id_and_a_fixed_one_can_be_given():
    from core.llm import Session

    a, b = Session(), Session()

    assert a.id != b.id
    assert Session(id="fixed").id == "fixed"


def test_a_sessions_second_turn_sees_the_first_without_the_caller_reassembling():
    from core.llm import Session

    session = Session(system="be terse")
    client = ScriptedLLMClient(["first reply", "second reply"])

    collect(session.turn(client, "first question"))
    collect(session.turn(client, "second question"))

    assert client.calls[1] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first reply"},
        {"role": "user", "content": "second question"},
    ]


def test_a_sessions_turn_still_yields_the_chunks_it_streamed():
    from core.llm import Session

    session = Session()
    client = ScriptedLLMClient([{"reasoning": "hmm", "text": "the answer"}])

    reply = collect(session.turn(client, "a question"))

    assert (reply.reasoning, reply.text) == ("hmm", "the answer")


def test_scripted_client_replays_turns_in_order_and_records_what_it_was_asked():
    client = ScriptedLLMClient(["first", {"reasoning": "hmm", "text": "second"}])

    assert collect(client.stream([{"role": "user", "content": "a"}])).text == "first"
    second = collect(client.stream([{"role": "user", "content": "b"}]))

    assert (second.reasoning, second.text) == ("hmm", "second")
    assert [m[0]["content"] for m in client.calls] == ["a", "b"]


def test_scripted_client_accepts_explicit_chunks():
    client = ScriptedLLMClient([[Chunk(TEXT, "a"), Chunk(TEXT, "b")]])

    assert collect(client.stream([])).text == "ab"


def test_scripted_client_refuses_a_turn_shape_it_does_not_understand():
    """
    A bare list of strings is what an unflattened `_MultiTurn` looks like once
    a script runs dry and the queue lands on the next phase's turn instead —
    silently replaying it as Chunks once turned that mistake into a crash
    three frames away, on whichever chunk's `.kind` was read first.
    """
    client = ScriptedLLMClient(["first", ["not", "a", "turn"]])
    collect(client.stream([]))

    with pytest.raises(TypeError, match=r"call 2 is a list.*not.*str.*dict.*Chunk"):
        collect(client.stream([]))


def test_scripted_client_running_dry_is_a_loud_test_failure():
    client = ScriptedLLMClient([])

    with pytest.raises(AssertionError, match="ran out of turns"):
        client.stream([])


def _an_image(tmp_path, name="bracket.png"):
    picture = tmp_path / name
    picture.write_bytes(b"a file; whether it decodes is the provider's problem")
    return str(picture)


def test_an_image_on_a_model_that_takes_images_is_usable(tmp_path):
    assert unusable_attachments([_an_image(tmp_path)], "openai/gpt-4o") == []


def test_a_model_that_cannot_see_is_named_before_the_run(tmp_path):
    reasons = unusable_attachments([_an_image(tmp_path)], "openai/gpt-3.5-turbo")

    assert len(reasons) == 1
    assert "openai/gpt-3.5-turbo" in reasons[0]
    assert "cannot accept images" in reasons[0]


def test_a_model_litellm_does_not_know_is_given_the_benefit_of_the_doubt(tmp_path):
    """
    A Home server model is absent from LiteLLM's table, and absent is not the
    same as cannot. Guessing 'no' here would refuse to send a picture to a
    local vision model that would have taken it; the post-hoc Refusal covers
    the case we get wrong.
    """
    assert unusable_attachments([_an_image(tmp_path)], "openai/some-local-build") == []


def test_a_file_that_is_not_an_image_or_a_pdf_is_named(tmp_path):
    drawing = tmp_path / "bracket.step"
    drawing.write_text("ISO-10303-21;")

    reasons = unusable_attachments([str(drawing)], "openai/gpt-4o")

    assert len(reasons) == 1
    assert "bracket.step" in reasons[0]
    assert "PNG" in reasons[0]


def _a_pdf(tmp_path, name="bracket.pdf"):
    drawing = tmp_path / name
    drawing.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return str(drawing)


def test_a_pdf_on_a_model_that_reads_pdfs_is_usable(tmp_path):
    """
    Drawings arrive as PDFs, and `IMAGE_SUFFIXES` turned every one of them away
    before the run started while the chip icon dressed them as attachable.
    """
    assert unusable_attachments([_a_pdf(tmp_path)], "anthropic/claude-3-7-sonnet-20250219") \
        == []


def test_a_model_that_cannot_read_a_pdf_is_named_before_the_run(tmp_path):
    """
    The case that is now possible: the model takes images and not PDFs. It has
    to be named before anything is sent, in the same voice as the sentences
    beside it — the provider's own complaint arrives too late. Grok 2
    Vision is such a model: it sees, and it does not read a PDF.
    """
    model = "xai/grok-2-vision-1212"
    reasons = unusable_attachments([_a_pdf(tmp_path)], model)

    assert len(reasons) == 1, reasons
    assert "bracket.pdf" in reasons[0]
    assert model in reasons[0]
    assert "PDF" in reasons[0]


def test_a_pdf_on_a_model_litellm_does_not_know_is_given_the_benefit_of_the_doubt(tmp_path):
    """`accepts_pdf` is the exact twin of `accepts_images`: absent is not a no."""
    assert accepts_pdf("openai/some-local-build") is None
    assert unusable_attachments([_a_pdf(tmp_path)], "openai/some-local-build") == []


def test_a_pdf_alone_is_not_refused_for_a_model_that_cannot_see(tmp_path):
    """A PDF is not an image, so "cannot accept images" is not the complaint."""
    reasons = unusable_attachments([_a_pdf(tmp_path)], "openai/gpt-3.5-turbo")

    assert not any("cannot accept images" in reason for reason in reasons), reasons


def test_a_file_that_is_gone_is_named_rather_than_silently_dropped(tmp_path):
    reasons = unusable_attachments([str(tmp_path / "vanished.png")], "openai/gpt-4o")

    assert len(reasons) == 1
    assert "vanished.png" in reasons[0]


def test_every_unusable_attachment_gets_its_own_reason(tmp_path):
    drawing = tmp_path / "bracket.step"
    drawing.write_text("ISO-10303-21;")

    reasons = unusable_attachments(
        [_an_image(tmp_path), str(drawing), str(tmp_path / "gone.png")],
        "openai/gpt-3.5-turbo")

    assert len(reasons) == 3, reasons


def test_no_attachment_never_complains_about_the_model():
    assert unusable_attachments([], "openai/gpt-3.5-turbo") == []


def test_a_plan_s_usage_limit_does_not_read_as_a_moment_s_rate_limit():
    """
    Measured mid-run on the opencode gateway
    (`logs/sessions/172369d7389a4a4f9c3c2ae8b5aded91.jsonl`): kimi-k3 was
    refused with "Monthly usage limit reached. Resets in 10 days", and the user
    was told they were sending requests too quickly and should wait and send the
    Intent again. Waiting is ten days, and the remedy is somewhere else.
    """
    import litellm

    spent = client_refused_by(litellm.RateLimitError(
        message=("Monthly usage limit reached. Resets in 10 days. To continue using this "
                 "model now, enable usage from your available balance."),
        llm_provider="openai", model="openai/kimi-k3"))

    with pytest.raises(ProviderRefused) as refusal:
        collect(spent.stream([{"role": "user", "content": "hi"}]))

    said = str(refusal.value)
    assert "usage limit" in said
    assert "too many requests too quickly" not in said
    assert "Resets in 10 days" in said, "the provider said when it clears and the user was not told"
