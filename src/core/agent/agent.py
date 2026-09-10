"""
The multi-turn phase agent: a phase's conversation with its own kernel.

A turn is read for the object it carries, by the routes `core.loop.prompt`
reads a phase's reply by. Read one way only — the fence, else the whole text,
then `json.loads` — this path lost the turns the model got right. Measured on
`logs/sessions/123a8f44549a46b5a2ff1f78ab661dba.jsonl`: 5 of that run's 27
turns, every one in Execute, every one carrying the object it meant. One wrote
a sentence and then the object; three wrote the object, a stray
`</parameter></invoke>`, and a second object; one wrote a truncated object
before the whole one. Each cost a turn on `contract_correction`, and Execute
never reached `declare` — the run had a Design and a Plan and made no part.
"""
import json
import os
import shutil
import time

from core.events import CALL_STARTED, COST, THOUGHT, TOOL_CALL, TOOL_RESULT, WRITING
from core.kernel import Kernel
from core.llm import REASONING, TEXT, Session
from core.llm.exceptions import StreamCutOff
from core.agent.phase_contract import spawn
from core.agent.subagent import delegate
from core.loop.prompt import _CUT_OFF_ATTEMPTS, _object_of, prompt
from core.prompts import read_prompt


class AgentGaveUp(Exception):
    """The model neither ran code nor declared, or the budget ran out first."""


_NO_DOCUMENT = object()


def run_phase(phase, working_dir, sources, llm, task, document=_NO_DOCUMENT,
              kernel=None, supervisor=None, stream=None, system=None):
    """
    Run *phase* as a multi-turn agent against its own kernel.

    *document* is the path the first code turn opens, and the artefact the
    phase's row of `PHASE_CONTRACT` decides it may see on disk; the default
    is a sentinel, not `None`, which the kernel reads as an empty Document.

    Returns `(declared, session, checkpoints)`: what the model declared, its
    whole conversation, and every Step's checkpoint path.
    """
    working = spawn(phase, working_dir,
                    **(dict(sources, document=document) if isinstance(document, str)
                       else sources))
    session = Session(system=_composed_system(system, working))
    working_kernel = kernel
    checkpoints = []
    first_step = True
    last_call = False
    try:
        text = _say(session, llm, task, phase=phase, stream=stream)
        while True:
            message = _parsed(text)
            if "declare" in message:
                return message["declare"], session, checkpoints
            if "delegate" in message:
                if supervisor is None:
                    raise AgentGaveUp("%s tried to delegate with no supervisor" % phase)
                answer = delegate(supervisor, session, 0, llm, message["delegate"],
                                  stream=stream, phase=phase)
                feedback = ({"delegate_answered": answer} if answer is not None
                            else {"delegate_refused": supervisor.refusals[-1]["reason"]})
                text = _say(session, llm, json.dumps(feedback), phase=phase, stream=stream)
                continue
            if "code" in message:
                if supervisor is not None:
                    supervisor.record_turn()
                    if supervisor.outcome:
                        if last_call:
                            raise AgentGaveUp("%s ran out of budget before declaring" % phase)
                        last_call = True
                        text = _say(session, llm, prompt("budget_last_call"),
                                    phase=phase, stream=stream)
                        continue
                if stream is not None:
                    stream.emit(TOOL_CALL, name=phase,
                                arguments={"code": message["code"]})
                if working_kernel is None:
                    working_kernel = Kernel()
                step_document = document if first_step else _NO_DOCUMENT
                result = working_kernel.check(message["code"], **(
                    {} if step_document is _NO_DOCUMENT else {"document": step_document}))
                first_step = False
                if result.ok and result.checkpoint:
                    checkpoints.append(_kept(working, result.checkpoint, len(checkpoints)))
                answer = _feedback(result)
                if stream is not None:
                    stream.emit(TOOL_RESULT, name=phase, result=answer)
                text = _say(session, llm, json.dumps(answer), phase=phase, stream=stream)
                continue
            if supervisor is not None:
                supervisor.record_turn()
                if supervisor.outcome and not last_call:
                    last_call = True
                    text = _say(session, llm, prompt("budget_last_call"),
                                phase=phase, stream=stream)
                    continue
                if supervisor.outcome:
                    if "_malformed" in message:
                        raise AgentGaveUp(
                            "%s did not reply with JSON. It said: %r"
                            % (phase, message["_malformed"]))
                    elif "_not_object" in message:
                        raise AgentGaveUp(
                            "%s replied with JSON but not an object: %r"
                            % (phase, message["_not_object"]))
                    elif message.get("_empty"):
                        raise AgentGaveUp(
                            "%s spent the turn on reasoning and wrote no answer" % phase)
                    else:
                        raise AgentGaveUp(
                            "%s replied with neither code, delegation nor declaration"
                            % phase)
            text = _say(session, llm, prompt("contract_correction"), phase=phase, stream=stream)
    finally:
        if kernel is None and working_kernel is not None:
            working_kernel.kill()


