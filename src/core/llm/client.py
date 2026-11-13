"""
LiteLLM client: streams from any provider LiteLLM supports.

`last_cost` follows `ClaudeCodeClient`'s own contract: `None` in
`__init__`, cleared at the top of every `stream()`, and set only for the
call that produced it — the same reason applies here, held over a call
that ends without a price would be billed the previous call's number.
"""
from typing import Callable, Iterator

from core.llm.types import REASONING, Chunk
from core.llm.exceptions import ProviderRefused
from core.llm.effort_policy import _OUTPUT_BUDGET, _SUBSCRIPTION_EFFORT
from core.llm.stream import _deltas, _finished, _stopped_for_length, _cut_off, _truncated, _translate, _billed


class LiteLLMClient:
    """
    Streams from any provider LiteLLM supports.

    `last_cost` follows `ClaudeCodeClient`'s own contract: `None` in
    `__init__`, cleared at the top of every `stream()`, and set only for the
    call that produced it — the same reason applies here, held over a call
    that ends without a price would be billed the previous call's number.
    """

    def __init__(self, model: str, api_key: str, api_base: str = None,
                 thinking_budget: int = None, completion: Callable = None,
                 effort: str = None, extra_headers: dict = None, output_ceiling: int = None):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.thinking_budget = thinking_budget
        self.effort = effort
        self.extra_headers = extra_headers
        self.output_ceiling = output_ceiling
        self.last_cost = None
        self._completion = completion

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        """
        Stream a reply to *messages*, chunk by chunk. The reasoning effort
        is sent with `allowed_openai_params`, because LiteLLM refuses to
        send a param it has no table entry for and a gateway's model is
        exactly what it has no entry for; usage is asked for on an
        OpenAI-compatible endpoint, because a gateway not asked can end a
        stream without a word about why and a cut-off reply then looks
        complete. `extra_headers` is what a gateway routes a call on
        rather than reads off the body, since OpenCode Go answers a call
        without `x-opencode-session` with 400 for every model it sells,
        and every raw chunk is kept on top of what the caller accumulates
        so `_priced` can reassemble them once the stream ends cleanly.
        """
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
        if self.extra_headers:
            kwargs.setdefault("extra_headers", dict(self.extra_headers))
        if self.thinking_budget:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        elif _may_reason(self.model):
            kwargs.setdefault("reasoning_effort", self.effort or _SUBSCRIPTION_EFFORT)
            kwargs.setdefault("allowed_openai_params", ["reasoning_effort"])
        kwargs.setdefault("max_tokens",
                          _output_cap(self.model, (self.thinking_budget or _OUTPUT_BUDGET) * 4,
                                      self.output_ceiling))
        openai_compatible = self.model.startswith(("openai/", "openrouter/"))
        if openai_compatible:
            kwargs.setdefault("stream_options", {"include_usage": True})

        self.last_cost = None
        for attempt in range(_ATTEMPTS):
            chunks = []
            finished = False
            cap_hit = False
            spoke = False
            try:
                for raw in completion(**kwargs):
                    chunks.append(raw)
                    for chunk in _deltas(raw):
                        spoke = True
                        yield chunk
                    cap_hit = cap_hit or _stopped_for_length(raw)
                    finished = finished or _finished(raw)
                break
            except ProviderRefused:
                raise
            except Exception as exc:
                if not spoke and attempt < _ATTEMPTS - 1 and _worth_another_try(exc):
                    yield Chunk(REASONING, "\n%s did not answer in time. Trying again (%d of %d).\n"
                                % (self.model, attempt + 2, _ATTEMPTS))
                    continue
                refusal = _translate(exc, self.model)
                if refusal is None:
                    raise
                raise refusal from exc

        if cap_hit:
            self.last_cost = _priced(self.model, chunks, messages) or \
                {"model": self.model, "usage_unavailable": True}
            raise _truncated(self.model, kwargs.get("max_tokens"))

        if openai_compatible and not finished:
            raise _cut_off(self.model)

        self.last_cost = _priced(self.model, chunks, messages)


def _may_reason(model) -> bool:
    """
    Whether to tell *model* how hard to think.

    A model LiteLLM knows answers for itself. One it has never heard of is a
    gateway's — kimi-k3 is — and those are the ones that need telling; a local
    server that does not reason ignores the key rather than refusing it.
    """
    import litellm

    if litellm.supports_reasoning(model):
        return True
    try:
        litellm.get_model_info(model)
    except Exception:
        return True
    return False


def _output_cap(model, wanted, listed=None) -> int:
    """
    *wanted*, or the model's own ceiling where one is known: asking for more
    output than a model can write is rejected outright. The ceiling is
    *listed*, the one the provider's own model list names, or else LiteLLM's,
    since a gateway model LiteLLM has never heard of has no ceiling to read.
    """
    import litellm

    known = listed
    if not known:
        try:
            known = litellm.get_model_info(model).get("max_output_tokens")
        except Exception:
            known = None
    return min(wanted, known) if known else wanted


def _priced(model, chunks, messages):
    """*model*'s cost and token counts off *chunks*, or None where the stream carried neither."""
    cost, tokens = _billed(model, chunks, messages)
    if cost is None and tokens is None:
        return None
    priced = {"model": model}
    if cost is not None:
        priced["usd"] = cost
    if tokens is not None:
        priced.update(tokens)
    return priced


_ATTEMPTS = 3


def _worth_another_try(exc) -> bool:
    """
    Whether the call is worth making again rather than refusing.

    A gateway that gave up waiting on the model behind it says so as a timeout
    or as `ServiceUnavailableError` — OpenRouter answered a cloaked model with
    `Provider timed out after 311814ms` — and the next call often lands; a
    rejected key or a spent quota says the same thing however many times it is
    asked.
    """
    import litellm

    return isinstance(exc, (litellm.Timeout, litellm.ServiceUnavailableError))
