"""
The LLM client seam.

One interface, two implementations: LiteLLM against a real provider, and a
scripted client that replays canned turns. Everything above this line is
testable without a network or an API key.

ADR-0004 — LiteLLM is the single unified client. `tools/spike_litellm.py`
holds the evidence that it carries Anthropic thinking blocks through the
stream and honours `cache_control`.

This module must stay importable without FreeCAD; the settings lookup that
needs it is deferred into `client_from_settings`.
"""
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, List

REASONING = "reasoning"
TEXT = "text"

# LiteLLM addresses a model as "<prefix>/<id>". These are the providers
# config/models.json knows about.
_PREFIX = {
    "OpenAI": "openai",
    "Google": "gemini",
    "Anthropic": "anthropic",
    "Custom/Local": "openai",
}


class ProviderRefused(Exception):
    """
    The provider would not answer: a limit, a rejected key, a dropped
    connection, a reply cut off before it finished.

    Carries a sentence written for the user — which model, which limit, what to
    do about it — and `said`, what the provider itself replied, verbatim.
    """

    def __init__(self, message: str, said: str = ""):
        super().__init__(message)
        self.said = said


@dataclass(frozen=True)
class Chunk:
    """One piece of a streamed reply. `kind` is REASONING or TEXT."""
    kind: str
    text: str


class LiteLLMClient:
    """Streams from any provider LiteLLM supports."""

    def __init__(self, model: str, api_key: str, api_base: str = None,
                 thinking_budget: int = None, completion: Callable = None):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.thinking_budget = thinking_budget
        self._completion = completion

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        completion = self._completion
        if completion is None:
            import litellm
            completion = litellm.completion

        kwargs = dict(
            model=self.model,
            messages=messages,
            stream=True,
            api_key=self.api_key,
            **overrides,
        )
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.thinking_budget:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
            kwargs.setdefault("max_tokens", self.thinking_budget * 4)

        try:
            for raw in completion(**kwargs):
                for chunk in _deltas(raw):
                    yield chunk
                if _stopped_for_length(raw):
                    raise _truncated(self.model, kwargs.get("max_tokens"))
        except ProviderRefused:
            raise
        except Exception as exc:
            refusal = _translate(exc, self.model)
            if refusal is None:
                raise
            raise refusal from exc


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


def _truncated(model, cap) -> ProviderRefused:
    """The cap is `budget * 4` above and invisible from outside; the message names it."""
    where = "at the %d-token output cap" % cap if cap else "at the provider's default output cap"
    return ProviderRefused(
        "%s ran out of output tokens: the reply was cut off %s before it finished. "
        "Raise the cap or ask for a smaller Step." % (model, where),
        "finish_reason: length",
    )


# LiteLLM's taxonomy translated once, here, so no phase has to guess what a
# provider error means. Google's 429 for an exhausted quota and OpenAI's for
# too many requests are the same class; only the body tells them apart.
_RETRY_AFTER = re.compile(
    r"retry[-_ ]?(?:delay|after)\"?\s*[:=]?\s*\"?(\d+(?:\.\d+)?\s*[smh]?)", re.I)
_OUT_OF_CREDIT = ("insufficient_quota", "credit balance", "out of credit", "billing_hard_limit")
_OUT_OF_QUOTA = ("quota", "resource_exhausted", "resource has been exhausted")
# Google answers a per-minute limit with the same RESOURCE_EXHAUSTED body as a
# spent daily quota; only the violated metric says whether the wait is seconds
# or a day.
_PER_MINUTE = ("perminute", "per minute", "per min")
# A text-only model handed an attachment. Narrow on purpose: every other
# BadRequestError is a bug in what we sent, and hiding it as a Refusal would
# tell the user to switch models over our own mistake.
_NO_IMAGES = ("image_url", "image input", "does not support image", "image content")


def _translate(exc, model):
    """A LiteLLM exception as a sentence the user can act on, or None if it is not one."""
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
        if any(word in low for word in _OUT_OF_QUOTA) and not any(
                word in low for word in _PER_MINUTE):
            return ProviderRefused(
                refused + ": your quota for this model is exhausted.%s Wait for the quota "
                "window to reset, raise the quota with your provider, or run this on another "
                "model." % _retry_hint(said), said)
        return ProviderRefused(
            refused + ": you are being rate limited — too many requests too quickly.%s Wait "
            "and send the Intent again." % _retry_hint(said), said)
    if isinstance(exc, litellm.Timeout):
        return ProviderRefused(
            refused + ": the provider did not answer in time. Try again, or pick a faster "
            "model.", said)
    if isinstance(exc, litellm.APIConnectionError):
        return ProviderRefused(
            "Could not reach %s: the connection failed. This is a transport failure, not a "
            "limit on your account — check the endpoint is running and reachable." % model, said)
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


class ScriptedLLMClient:
    """
    The test double for the seam. Hand it the turns you want back, in order,
    and it records the messages it was asked with.

        client = ScriptedLLMClient(["a plan", {"reasoning": "hmm", "text": "code"}])
    """

    def __init__(self, turns: Iterable = ()):
        self.turns: List = list(turns)
        self.calls: List[list] = []

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        self.calls.append(messages)
        if not self.turns:
            raise AssertionError(
                "ScriptedLLMClient ran out of turns on call %d" % len(self.calls)
            )
        return _replay(self.turns.pop(0))


def _replay(turn) -> Iterator[Chunk]:
    """An Exception anywhere in a turn is raised there — a stream can die partway."""
    if isinstance(turn, Exception):
        raise turn
    for chunk in _as_chunks(turn):
        if isinstance(chunk, Exception):
            raise chunk
        yield chunk


def _as_chunks(turn) -> List[Chunk]:
    if isinstance(turn, str):
        return [Chunk(TEXT, turn)]
    if isinstance(turn, dict):
        chunks = []
        if turn.get("reasoning"):
            chunks.append(Chunk(REASONING, turn["reasoning"]))
        if turn.get("text"):
            chunks.append(Chunk(TEXT, turn["text"]))
        return chunks
    return list(turn)


def litellm_model_name(provider: str, model_id: str) -> str:
    """'Anthropic', 'claude-3-7-sonnet-20250219' -> 'anthropic/claude-3-7-...'."""
    prefix = _PREFIX.get(provider)
    if prefix is None:
        # An unknown provider the user typed in themselves; assume it is already
        # a name LiteLLM understands.
        return model_id
    if model_id.startswith(prefix + "/"):
        return model_id
    return prefix + "/" + model_id


def client_from_settings(model_id: str = None) -> LiteLLMClient:
    """Build a client from the provider settings page and stored keys."""
    from utils import settings_manager

    provider, mid, secret = settings_manager.instance().get_llm_credentials(model_id)

    api_base = None
    api_key = secret
    # The Custom/Local page stores the endpoint where other providers store a key.
    if secret.startswith("http://") or secret.startswith("https://"):
        api_base = secret.rstrip("/")
        api_key = "not-needed"

    return LiteLLMClient(
        model=litellm_model_name(provider, mid),
        api_key=api_key,
        api_base=api_base,
        thinking_budget=2048 if provider == "Anthropic" else None,
    )