def _kept(working_dir, checkpoint, index):
    """Copy the kernel's one checkpoint file out under its own step number."""
    kept = os.path.join(working_dir, "checkpoints", "%03d.FCStd" % index)
    os.makedirs(os.path.dirname(kept), exist_ok=True)
    shutil.copyfile(checkpoint, kept)
    return kept


def _composed_system(phase_prompt, working_dir):
    protocol = _protocol().replace("{{working_dir}}", working_dir)
    if phase_prompt:
        return phase_prompt + "\n\n" + protocol
    return protocol


def _protocol():
    return read_prompt("agent_protocol.md")


def _say(session, llm, content, phase=None, stream=None):
    for attempt in range(_CUT_OFF_ATTEMPTS):
        try:
            return _one_turn(session, llm, content, phase, stream)
        except StreamCutOff:
            if attempt == _CUT_OFF_ATTEMPTS - 1:
                raise
            session.messages.pop()
            time.sleep(2 ** attempt)


def _one_turn(session, llm, content, phase=None, stream=None):
    llm = llm.for_phase(phase) if hasattr(llm, "for_phase") else llm
    model = getattr(llm, "model", None)
    ceiling = getattr(llm, "timeout", None)
    
    if stream is not None:
        started = {"phase": phase, "model": model}
        if ceiling is not None:
            started["ceiling"] = ceiling
        stream.emit(CALL_STARTED, **started)
    
    start = time.time()
    text = []
    try:
        for chunk in session.turn(llm, content):
            if chunk.kind == REASONING:
                if stream is not None:
                    stream.emit(THOUGHT, phase=phase, text=chunk.text)
            elif chunk.kind == TEXT:
                text.append(chunk.text)
                if stream is not None:
                    stream.emit(WRITING, phase=phase, text=chunk.text)
    finally:
        if stream is not None:
            spent = getattr(llm, "last_cost", None) or {}
            paid = {"phase": phase, "seconds": time.time() - start,
                    "model": spent.get("model", model)}
            for key in ("usd", "prompt_tokens", "completion_tokens", "cached_tokens"):
                if key in spent:
                    paid[key] = spent[key]
            stream.emit(COST, **paid)
    
    return "".join(text)


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


def _feedback(result):
    """
    What the agent's next turn is told about the Step it just ran, as a dict:
    the same payload is the `tool_result` that carries the kernel's answer
    into the session log, which held only the code the model wrote.
    """
    payload = {"ok": result.ok, "status": "ok" if result.ok else "failed",
               "checkpoint": result.checkpoint}
    if result.stdout:
        payload["stdout"] = result.stdout
    if result.ok:
        touched = set(result.created) | set(result.modified)
        geometry = {name: g for name, g in result.geometry.items() if name in touched}
        payload.update(created=result.created, modified=result.modified,
                       deleted=result.deleted, geometry=geometry)
    else:
        payload["traceback"] = result.traceback
    return payload
