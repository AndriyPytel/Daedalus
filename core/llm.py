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


@dataclass(frozen=True)
class Chunk:
    """One piece of a streamed reply. `kind` is REASONING or TEXT."""
    kind: str
    text: str


@dataclass
class Reply:
    """A whole streamed reply, accumulated."""
    text: str = ""
    reasoning: str = ""


def collect(chunks: Iterable[Chunk]) -> Reply:
    reply = Reply()
    for chunk in chunks:
        if chunk.kind == REASONING:
            reply.reasoning += chunk.text
        else:
            reply.text += chunk.text
    return reply


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

        for raw in completion(**kwargs):
            for chunk in _deltas(raw):
                yield chunk


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
        return iter(_as_chunks(self.turns.pop(0)))


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
    from utils.settings_manager import SettingsManager

    provider, mid, secret = SettingsManager.instance().get_llm_credentials(model_id)

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
