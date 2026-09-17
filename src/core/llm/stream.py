"""Stream parsing for LiteLLM responses."""
import re
from typing import Iterator

from core.llm.types import REASONING, TEXT, Chunk
from core.llm.exceptions import ProviderRefused, StreamCutOff


def _deltas(raw) -> Iterator[Chunk]:
    """Map one LiteLLM stream chunk onto our Chunks, provider-agnostically."""
    for choice in getattr(raw, "choices", None) or []:
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield Chunk(REASONING, reasoning)
        text = getattr(delta, "content", None)
        if text:
            yield Chunk(TEXT, text)


def _stopped_for_length(raw) -> bool:
    """The provider marking a reply as cut off. Dropped, this looks like a bad answer."""
    return any(getattr(choice, "finish_reason", None) == "length"
               for choice in getattr(raw, "choices", None) or [])


def _finished(raw) -> bool:
    """
    The terminal chunk an OpenAI-compatible endpoint owes every stream. Its
    absence over a whole stream is the connection dropped, not a short answer.
    """
    return any(getattr(choice, "finish_reason", None)
               for choice in getattr(raw, "choices", None) or [])


def _cut_off(model) -> StreamCutOff:
    return StreamCutOff(
        "%s closed the stream before it answered — no end marker, no reason given. "
        "This is the connection rather than the model; sending the Intent again "
        "usually gets through, and another provider gets through more often." % model,
        "the stream ended with no finish_reason",
    )


def _truncated(model, cap) -> ProviderRefused:
    """The cap is `budget * 4` above and invisible from outside; the message names it."""
    where = "at the %d-token output cap" % cap if cap else "at the provider's default output cap"
    return ProviderRefused(
        "%s ran out of output tokens: the reply was cut off %s before it finished. "
        "Raise the cap or ask for a smaller Step." % (model, where),
        "finish_reason: length",
    )


_RETRY_AFTER = re.compile(
    r"retry[-_ ]?(?:delay|after)\"?\s*[:=]?\s*\"?(\d+(?:\.\d+)?\s*[smh]?)", re.I)
_OUT_OF_CREDIT = ("insufficient_quota", "credit balance", "out of credit",
                  "billing_hard_limit", "requires more credits")
_OUT_OF_QUOTA = ("quota", "resource_exhausted", "resource has been exhausted")
_USED_UP = ("usage limit", "monthly limit", "limit reached", "spend limit",
            "spending limit")
_PER_MINUTE = ("perminute", "per minute", "per min")
_NO_IMAGES = ("image_url", "image input", "does not support image", "image content")


def _translate(exc, model):
    """
    A LiteLLM exception as a sentence the user can act on, or None if it is not one.

    A plan's own ceiling is not a rate, though a gateway answers it with a 429
    too, and telling the user to wait a moment is telling them to wait ten
    days: the opencode gateway refused kimi-k3 mid-run with "Monthly usage
    limit reached. Resets in 10 days." Google answers a per-minute limit with
    the same RESOURCE_EXHAUSTED body as a spent daily quota, so only the
    violated metric says whether the wait is seconds or a day.
    """
    import litellm

    said = str(getattr(exc, "message", "") or exc)
    status = getattr(exc, "status_code", None)
    if status:
        said = "HTTP %s %s" % (status, said)
    low = said.lower()
    refused = "%s refused the request" % model

    if isinstance(exc, litellm.ContextWindowExceededError):
        return ProviderRefused(
            refused + ": the request is longer than the model's context window. Break the "
            "Intent into smaller pieces, or pick a model with more room.", said)
    if isinstance(exc, litellm.AuthenticationError):
        return ProviderRefused(
            refused + ": the API key was rejected. Check the key for this provider on the "
            "Providers page.", said)
    if isinstance(exc, (litellm.RateLimitError, litellm.BudgetExceededError)):
        if any(word in low for word in _OUT_OF_CREDIT):
            return ProviderRefused(
                refused + ": the account is out of credit. Top it up in your provider's "
                "billing dashboard.", said)
        if any(word in low for word in _USED_UP):
            return ProviderRefused(
                refused + ": the plan's usage limit for this model is reached, and waiting "
                "a moment will not clear it. The provider said: %s" % said, said)
        if any(word in low for word in _OUT_OF_QUOTA) and not any(
                word in low for word in _PER_MINUTE):
            return ProviderRefused(
                refused + ": your quota for this model is exhausted.%s Wait for the quota "
                "window to reset, raise the quota with your provider, or run this on another "
                "model." % _retry_hint(said), said)
        return ProviderRefused(
            refused + ": you are being rate limited — too many requests too quickly.%s Wait "
            "and send the Intent again." % _retry_hint(said), said)
    if isinstance(exc, (litellm.Timeout, litellm.ServiceUnavailableError)):
        return ProviderRefused(
            refused + ": the provider did not answer in time. Try again, or pick a faster "
            "model.", said)
    if isinstance(exc, litellm.APIConnectionError):
        return ProviderRefused(
            "Could not reach %s: the connection failed. This is a transport failure, not a "
            "limit on your account — check the endpoint is running and reachable." % model, said)
    if any(word in low for word in _OUT_OF_CREDIT):
        return ProviderRefused(
            refused + ": the account cannot afford this call. Top the account up, or pick a "
            "model with a smaller output cap. The provider said: %s" % said, said)
    if isinstance(exc, litellm.BadRequestError) and any(word in low for word in _NO_IMAGES):
        return ProviderRefused(
            refused + ": this model cannot accept images. Send the Intent without the "
            "attachment, or pick a model with image support.", said)
    return None


def _retry_hint(said):
    """The reset time, when the provider named one — the difference between waiting and giving up."""
    found = _RETRY_AFTER.search(said)
    if not found:
        return ""
    return " The provider says to retry in %s." % found.group(1).strip()


def _billed(model, chunks, messages):
    """
    What the raw stream *chunks* would have cost against *model*'s price
    table, and the prompt/completion/cached-prompt token counts, or (None, None).
    """
    import litellm

    try:
        built = litellm.stream_chunk_builder(chunks, messages=messages)
    except Exception:
        return None, None

    usage = getattr(built, "usage", None)
    tokens = None
    if usage is not None:
        tokens = {"prompt_tokens": usage.prompt_tokens,
                  "completion_tokens": usage.completion_tokens}
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
        if cached:
            tokens["cached_tokens"] = cached
        reasoning = getattr(getattr(usage, "completion_tokens_details", None),
                            "reasoning_tokens", None)
        if reasoning:
            tokens["reasoning_tokens"] = reasoning

    try:
        cost = litellm.completion_cost(completion_response=built, model=model)
    except Exception:
        cost = None
    return cost, tokens
