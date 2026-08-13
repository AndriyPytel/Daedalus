#!/usr/bin/env python
"""
ADR-0004 spike: does LiteLLM carry Anthropic thinking blocks through the stream,
and does it honour cache_control?

Both are load-bearing — the panel renders reasoning as it forms, and every Step
resends Document state. If either answer is no, ADR-0004 is revised to native
SDKs plus a thin adapter.

    python tools/spike_litellm.py            # offline: drives LiteLLM's own code
    python tools/spike_litellm.py --live     # one real Anthropic call, needs a key

Offline mode is not a mock of LiteLLM — it feeds Anthropic's real SSE event
shapes through LiteLLM's real Anthropic parser and its real request transform,
and asserts on what comes out. Live mode is the confirmation that the wire
agrees with the code.

``RECORDED_THINKING_STREAM`` is Anthropic's documented extended-thinking
stream, verbatim event shapes.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RECORDED_THINKING_STREAM = [
    {"type": "message_start", "message": {"id": "msg_spike", "type": "message", "role": "assistant",
                                          "model": "claude-3-7-sonnet-20250219", "content": [],
                                          "stop_reason": None,
                                          "usage": {"input_tokens": 42, "output_tokens": 1,
                                                    "cache_creation_input_tokens": 1024,
                                                    "cache_read_input_tokens": 0}}},
    {"type": "content_block_start", "index": 0,
     "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "thinking_delta", "thinking": "The user wants a 10mm cube. "}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "thinking_delta", "thinking": "Part::Box is the primitive for that."}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "signature_delta", "signature": "EqQBCgIYAhIM1gbc"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "text_delta", "text": "I'll add a 10mm box."}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
     "usage": {"output_tokens": 37}},
    {"type": "message_stop"},
]

CACHED_SYSTEM = "You drive FreeCAD 1.0. " + ("Document state follows. " * 400)


def check_thinking_survives_the_stream():
    """LiteLLM's Anthropic stream parser must surface thinking as reasoning_content."""
    from litellm.llms.anthropic.chat.handler import ModelResponseIterator

    it = ModelResponseIterator(streaming_response=None, sync_stream=True)

    reasoning, text, signatures = [], [], []
    for event in RECORDED_THINKING_STREAM:
        parsed = it.chunk_parser(event)
        for choice in parsed.choices:
            delta = choice.delta
            if getattr(delta, "reasoning_content", None):
                reasoning.append(delta.reasoning_content)
            if getattr(delta, "content", None):
                text.append(delta.content)
            for block in getattr(delta, "thinking_blocks", None) or []:
                if block.get("signature"):
                    signatures.append(block["signature"])

    reasoning = "".join(reasoning)
    text = "".join(text)

    assert "Part::Box is the primitive" in reasoning, f"reasoning lost: {reasoning!r}"
    assert text == "I'll add a 10mm box.", f"text lost: {text!r}"
    assert signatures, "thinking signature dropped — multi-turn thinking would break"

    print("  reasoning_content : " + repr(reasoning))
    print("  content           : " + repr(text))
    print("  signature         : " + repr(signatures[0]))
    return True


def check_cache_control_reaches_the_wire():
    """cache_control on a message must survive into the body LiteLLM sends."""
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    messages = [
        {"role": "system", "content": [
            {"type": "text", "text": CACHED_SYSTEM,
             "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": "Add a 10mm cube."},
    ]

    body = AnthropicConfig().transform_request(
        model="claude-3-7-sonnet-20250219",
        messages=messages,
        optional_params={},
        litellm_params={},
        headers={},
    )

    marked = [b for b in body.get("system", []) if b.get("cache_control")]
    assert marked, "cache_control stripped from the system block:\n" + json.dumps(body, indent=2)[:2000]

    headers = AnthropicConfig().validate_environment(
        headers={}, model="claude-3-7-sonnet-20250219", messages=messages,
        optional_params={}, litellm_params={}, api_key="sk-spike",
    )

    print("  system blocks marked ephemeral : " + str(len(marked)))
    print("  cached block length            : " + str(len(marked[0]["text"])) + " chars")
    print("  anthropic-beta header          : " + repr(headers.get("anthropic-beta", "")))
    return True


def live():
    """One real call: thinking on, a big cached system prompt, sent twice."""
    import litellm

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — nothing to send.")
        return False

    model = os.environ.get("SPIKE_MODEL", "anthropic/claude-3-7-sonnet-20250219")
    messages = [
        {"role": "system", "content": [
            {"type": "text", "text": CACHED_SYSTEM, "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": "Name the FreeCAD 1.0 primitive for a box. One word."},
    ]

    for turn in (1, 2):
        reasoning, text, usage = [], [], None
        stream = litellm.completion(
            model=model, messages=messages, stream=True, stream_options={"include_usage": True},
            thinking={"type": "enabled", "budget_tokens": 1024}, max_tokens=2048,
        )
        for chunk in stream:
            for choice in chunk.choices:
                if getattr(choice.delta, "reasoning_content", None):
                    reasoning.append(choice.delta.reasoning_content)
                if getattr(choice.delta, "content", None):
                    text.append(choice.delta.content)
            if getattr(chunk, "usage", None):
                usage = chunk.usage

        print("turn " + str(turn))
        print("  reasoning chars : " + str(len("".join(reasoning))))
        print("  text            : " + repr("".join(text))[:120])
        if usage:
            details = getattr(usage, "prompt_tokens_details", None)
            print("  cache write     : " + str(getattr(usage, "cache_creation_input_tokens", None)))
            print("  cache read      : " + str(getattr(details, "cached_tokens", None) if details else None))

    return True


def main():
    if "--live" in sys.argv:
        return 0 if live() else 1

    print("ADR-0004 spike — LiteLLM " + __import__("importlib.metadata", fromlist=["x"]).version("litellm"))
    print()
    print("1. Anthropic thinking blocks through the stream")
    ok_thinking = check_thinking_survives_the_stream()
    print()
    print("2. cache_control honoured")
    ok_cache = check_cache_control_reaches_the_wire()
    print()
    verdict = "CONFIRMED" if (ok_thinking and ok_cache) else "FAILED"
    print("ADR-0004: " + verdict)
    return 0 if verdict == "CONFIRMED" else 1


if __name__ == "__main__":
    sys.exit(main())
