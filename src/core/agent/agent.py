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

`_feedback` carries `volume` on both branches. What a feature moved is the
number the model's own failing assert was about, so a failed Step is exactly
the reply worth having it on.
"""
import json
import os
import shutil
import time

from core.events import (
    CALL_STARTED, COST, STEP_DECLARED, THOUGHT, TOOL_CALL, TOOL_RESULT, WRITING,
)
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


_DECLARATION_LIMIT = 15
"""Turns a phase may run code without declaring a Step before it is demanded
one, past which a phase that still does not declare gives up. A pass that only
reads spends the whole budget and leaves the run nothing to apply."""


def run_phase(phase, working_dir, sources, llm, task, document=_NO_DOCUMENT,
              kernel=None, supervisor=None, stream=None, system=None,
              on_declaration=None):
    """
    Run *phase* as a multi-turn agent against its own kernel. *on_declaration*,
    called the moment a Step is declared, answers `{"delivered": True}`,
    `{"delivered": False, "retry": True, ...}` (`drift` is the same shape,
    kept for SignatureMismatch), or `{"delivered": False, "traceback": ...}`
    (FCC-A-53, FCC-372). Either `retry` flavour rewinds the kernel to the
    last checkpoint and asks again in this session; a plain
    `{"delivered": False}` is final, for `_work_through`'s handback (FCC-61).
    Returns `(declared, session, declarations)`.
    """
    working = spawn(phase, working_dir,
                    **(dict(sources, document=document) if isinstance(document, str)
                       else sources))
    session = Session(system=_composed_system(system, working))
    working_kernel = kernel
    declarations = []
    delivered_steps = set()
    first_step = True
    declared_failure = None
    pending_retry = None
    last_call = False
    since_declaration = 0
    declaration_demanded = False
    try:
        text = _say(session, llm, task, phase=phase, stream=stream)
        while True:
            message = _parsed(text)
            if "declare" in message:
                declared = message["declare"]
                if pending_retry is not None and not (
                        declared.get("unbuildable") or declared.get("obsolete")):
                    declarations.append(pending_retry)
                return declared, session, declarations
            if "delegate" in message:
                if supervisor is None:
                    raise AgentGaveUp("%s tried to delegate with no supervisor" % phase)
                answer = delegate(supervisor, session, 0, llm, message["delegate"],
                                  stream=stream, phase=phase)
                if answer is not None and declaration_demanded:
                    declaration_demanded = False
                    since_declaration = 0
                feedback = ({"delegate_answered": answer} if answer is not None
                            else {"delegate_refused": supervisor.refusals[-1]["reason"]})
                text = _say(session, llm, json.dumps(feedback), phase=phase, stream=stream)
                continue
            if "code" in message:
                if declaration_demanded:
                    raise AgentGaveUp(
                        "%s ran %d turns without declaring" % (phase, since_declaration))
                since_declaration += 1
                if since_declaration > _DECLARATION_LIMIT:
                    declaration_demanded = True
                    text = _say(session, llm, prompt("declaration_limit"),
                                phase=phase, stream=stream)
                    continue
                if supervisor is not None:
                    supervisor.record_turn()
                    if supervisor.outcome:
                        if last_call:
                            raise AgentGaveUp("%s ran out of budget before declaring" % phase)
                        last_call = True
                        text = _say(session, llm, prompt("budget_last_call"),
                                    phase=phase, stream=stream)
                        continue
                if message.get("step") in delivered_steps:
                    next_step = _next_step(message["step"], sources.get("plan"))
                    refused = {"ok": False, "status": "refused", "step": message["step"]}
                    if next_step is not None:
                        refused["next_step"] = next_step
                        refused["traceback"] = ("Step %s was already delivered. Write Step "
                                                "%s next." % (message["step"], next_step))
                    else:
                        refused["traceback"] = ("Step %s was already delivered, and is the "
                                                "Plan's last Step. Declare." % message["step"])
                    if stream is not None:
                        stream.emit(TOOL_RESULT, name=phase, result=refused)
                    text = _say(session, llm, json.dumps(refused), phase=phase, stream=stream)
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
                delivered_step = None
                if "step" in message and result.ok and result.checkpoint:
                    since_declaration = 0
                    declaration_demanded = False
                    checkpoint = _kept(working, result.checkpoint, len(declarations))
                    declaration = {
                        "step": message["step"], "label": message.get("label"),
                        "code": message["code"], "checkpoint": checkpoint,
                        "signature": dict(result.signature), "names": dict(result.names),
                        "created": list(result.created), "modified": list(result.modified),
                        "deleted": list(result.deleted), "objects": list(result.objects),
                    }
                    if stream is not None:
                        stream.emit(STEP_DECLARED, step=message["step"],
                                    label=message.get("label"), checkpoint=checkpoint)
                    if declared_failure is not None and message["step"] != declared_failure:
                        skipped = {"ok": False, "status": "failed",
                                  "traceback": "Step %s was not applied because Step %s "
                                                "failed. Fix Step %s first."
                                                % (message["step"], declared_failure,
                                                   declared_failure)}
                        if stream is not None:
                            stream.emit(TOOL_RESULT, name=phase, result=skipped)
                        text = _say(session, llm, json.dumps(skipped), phase=phase, stream=stream)
                        continue
                    delivered = (on_declaration(declaration) if on_declaration is not None
                                else {"delivered": True})
                    if delivered.get("drift") or delivered.get("retry"):
                        working_kernel.check("pass", document=(
                            declarations[-1]["checkpoint"] if declarations
                            else (document if isinstance(document, str) else None)))
                        declaration["delivered"] = False
                        declaration["traceback"] = delivered.get("traceback", "")
                        pending_retry = declaration
                        drifted = {"ok": False, "status": "failed",
                                  "traceback": "Step %s replayed against the Document and "
                                                "did not land: %s\n\nWrite Step %s again."
                                                % (message["step"], delivered.get("traceback", ""),
                                                   message["step"])}
                        if stream is not None:
                            stream.emit(TOOL_RESULT, name=phase, result=drifted)
                        text = _say(session, llm, json.dumps(drifted), phase=phase, stream=stream)
                        continue
                    elif not delivered.get("delivered"):
                        pending_retry = None
                        declaration["delivered"] = False
                        declaration["traceback"] = delivered.get("traceback", "")
                        declarations.append(declaration)
                        declared_failure = message["step"]
                    else:
                        pending_retry = None
                        declarations.append(declaration)
                        delivered_steps.add(message["step"])
                        delivered_step = message["step"]
                        declared_failure = None
                answer = _feedback(result)
                if delivered_step is not None:
                    answer["step"] = delivered_step
                    next_step = _next_step(delivered_step, sources.get("plan"))
                    if next_step is not None:
                        answer["next_step"] = next_step
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


def _next_step(step, plan):
    """
    The Step after *step* the Plan still holds, or `None` once none remain.

    *plan* -- `sources["plan"]` -- names its own numbers through a list of
    `{"step": ...}` dicts; a plain list of Steps, or none at all, is read
    position by position, 1-based, the only numbering it can mean.
    """
    if not isinstance(plan, list) or not plan:
        return step + 1
    if all(isinstance(item, dict) and "step" in item for item in plan):
        numbers = [item["step"] for item in plan]
    else:
        numbers = range(1, len(plan) + 1)
    later = [number for number in numbers if number > step]
    return min(later) if later else None


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
    if result.volume:
        payload["volume"] = result.volume
    if result.ok:
        touched = set(result.created) | set(result.modified)
        geometry = {name: g for name, g in result.geometry.items() if name in touched}
        payload.update(created=result.created, modified=result.modified,
                       deleted=result.deleted, geometry=geometry)
    else:
        payload["traceback"] = result.traceback
    return payload
