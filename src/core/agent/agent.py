"""
One model turn, read one way only -- the fenced block if present, else the whole
text, then `json.loads`. `_feedback` carries `volume` on both branches, because
what a feature moved is the number a failed Step's own assert was about.
"""
import json
import time

from core.events import CALL_STARTED, COST, INVOKE_MARKUP, THOUGHT, WRITING
from core.llm import REASONING, TEXT
from core.llm.effort_policy import STALL_LIMIT, STALL_WAIT
from core.llm.exceptions import CallStalled, StreamCutOff
from core.agent.invoke_guard import first_invoke_in, recovered_message
from core.loop.prompt import _CUT_OFF_ATTEMPTS, _object_of
from core.prompts import read_prompt


_STALL_ATTEMPTS = 3


def _say(session, llm, content, phase=None, stream=None):
    """
    One turn, sent again where the stream was cut off or the provider went silent.

    A stalled call is reissued at once, a second one in a row waits `STALL_WAIT` first, and a third fails the turn.
    """
    cut = stalled = 0
    while True:
        try:
            return _one_turn(session, llm, content, phase, stream)
        except StreamCutOff:
            cut += 1
            if cut == _CUT_OFF_ATTEMPTS:
                raise
            session.messages.pop()
            time.sleep(2 ** (cut - 1))
        except CallStalled:
            stalled += 1
            if stalled == _STALL_ATTEMPTS:
                raise
            session.messages.pop()
            if stalled == 2:
                time.sleep(STALL_WAIT)


def _one_turn(session, llm, content, phase=None, stream=None):
    llm = llm.for_phase(phase) if hasattr(llm, "for_phase") else llm
    model = getattr(llm, "model", None)
    ceiling = getattr(llm, "timeout", None)
    if hasattr(llm, "stall"):
        llm.stall = None if any(m["role"] == "assistant" for m in session.messages) else STALL_LIMIT

    if stream is not None:
        started = {"phase": phase, "model": model}
        if ceiling is not None:
            started["ceiling"] = ceiling
        effort = getattr(llm, "effort", None)
        if effort is not None:
            started["effort"] = effort
        stream.emit(CALL_STARTED, **started)

    start = time.time()
    text = []
    invoke = None
    turn = session.turn(llm, content)
    try:
        for chunk in turn:
            if chunk.kind == REASONING:
                if stream is not None:
                    stream.emit(THOUGHT, phase=phase, text=chunk.text)
            elif chunk.kind == TEXT:
                text.append(chunk.text)
                if stream is not None:
                    stream.emit(WRITING, phase=phase, text=chunk.text)
                invoke = first_invoke_in("".join(text))
                if invoke is not None:
                    turn.close()
                    break
    finally:
        if stream is not None:
            spent = getattr(llm, "last_cost", None) or {}
            paid = {"phase": phase, "seconds": time.time() - start,
                    "model": spent.get("model", model)}
            for key in ("usd", "prompt_tokens", "completion_tokens", "cached_tokens",
                        "cache_creation_tokens", "reasoning_tokens", "usage_unavailable",
                        "resolved_model",
                        "stop_reason", "duration_api_ms", "num_turns", "api_error_status",
                        "terminal_reason"):
                if key in spent:
                    paid[key] = spent[key]
            stream.emit(COST, **paid)

    if invoke is None:
        reply = "".join(text)
        session.messages[-1]["content"] = _the_object_alone(reply)
        return reply

    name, parameters = invoke
    session.messages.append({"role": "assistant", "content": "".join(text)})
    recovered = recovered_message(parameters)
    if stream is not None:
        stream.emit(INVOKE_MARKUP, phase=phase, name=name,
                    parameters=sorted(parameters), recovered=recovered is not None)
    if recovered is not None:
        return recovered
    return _one_turn(session, llm, read_prompt("invoke_markup_used.md"), phase, stream)


def _the_object_alone(reply):
    """
    *reply* cut down to the object it carries, since text written after it
    is the model answering its own call and replaying that to the provider
    is what its reasoning extraction filter refuses. A reply with no object
    is kept as written.
    """
    parsed = _object_of(reply)
    return json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, dict) else reply


def _parsed(text):
    """The object a turn carries, or what was wrong with the turn instead."""
    if not (text or "").strip():
        return {"_empty": True}
    parsed = _object_of(text)
    if parsed is None:
        return {"_malformed": text[:400]}
    if not isinstance(parsed, dict):
        return {"_not_object": parsed}
    return parsed


def _feedback(result, lint_warnings=None):
    """
    What the agent's next turn is told about the Step it just ran, as a dict:
    the same payload is the `tool_result` that carries the kernel's answer
    into the session log, which held only the code the model wrote.
    `lint_warnings` is the exploration lint's findings, the words a declared
    Step would be refused with, none of them blocking the call.
    """
    payload = {"ok": result.ok, "status": "ok" if result.ok else "failed",
               "checkpoint": result.checkpoint}
    if result.stdout:
        payload["stdout"] = result.stdout
    if result.volume:
        payload["volume"] = result.volume
    if result.rolled_back:
        payload["rolled_back"] = result.rolled_back
    if lint_warnings:
        payload["lint_warnings"] = lint_warnings
    if result.ok:
        touched = set(result.created) | set(result.modified)
        geometry = {name: g for name, g in result.geometry.items() if name in touched}
        payload.update(created=result.created, modified=result.modified,
                       deleted=result.deleted, geometry=geometry)
    else:
        payload["traceback"] = result.traceback
    return payload
