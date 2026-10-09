"""
The multi-turn phase agent: a phase's conversation with its own kernel, a
turn read one way only -- the fenced block if present, else the whole text,
then `json.loads`. Measured on one run's 27 Execute turns: 5 carried the
object the model meant but wrapped in a stray sentence, a duplicate object,
or a truncation, each costing a turn on `contract_correction` while Execute
never reached `declare`. `_feedback` carries `volume` on both branches,
because what a feature moved is the number a failed Step's own assert was
about.
"""
import json
import os
import shutil
import tempfile
import time

from core.events import (
    CALL_STARTED, COST, INVOKE_MARKUP, STEP_DECLARED, THOUGHT, TOOL_CALL, TOOL_RESULT,
    TURN_BUDGET_EXHAUSTED, WRITING,
)
from core.kernel import Kernel
from core.llm import REASONING, TEXT, Session
from core.llm.exceptions import StreamCutOff
from core.agent import phase_contract as pc
from core.agent.invoke_guard import first_invoke_in, recovered_message
from core.agent.phase_contract import spawn
from core.agent.subagent import SessionTreeSupervisor, delegate
from core.freecad_apply import discard
from core.lint import lint
from core.loop.prompt import _CUT_OFF_ATTEMPTS, _LEDGER_HEADING, _object_of, prompt
from core.prompts import read_prompt


class AgentGaveUp(Exception):
    """The model neither ran code nor declared, or the budget ran out first."""


_NO_DOCUMENT = object()


_FORCED_BY_BUDGET = "_forced_by_budget"
"""The key `run_phase` sets on a `declare` reply sent while `last_call` is
True, so a caller can tell the declaration `budget_last_call.md` compelled
from one the model chose to make -- a verdict on a Step it never had a turn
left to attempt."""


_DECLARATION_LIMIT = 15
"""Turns a phase may run code without declaring a Step before it is demanded
one, past which a phase that still does not declare gives up. Counted against
the Step in progress, not the call: a caller that threads the same
`exploration` dict through a regeneration for the same undeclared Step
carries the count forward, so a fresh Session no longer buys a fresh budget
for probing the one thing that keeps failing. A pass that only reads spends
the whole budget and leaves the run nothing to apply."""


_PYTHON_LEVEL_ERRORS = ("AttributeError", "NameError", "TypeError", "SyntaxError")


def _a_python_level_error(traceback):
    """
    Whether *traceback* names a bug in the Step's own code -- an attribute,
    name or type it got wrong, or code that does not parse -- rather than a
    geometric refusal or a failed assertion, which read from the object the
    code built and say nothing about the code itself.
    """
    return bool(traceback) and any(kind + ":" in traceback for kind in _PYTHON_LEVEL_ERRORS)


