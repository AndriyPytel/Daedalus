"""
The `claude` CLI's own stream-json events, in and out: what one call sends
as its user turn, and what one line of the reply carries -- a chunk of
text, a withheld-thinking marker, a Refusal, or the token counts at the end.
Split out of `core/llm/claude.py` so the CLI process wrapper and the shape
of the wire it speaks are each one read on their own.
"""
import json
import time
from typing import Iterator

from core.llm.types import REASONING, TEXT, Chunk
from core.llm.exceptions import ProviderRefused


def _system_of(messages: list) -> str:
    """Every system message, as the prompt `--system-prompt-file` replaces the CLI's with."""
    return "\n\n".join(_as_text(message.get("content"))
                       for message in messages if message.get("role") == "system")


def _as_text(content) -> str:
    """A message's text, whether it travels as a string or as blocks."""
    if isinstance(content, str):
        return content
    return "\n\n".join(piece.get("text", "") for piece in content or ()
                       if piece.get("type") == "text")


_CACHE_BREAKPOINT = {"type": "ephemeral", "ttl": "1h"}


def _user_turn(messages: list) -> dict:
    """
    *messages* as the one message stream-json input takes, with one explicit
    cache breakpoint on its last block. Each assistant reply rides along as a
    marked text block, because stream-json input takes user turns only.
    """
    content = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            content.extend(_blocks(message.get("content")))
        elif role == "assistant":
            content.append(_reply_block(message.get("content")))
    if content:
        content[-1] = dict(content[-1], cache_control=_CACHE_BREAKPOINT)
    return {"type": "user", "message": {"role": "user", "content": content}}


def _reply_block(content) -> dict:
    """One earlier assistant reply, marked so the model reads it as its own rather than the user's."""
    return {"type": "text", "text": "Your reply:\n" + _as_text(content)}


def _blocks(content) -> list:
    """
    One message as the content blocks Anthropic takes.

    `core/loop.py :: _as_image` builds LiteLLM's envelopes — an `image_url` and
    a `file`, each carrying a `data:` URI — because that is what a keyed
    provider goes through. This route speaks Anthropic's own shapes, so the
    envelope is translated here rather than the file being read a second time.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]

    blocks = []
    for piece in content or ():
        kind = piece.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": piece.get("text", "")})
        elif kind == "image_url":
            blocks.append(_attachment("image", piece["image_url"]["url"]))
        elif kind == "file":
            blocks.append(_attachment("document", piece["file"]["file_data"]))
    return blocks


def _attachment(kind: str, uri: str) -> dict:
    """A `data:` URI as a base64 source block, carrying the media type it names."""
    head, _, payload = (uri or "").partition(",")
    return {"type": kind,
            "source": {"type": "base64",
                       "media_type": head[len("data:"):].split(";")[0],
                       "data": payload}}


def _parsed(line: str):
    """One line of the stream as the event it carries, or None where it carries none."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _withheld_thinking(event: dict) -> bool:
    """
    Whether *event* is thinking the provider is doing but not showing.

    `thinking_delta` carrying no words and a token count: `sonnet` and `opus`
    on this route reason that way for minutes at a time, and a panel told
    nothing at all cannot tell that from a call that has hung.
    """
    if event.get("type") != "stream_event":
        return False
    delta = ((event.get("event") or {}).get("delta") or {})
    return (delta.get("type") == "thinking_delta"
            and not delta.get("thinking")
            and bool(delta.get("estimated_tokens")))


def _said(event: dict) -> Iterator[Chunk]:
    """
    The text one event carries, deltas only: the `assistant` event repeats
    the whole reply once complete, and taking both would send every phase
    its answer twice, while `signature_delta` is dropped since it signs a
    thinking block for a later turn and every call here is a turn of its
    own. Thinking that arrives without its words yields nothing: `sonnet`
    and `opus` stream `thinking_delta` carrying `""` and a rising
    `estimated_tokens` with the words never coming, while `haiku` streams
    the words through this same branch, and reporting a count instead once
    filled a 45-minute session's transcript with 1506 lines of
    `thinking… ~N tokens` that said how much was spent and nothing about
    why. Where the words are withheld the panel says the phase is running
    and stays quiet about the rest.
    """
    if event.get("type") != "stream_event":
        return
    streamed = event.get("event") or {}
    if streamed.get("type") != "content_block_delta":
        return
    delta = streamed.get("delta") or {}
    if delta.get("type") == "thinking_delta" and delta.get("thinking"):
        yield Chunk(REASONING, delta["thinking"])
    if delta.get("type") == "text_delta" and delta.get("text"):
        yield Chunk(TEXT, delta["text"])


def _refusal_in(event: dict, model: str):
    """
    The Refusal an event carries, or None.

    This route sees none of LiteLLM's exception classes, so `_translate` is no
    use to it: the material is the stream's own `rate_limit_event` and the
    `result` event's `stop_reason` and `subtype`. Every `allowed*` status is a
    window still open -- `allowed_warning` is one filling up, not one spent.
    """
    if event.get("type") == "rate_limit_event":
        limit = event.get("rate_limit_info") or {}
        if str(limit.get("status") or "").startswith("allowed"):
            return None
        return ProviderRefused(
            "%s refused the request: the subscription's %s limit is spent, and it "
            "resets at %s. Wait for it, or run this Intent on a provider with a key."
            % (model, (limit.get("rateLimitType") or "usage").replace("_", "-"),
               _clock(limit.get("resetsAt"))),
            json.dumps(limit))

    if event.get("type") != "result":
        return None

    if event.get("stop_reason") == "max_tokens":
        return ProviderRefused(
            "%s ran out of output tokens: the reply was cut off before it finished. "
            "Ask for a smaller Step." % model,
            "stop_reason: max_tokens")

    if event.get("is_error") or event.get("subtype") != "success":
        return ProviderRefused(
            "%s refused the request: the claude CLI ended the call as '%s'."
            % (model, event.get("subtype")),
            str(event.get("result") or "")[:400])

    return None


def _clock(epoch) -> str:
    """
    `resetsAt` as a time of day.

    It arrives as an epoch, which is strictly more than
    `core/llm.py :: _retry_hint` scrapes out of a sentence with a regex, and
    strictly less use to the user until it is a clock.
    """
    try:
        return time.strftime("%H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return "the top of the next window"


def _tokens_of(usage: dict) -> dict:
    """
    Prompt, completion and cached-prompt token counts off a `result` event's
    own `usage` -- `input_tokens` and `output_tokens` on this route, where the
    keyed one reads `prompt_tokens`/`completion_tokens` off LiteLLM's `Usage`.
    `cache_read_input_tokens` is the preamble a cache hit spared the model and
    `cache_creation_input_tokens` is the preamble this call paid to write for
    a later one to read; both are omitted rather than zero where nothing was
    read or written.
    """
    tokens = {}
    if usage.get("input_tokens") is not None:
        tokens["prompt_tokens"] = usage["input_tokens"]
    if usage.get("output_tokens") is not None:
        tokens["completion_tokens"] = usage["output_tokens"]
    cached = usage.get("cache_read_input_tokens")
    if cached:
        tokens["cached_tokens"] = cached
    written = usage.get("cache_creation_input_tokens")
    if written:
        tokens["cache_creation_tokens"] = written
    return tokens
