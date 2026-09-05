"""
The multi-turn phase agent: a phase's conversation with its own kernel,
not one provider call in and one JSON reply out (FCC-321).

Every turn the model replies with one JSON object: `{"code": "..."}` runs
one more Step against the phase's kernel and hands back what happened, or
`{"declare": <value>}` ends the phase with `<value>` -- a Step, a verdict,
whatever that phase's own caller expects (FCC-322 is where that shape is
decided, not this module). The loop keeps going for as long as the model
keeps writing code, bounded only by the tree-wide turn/token budget
(`core/subagent.py`), never a fixed per-phase call count.

The kernel is spawned once, per `core/phase_contract.py`'s declared
visibility row, and kept for the whole phase: every Step after the first
continues that same session (`core/kernel.py :: Kernel.check`). The kernel
itself keeps one checkpoint file, overwritten on every save
(`core/kernel_worker.py :: _save_checkpoint`), so this module copies it out
under `working_dir/checkpoints/` after every successful Step -- the history
of every attempt the agent made, not only the one it settled on.
"""
import json
import os
import re
import shutil

from core.kernel import Kernel
from core.llm import TEXT, Session
from core.phase_contract import spawn

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class AgentGaveUp(Exception):
    """The model neither ran code nor declared, or the budget ran out first."""


def run_phase(phase, working_dir, sources, llm, task, document=None,
              kernel=None, supervisor=None):
    """
    Run *phase* as a multi-turn agent against its own kernel.

    *sources* is handed straight to `phase_contract.spawn` -- only the
    artefacts *phase*'s row declares are ever written into `working_dir`.
    *document* opens the kernel's Document for the first Step only, the same
    handoff `Kernel.check` itself defines; every Step after that continues
    the same running session.

    *kernel* lets a caller reuse an already-running `Kernel` (or a test
    double with the same `check` contract); one is started and killed here
    when no caller supplies it. *supervisor* is the tree-wide
    `SessionTreeSupervisor` a turn is charged against, when the caller has
    one.

    Returns `(declared, session, checkpoints)`: what the model declared, its
    whole conversation (FCC-309's `Session`), and every Step's checkpoint
    path in the order the agent produced them.

    Raises `AgentGaveUp` when the model answers outside the `code`/`declare`
    contract, or when *supervisor* says the budget is spent first.
    """
    working = spawn(phase, working_dir, **sources)
    session = Session(system=_system(phase, working))
    working_kernel = kernel if kernel is not None else Kernel()
    checkpoints = []
    first_step = True
    try:
        text = _say(session, llm, task)
        while True:
            message = _parsed(text)
            if "declare" in message:
                return message["declare"], session, checkpoints
            if "code" not in message:
                raise AgentGaveUp(
                    "%s replied with neither code nor a declaration: %r" % (phase, text))
            if supervisor is not None:
                supervisor.record_turn()
                if supervisor.outcome:
                    raise AgentGaveUp("%s ran out of budget before declaring" % phase)
            step_document = document if first_step else _NO_DOCUMENT
            result = working_kernel.check(message["code"], **(
                {} if step_document is _NO_DOCUMENT else {"document": step_document}))
            first_step = False
            if result.ok and result.checkpoint:
                checkpoints.append(_kept(working, result.checkpoint, len(checkpoints)))
            text = _say(session, llm, _feedback(result))
    finally:
        if kernel is None:
            working_kernel.kill()


_NO_DOCUMENT = object()


def _kept(working_dir, checkpoint, index):
    """Copy the kernel's one checkpoint file out under its own step number."""
    kept = os.path.join(working_dir, "checkpoints", "%03d.FCStd" % index)
    os.makedirs(os.path.dirname(kept), exist_ok=True)
    shutil.copyfile(checkpoint, kept)
    return kept


def _system(phase, working_dir):
    return (
        "You are the %s agent. Your working directory is %s, holding exactly "
        "the artefacts your phase is allowed to see. Reply with one JSON "
        "object per turn: {\"code\": \"...\"} to run Python against your "
        "kernel's Document and see what happened, or {\"declare\": ...} once "
        "you are done." % (phase, working_dir)
    )


def _say(session, llm, content):
    return "".join(chunk.text for chunk in session.turn(llm, content) if chunk.kind == TEXT)


def _parsed(text):
    fenced = _FENCE.search(text or "")
    candidate = (fenced.group(1) if fenced else (text or "")).strip()
    try:
        parsed = json.loads(candidate)
    except ValueError:
        raise AgentGaveUp("could not parse a JSON reply from: %r" % (text or "")[:400])
    if not isinstance(parsed, dict):
        raise AgentGaveUp("reply was JSON but not an object: %r" % parsed)
    return parsed


def _feedback(result):
    """What the agent's next turn is told about the Step it just ran."""
    payload = {"ok": result.ok, "checkpoint": result.checkpoint}
    if result.ok:
        payload.update(created=result.created, modified=result.modified,
                       deleted=result.deleted, geometry=result.geometry)
    else:
        payload["traceback"] = result.traceback
    return json.dumps(payload)
