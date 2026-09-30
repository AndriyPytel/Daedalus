"""
The one-shot arm: one model call from the drawing and the intent to a complete
FreeCAD script, executed once in this process with no retry and no feedback.
Whatever geometry the script made before it raised is kept, so a late failure
costs the arm no more than it would cost a person running the same script.
"""
import os
import re

from core.agent.agent import _say
from core.events import APPLIED, ATTACHED, COMPLETED, ERROR, FAILED, INTENT, PREFLIGHT_FAILED
from core.llm import Session
from core.loop.prompt import _as_image
from core.loop.results import LoopResult
from core.agent.solo import with_traps
from core.prompts import read_prompt

_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def script_of(reply):
    """The fenced Python block a reply carries, or the whole reply where it has none."""
    fenced = _FENCE.search(reply or "")
    return (fenced.group(1) if fenced else (reply or "")).strip()


def run_oneshot(intent, *, llm, document, events, attachments=()):
    """Ask once, run once against *document*, and say how it went."""
    from core.kernel.kernel_worker import (_NamedDoc, register_step_source, step_namespace,
                                           trimmed_traceback)

    events.emit(INTENT, text=intent)
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(p), "path": p} for p in attachments])

    content = [{"type": "text", "text": intent}]
    content += [block for block in map(_as_image, attachments) if block]
    try:
        reply = _say(Session(system=with_traps(read_prompt("oneshot.md"))), llm, content,
                     phase="oneshot", stream=events)
        code = script_of(reply)
        try:
            register_step_source(code)
            exec(compile(code, "<step>", "exec"), step_namespace(_NamedDoc(document, {})))  # noqa: S102
            document.recompute()
        except BaseException as exc:
            failure = trimmed_traceback(exc)
            events.emit(PREFLIGHT_FAILED, step=1, attempt=1, traceback=failure, gate=None)
            events.emit(FAILED, message=failure)
            return LoopResult(ok=False, reason=failure)
        events.emit(APPLIED, step=1, label="script", created=[o.Name for o in document.Objects])
        events.emit(COMPLETED)
        return LoopResult(ok=True, steps_applied=1)
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        events.emit(FAILED, message="%s: %s" % (type(exc).__name__, exc))
        return LoopResult(ok=False, reason=str(exc))
