"""
LiteLLM client: streams from any provider LiteLLM supports.

`last_cost` follows `ClaudeCodeClient`'s own contract: `None` in
`__init__`, cleared at the top of every `stream()`, and set only for the
call that produced it — the same reason applies here, held over a call
that ends without a price would be billed the previous call's number.
"""
from typing import Callable, Iterator

from core.llm.types import Chunk
from core.llm.exceptions import ProviderRefused
from core.llm.effort_policy import _OUTPUT_BUDGET, _resolved_effort
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
                 effort: str = None, extra_headers: dict = None):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.thinking_budget = thinking_budget
        self.effort = effort
        self.extra_headers = extra_headers
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
            kwargs.setdefault("reasoning_effort", _resolved_effort(self.effort))
            kwargs.setdefault("allowed_openai_params", ["reasoning_effort"])
        kwargs.setdefault("max_tokens",
                          _output_cap(self.model, (self.thinking_budget or _OUTPUT_BUDGET) * 4))
        openai_compatible = self.model.startswith("openai/")
        if openai_compatible:
            kwargs.setdefault("stream_options", {"include_usage": True})

        self.last_cost = None
        chunks = []
        finished = False
        try:
            for raw in completion(**kwargs):
                chunks.append(raw)
                for chunk in _deltas(raw):
                    yield chunk
                if _stopped_for_length(raw):
                    raise _truncated(self.model, kwargs.get("max_tokens"))
                finished = finished or _finished(raw)
        except ProviderRefused:
            raise
        except Exception as exc:
            refusal = _translate(exc, self.model)
            if refusal is None:
                raise
            raise refusal from exc

        if openai_compatible and not finished:
            raise _cut_off(self.model)

        cost, tokens = _billed(self.model, chunks, messages)
        if cost is not None or tokens is not None:
            self.last_cost = {"model": self.model}
            if cost is not None:
                self.last_cost["usd"] = cost
            if tokens is not None:
                self.last_cost.update(tokens)


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


def _output_cap(model, wanted) -> int:
    """
    *wanted*, or the model's own ceiling where LiteLLM knows one: asking for
    more output than a model can write is rejected outright, and a gateway
    model LiteLLM has never heard of has no ceiling to read.
    """
    import litellm

    try:
        known = litellm.get_model_info(model).get("max_output_tokens")
    except Exception:
        known = None
    return min(wanted, known) if known else wanted