def run_phase(phase, working_dir, sources, llm, task, document=_NO_DOCUMENT,
              kernel=None, supervisor=None, stream=None, system=None,
              on_declaration=None, exploration=None, design=None, repair_ceiling=None,
              already_delivered=(), ledger="", plan_values=None):
    """
    Run *phase* as a multi-turn agent against its own kernel, returning
    `(declared, session, declarations)`. *on_declaration*, called the moment a
    Step is declared, answers `{"delivered": True}`, a retry shape (`retry` or
    `drift`, kept apart for SignatureMismatch) that rewinds the kernel and
    asks again in this session, or a final `{"delivered": False}` for the
    caller's handback -- which rewinds the kernel the same way, reopening the
    answer's own `resync` path (the real Document after the rejection) when
    given, the last landed checkpoint otherwise, since a Step abandoned there
    already committed its Preflight checkpoint though the real Document never
    landed it. *exploration*
    seeds and is updated with `since_declaration`, *design* is carried into
    the kernel alongside *document*, remembered past this call the same as a
    Step's allocated names, and *repair_ceiling* refuses an `unbuildable`
    declared right after the Step's own rehearsal code raised a Python-level
    error (`_a_python_level_error`) rather than a geometric refusal, demanding
    the Step again, up to that many times, before it reaches a revision;
    *already_delivered* seeds Step order enforcement with numbers a prior
    call already landed, so a regeneration resuming mid-Plan does not refuse
    its own first Step for a predecessor this call never saw; *ledger* is
    rendered after the Protocol rather than folded into *system*, the same
    split `core.agent.agent._composed_system` documents; and *plan_values*,
    handed to the kernel the same way as *design*, is captured back out of
    every call the Plan phase itself makes, so a later Step can name a
    value an earlier one computed.
    """
    working = spawn(phase, working_dir,
                    **(dict(sources, document=document) if isinstance(document, str)
                       else sources))
    session = Session(system=_composed_system(system, ledger))
    working_kernel = kernel
    declarations = []
    delivered_steps = set(already_delivered)
    first_step = True
    declared_failure = None
    pending_retry = None
    last_failure = None
    repairs = 0
    last_landed_checkpoint = None
    last_call = False
    exploration = {} if exploration is None else exploration
    since_declaration = exploration.get("turns", 0)
    declaration_demanded = False
    plan_length = len(sources.get("plan") or ()) if phase == pc.EXECUTE else 0

    def own_turns():
        if supervisor is not None and plan_length:
            supervisor.begin_step(max(plan_length - len(delivered_steps), 1))

    def replied_with(result):
        if stream is not None:
            stream.emit(TOOL_RESULT, name=phase, result=result)
        return _say(session, llm, json.dumps(result), phase=phase, stream=stream)

    own_turns()
    try:
        text = _say(session, llm, task, phase=phase, stream=stream)
        while True:
            message = _parsed(text)
            if "declare" in message:
                declared = message["declare"]
                if last_call and isinstance(declared, dict):
                    declared[_FORCED_BY_BUDGET] = True
                if (repair_ceiling and not last_call and isinstance(declared, dict)
                        and declared.get("unbuildable") and _a_python_level_error(last_failure)
                        and repairs < repair_ceiling):
                    repairs += 1
                    refused = {"ok": False, "status": "refused",
                              "traceback": "%s\n\nThat is a bug in the Step's own code, "
                                            "not a verdict on the Plan -- write the Step "
                                            "again rather than declare it unbuildable."
                                            % last_failure}
                    text = replied_with(refused)
                    continue
                if pending_retry is not None and not (
                        declared.get("unbuildable") or declared.get("obsolete")):
                    declarations.append(pending_retry)
                return declared, session, declarations
            if "delegate" in message:
                if supervisor is None:
                    raise AgentGaveUp("%s tried to delegate with no supervisor" % phase)
                answer = delegate(supervisor, session, llm, message["delegate"],
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
                fix = message.get("fix")
                builds = message.get("step") is not None or fix is not None
                if supervisor is not None and builds:
                    supervisor.record_turn()
                    if supervisor.outcome or supervisor.reserved():
                        if last_call:
                            raise AgentGaveUp("%s ran out of budget before declaring" % phase)
                        last_call = True
                        if stream is not None:
                            reached = supervisor.exhaustion_reason()
                            if reached is not None:
                                stream.emit(TURN_BUDGET_EXHAUSTED, phase=phase, **reached)
                        text = _say(session, llm, prompt("budget_last_call"),
                                    phase=phase, stream=stream)
                        continue
                if fix is not None and fix not in delivered_steps:
                    refused = {"ok": False, "status": "refused", "fix": fix,
                              "traceback": "Step %s has not been delivered, so there is "
                                            "nothing to fix. Write it as a Step." % fix}
                    text = replied_with(refused)
                    continue
                if message.get("step") in delivered_steps:
                    next_step = _step_beside(message["step"], sources.get("plan"), forward=True)
                    refused = {"ok": False, "status": "refused", "step": message["step"]}
                    if next_step is not None:
                        refused["next_step"] = next_step
                        refused["traceback"] = ("Step %s was already delivered. Write Step "
                                                "%s next." % (message["step"], next_step))
                    else:
                        refused["traceback"] = ("Step %s was already delivered, and is the "
                                                "Plan's last Step. Declare." % message["step"])
                    text = replied_with(refused)
                    continue
                if message.get("step") is not None:
                    previous_step = _step_beside(message["step"], sources.get("plan"), forward=False)
                    if (previous_step is not None and previous_step not in delivered_steps
                            and previous_step != declared_failure):
                        refused = {"ok": False, "status": "refused", "step": message["step"],
                                  "traceback": "Step %s has neither landed nor been declared. "
                                                "Write Step %s first."
                                                % (previous_step, previous_step)}
                        text = replied_with(refused)
                        continue
                if stream is not None:
                    stream.emit(TOOL_CALL, name=phase,
                                arguments={"code": message["code"]})
                if working_kernel is None:
                    working_kernel = Kernel()
                step_document = document if first_step else _NO_DOCUMENT
                kernel_kwargs = {"design": design} if first_step and design is not None else {}
                if first_step and plan_values is not None:
                    kernel_kwargs["plan_values"] = plan_values
                if step_document is not _NO_DOCUMENT:
                    kernel_kwargs["document"] = step_document
                if phase == pc.PLAN:
                    kernel_kwargs["capture_values"] = True
                if builds:
                    kernel_kwargs["builds"] = True
                result = working_kernel.check(
                    message["code"], exploring=not builds, **kernel_kwargs)
                last_failure = None if result.ok else result.traceback
                first_step = False
                if fix is not None:
                    if result.ok and result.checkpoint:
                        since_declaration = 0
                        declaration_demanded = False
                        checkpoint = _kept(working, result.checkpoint, len(declarations))
                        declaration = {
                            "step": None, "fix": fix,
                            "label": message.get("label") or "Fix Step %s" % fix,
                            "code": message["code"], "checkpoint": checkpoint,
                            "signature": dict(result.signature), "names": dict(result.names),
                            "created": list(result.created), "modified": list(result.modified),
                            "deleted": list(result.deleted), "objects": list(result.objects),
                        }
                        delivered = (on_declaration(declaration) if on_declaration is not None
                                    else {"delivered": True})
                        if delivered.get("delivered"):
                            last_landed_checkpoint = checkpoint
                            answer = _feedback(result, None)
                            answer["fixed"] = fix
                        else:
                            resync = delivered.get("resync")
                            working_kernel.check("pass", document=(
                                resync if resync is not None
                                else last_landed_checkpoint
                                if last_landed_checkpoint is not None
                                else (document if isinstance(document, str) else None)))
                            if resync is not None:
                                discard(resync)
                            answer = {"ok": False, "status": "failed", "fix": fix,
                                      "traceback": "The fix for Step %s did not land: %s\n\n"
                                                    "Write the fix again."
                                                    % (fix, delivered.get("traceback", ""))}
                    else:
                        answer = _feedback(result, None)
                    text = replied_with(answer)
                    continue
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
                        text = replied_with(skipped)
                        continue
                    delivered = (on_declaration(declaration) if on_declaration is not None
                                else {"delivered": True})
                    if not delivered.get("delivered"):
                        resync = delivered.get("resync")
                        working_kernel.check("pass", document=(
                            resync if resync is not None
                            else last_landed_checkpoint
                            if last_landed_checkpoint is not None
                            else (document if isinstance(document, str) else None)))
                        if resync is not None:
                            discard(resync)
                    if delivered.get("drift") or delivered.get("retry"):
                        declaration["delivered"] = False
                        declaration["traceback"] = delivered.get("traceback", "")
                        pending_retry = declaration
                        drifted = {"ok": False, "status": "failed",
                                  "traceback": "Step %s replayed against the Document and "
                                                "did not land: %s\n\nWrite Step %s again."
                                                % (message["step"], delivered.get("traceback", ""),
                                                   message["step"])}
                        if delivered.get("directive"):
                            drifted["you_have_failed_this_the_same_way_twice"] = \
                                delivered["directive"]
                        text = replied_with(drifted)
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
                        if message["step"] is not None:
                            delivered_steps.add(message["step"])
                            own_turns()
                        delivered_step = message["step"]
                        declared_failure = None
                        last_landed_checkpoint = checkpoint
                lint_warnings = (lint(message["code"]).problems
                                if "step" not in message else None)
                answer = _feedback(result, lint_warnings)
                if delivered_step is not None:
                    answer["step"] = delivered_step
                    next_step = _step_beside(delivered_step, sources.get("plan"), forward=True)
                    if next_step is not None:
                        answer["next_step"] = next_step
                text = replied_with(answer)
                continue
            if supervisor is not None:
                supervisor.record_turn()
                if (supervisor.outcome or supervisor.reserved()) and not last_call:
                    last_call = True
                    if stream is not None:
                        reached = supervisor.exhaustion_reason()
                        if reached is not None:
                            stream.emit(TURN_BUDGET_EXHAUSTED, phase=phase, **reached)
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
        if supervisor is not None:
            supervisor.end_step()
        exploration["turns"] = since_declaration
        if kernel is None and working_kernel is not None:
            working_kernel.kill()


def run_declaring(phase, sources, llm, task, working_dir=None, supervisor=None, **kwargs):
    """
    `run_phase` in a working directory of its own, removed afterwards unless
    *working_dir* is given, under *supervisor* or a fresh one. Returns the
    declaration as a dict -- `{}` when the phase declared anything else -- and
    the declarations the phase made.
    """
    supervisor = supervisor or SessionTreeSupervisor()
    if working_dir is not None:
        declared, _, declarations = run_phase(
            phase, working_dir, sources, llm, task, supervisor=supervisor, **kwargs)
    else:
        with tempfile.TemporaryDirectory() as made:
            declared, _, declarations = run_phase(
                phase, made, sources, llm, task, supervisor=supervisor, **kwargs)
    return (declared if isinstance(declared, dict) else {}), declarations


def _step_beside(step, plan, forward):
    """
    The Step after *step* the Plan still holds (*forward*) or the one before it,
    or `None` where none remains.

    *plan* -- `sources["plan"]` -- names its own numbers through a list of
    `{"step": ...}` dicts; a plain list of Steps, or none at all, is read
    position by position, 1-based, the only numbering it can mean.
    """
    if not isinstance(plan, list) or not plan:
        if forward:
            return step + 1
        return step - 1 if step > 1 else None
    if all(isinstance(item, dict) and "step" in item for item in plan):
        numbers = [item["step"] for item in plan]
    else:
        numbers = range(1, len(plan) + 1)
    if forward:
        later = [number for number in numbers if number > step]
        return min(later) if later else None
    earlier = [number for number in numbers if number < step]
    return max(earlier) if earlier else None


def _kept(working_dir, checkpoint, index):
    """Copy the kernel's one checkpoint file out under its own step number."""
    kept = os.path.join(working_dir, "checkpoints", "%03d.FCStd" % index)
    os.makedirs(os.path.dirname(kept), exist_ok=True)
    shutil.copyfile(checkpoint, kept)
    return kept


def _composed_system(phase_prompt, ledger=""):
    """
    *phase_prompt* then the Protocol, both the same text on every call a
    phase makes; *ledger*, once this run has a failure to carry forward,
    is rendered last of all -- appended after both rather than folded into
    *phase_prompt* the way `core/loop/prompt.py :: prompt` renders it for
    every other caller, so a call with one more entry than the call before
    it still shares that whole, unchanged prefix rather than losing it to
    an edit in the middle of the string.
    """
    body = phase_prompt + "\n\n" + _protocol() if phase_prompt else _protocol()
    if ledger:
        body = "%s\n\n%s\n%s" % (body, _LEDGER_HEADING, ledger)
    return body


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
                        "cache_creation_tokens", "reasoning_tokens", "usage_unavailable"):
                if key in spent:
                    paid[key] = spent[key]
            stream.emit(COST, **paid)

    if invoke is None:
        return "".join(text)

    name, parameters = invoke
    session.messages.append({"role": "assistant", "content": "".join(text)})
    recovered = recovered_message(parameters)
    if stream is not None:
        stream.emit(INVOKE_MARKUP, phase=phase, name=name,
                    parameters=sorted(parameters), recovered=recovered is not None)
    if recovered is not None:
        return recovered
    return _one_turn(session, llm, read_prompt("invoke_markup_used.md"), phase, stream)


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
