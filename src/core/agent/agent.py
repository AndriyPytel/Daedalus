"""
The multi-turn phase agent: a phase's conversation with its own kernel,
not one provider call in and one JSON reply out (FCC-321).

Every turn the model replies with one JSON object: `{"code": "..."}` runs
one more Step against the phase's kernel and hands back what happened,
`{"delegate": "..."}` hands a bounded question to a depth-1 sub-agent
(`core/agent/subagent.py :: delegate`, FCC-307/FCC-322) and hands back its answer,
or `{"declare": <value>}` ends the phase with `<value>` -- a Step, a
verdict, whatever that phase's own caller expects (FCC-322 is where that
shape is decided, not this module). The loop keeps going for as long as the
model keeps writing code or delegating, bounded only by the tree-wide
turn/token budget (`core/agent/subagent.py`), never a fixed per-phase call count.

The kernel is spawned once, per `core/agent/phase_contract.py`'s declared
visibility row, and kept for the whole phase: every Step after the first
continues that same session (`core/kernel/kernel.py :: Kernel.check`). The kernel
itself keeps one checkpoint file, overwritten on every save
(`core/kernel/kernel_worker.py :: _save_checkpoint`), so this module copies it out
under `working_dir/checkpoints/` after every successful Step -- the history
of every attempt the agent made, not only the one it settled on.
"""
import json
import os
import re
import shutil
import time

from core.events import CALL_STARTED, COST, THOUGHT, WRITING
from core.kernel import Kernel
from core.llm import REASONING, TEXT, Session
from core.llm.exceptions import StreamCutOff
from core.agent.phase_contract import spawn
from core.agent.subagent import delegate
from core.loop.prompt import prompt

_CUT_OFF_ATTEMPTS = 3

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class AgentGaveUp(Exception):
    """The model neither ran code nor declared, or the budget ran out first."""


def run_phase(phase, working_dir, sources, llm, task, document=None,
              kernel=None, supervisor=None, stream=None, system=None):
    """
    Run *phase* as a multi-turn agent against its own kernel.

    *sources* is handed straight to `phase_contract.spawn` -- only the
    artefacts *phase*'s row declares are ever written into `working_dir`.
    *document* opens the kernel's Document for the first Step only, the same
    handoff `Kernel.check` itself defines; every Step after that continues
    the same running session.

    *kernel* lets a caller reuse an already-running `Kernel` (or a test
    double with the same `check` contract); one is started here only when a
    turn replies with `{"code": ...}` and *kernel* is None. Phases that only
    declare (Design, Plan, Review) never touch the Kernel and never import
    FreeCAD. *supervisor* is the tree-wide `SessionTreeSupervisor` a turn is
    charged against, when the caller has one -- delegating (`{"delegate":
    "..."}`) raises `AgentGaveUp` outright with no *supervisor*, because
    there is nothing to charge it against or to bound its depth. *stream* is
    the run's `EventStream`, handed straight to `subagent.delegate` so a
    delegation renders live (FCC-308).

    *system* is the phase's own prompt (from `config/prompts/<phase>.md`),
    composed with the agent protocol to form the full system message.

    Returns `(declared, session, checkpoints)`: what the model declared, its
    whole conversation (FCC-309's `Session`), and every Step's checkpoint
    path in the order the agent produced them.

    Raises `AgentGaveUp` when the model answers outside the
    `code`/`delegate`/`declare` contract, or when *supervisor* says the
    budget is spent first.
    """
    working = spawn(phase, working_dir, **sources)
    session = Session(system=_composed_system(system, working))
    working_kernel = kernel
    checkpoints = []
    first_step = True
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
                        raise AgentGaveUp("%s ran out of budget before declaring" % phase)
                if working_kernel is None:
                    working_kernel = Kernel()
                step_document = document if first_step else _NO_DOCUMENT
                result = working_kernel.check(message["code"], **(
                    {} if step_document is _NO_DOCUMENT else {"document": step_document}))
                first_step = False
                if result.ok and result.checkpoint:
                    checkpoints.append(_kept(working, result.checkpoint, len(checkpoints)))
                text = _say(session, llm, _feedback(result), phase=phase, stream=stream)
                continue
            if supervisor is not None:
                supervisor.record_turn()
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


_NO_DOCUMENT = object()


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
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "prompts", "agent_protocol.md")
    with open(path) as f:
        return f.read()


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
    fenced = _FENCE.search(text or "")
    candidate = (fenced.group(1) if fenced else (text or "")).strip()
    if not candidate:
        return {"_empty": True}
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return {"_malformed": text[:400] if text else ""}
    if not isinstance(parsed, dict):
        return {"_not_object": parsed}
    return parsed


def _feedback(result):
    """What the agent's next turn is told about the Step it just ran."""
    payload = {"ok": result.ok, "checkpoint": result.checkpoint}
    if result.ok:
        touched = set(result.created) | set(result.modified)
        geometry = {name: g for name, g in result.geometry.items() if name in touched}
        payload.update(created=result.created, modified=result.modified,
                       deleted=result.deleted, geometry=geometry)
    else:
        payload["traceback"] = result.traceback
    return json.dumps(payload)
