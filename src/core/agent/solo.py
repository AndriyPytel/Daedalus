"""
The agent arm: one model choosing its own route over the same kernel, read
tools and render the pipeline uses, with no Design, Plan, Gate, Assertion or
Review around it. State lives in the kernel worker, so a crash in the model's
code costs a turn, never the run, and the last checkpoint is what gets exported.
"""
import json
import os
import shutil
import tempfile

from core.agent.agent import _feedback, _parsed, _say
from core.events import (APPLIED, ATTACHED, BUDGET_EXHAUSTED, COMPLETED, ERROR, FAILED, INTENT,
                         PREFLIGHT_FAILED, TOOL_CALL, TOOL_RESULT)
from core.llm import Session
from core.loop.prompt import _TRAPS_PLACEHOLDER, _as_image
from core.loop.results import LoopResult
from core.freecad_apply import TRAPS
from core.prompts import read_prompt
from core.tools import ToolError

NOT_INSPECTABLE = ("document_copy",)


def with_traps(body):
    """*body* with the traps this FreeCAD build has filled in."""
    return body.replace(_TRAPS_PLACEHOLDER, "\n".join("- " + t for t in TRAPS))


def _answer(result):
    payload = _feedback(result, None)
    payload.pop("checkpoint", None)
    return payload


def run_solo(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None,
             max_turns=60, refresh=None):
    """
    Run *intent* to `finish`, the turn ceiling or *cancelled*, and hand back
    `(LoopResult, checkpoint)`, the checkpoint being the kernel's Document as it
    last stood or `None` where nothing landed. *refresh* is called before a read
    tool asks about the Document, so the Document it reads is the kernel's.
    """
    events.emit(INTENT, text=intent)
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(p), "path": p} for p in attachments])

    session = Session(system=with_traps(read_prompt("solo.md")))
    content = [{"type": "text", "text": intent}]
    content += [block for block in map(_as_image, attachments) if block]
    renders = tempfile.mkdtemp(prefix="fcc-solo-")
    state = {"applied": 0, "failures": 0, "checkpoint": None, "stale": False, "renders": 0}

    def built(reason, ok=True):
        return LoopResult(ok=state["applied"] > 0 and ok, reason=reason,
                          steps_applied=state["applied"]), state["checkpoint"]

    def code_turn(code):
        events.emit(TOOL_CALL, name="code", arguments={"code": code})
        result = kernel.check(code)
        answer = _answer(result)
        if result.ok:
            state["checkpoint"] = result.checkpoint or state["checkpoint"]
            if result.created or result.modified or result.deleted:
                state["applied"] += 1
                state["failures"] = 0
                state["stale"] = True
                events.emit(APPLIED, step=state["applied"], label="code", created=result.created)
        else:
            state["failures"] += 1
            events.emit(PREFLIGHT_FAILED, step=state["applied"] + 1, attempt=state["failures"],
                        traceback=result.traceback, gate=None)
        events.emit(TOOL_RESULT, name="code", result=answer)
        return json.dumps(answer, default=str)

    def probe_turn(code):
        events.emit(TOOL_CALL, name="probe", arguments={"code": code})
        answer = _answer(kernel.check(code, exploring=True))
        events.emit(TOOL_RESULT, name="probe", result=answer)
        return json.dumps(answer, default=str)

    def inspect_turn(call):
        call = call if isinstance(call, dict) else {}
        name, arguments = str(call.get("tool") or ""), call.get("arguments") or {}
        events.emit(TOOL_CALL, name=name, arguments=dict(arguments))
        try:
            if name in NOT_INSPECTABLE:
                raise ToolError("%r is not a tool you can inspect with" % name)
            if state["stale"] and refresh is not None:
                refresh(state["checkpoint"])
                state["stale"] = False
            answer = tools.call(name, arguments)
        except ToolError as exc:
            answer = {"error": str(exc)}
        events.emit(TOOL_RESULT, name=name, result=answer)
        return json.dumps(answer, default=str)

    def render_turn():
        state["renders"] += 1
        events.emit(TOOL_CALL, name="render", arguments={})
        drawn = kernel.check("pass", render_path=os.path.join(renders, "%d.png" % state["renders"]))
        picture = _as_image(drawn.render) if drawn.ok else None
        answer = {"rendered": "isometric"} if picture else {
            "error": drawn.traceback or "nothing to draw yet"}
        events.emit(TOOL_RESULT, name="render", result=answer)
        return [{"type": "text", "text": json.dumps(answer)}] + ([picture] if picture else [])

    try:
        turns = 0
        while True:
            if cancelled is not None and cancelled():
                events.emit(BUDGET_EXHAUSTED, reason="the dollar ceiling was reached", tried=[])
                return built("the dollar ceiling was reached")
            if turns >= max_turns:
                events.emit(BUDGET_EXHAUSTED, reason="the turn budget was spent", tried=[])
                return built("the turn budget was spent")
            turns += 1
            message = _parsed(_say(session, llm, content, phase="agent", stream=events))
            if "finish" in message:
                events.emit(COMPLETED, summary=str(message["finish"]))
                return built(str(message["finish"]))
            if "code" in message:
                content = code_turn(str(message["code"]))
            elif "probe" in message:
                content = probe_turn(str(message["probe"]))
            elif "inspect" in message:
                content = inspect_turn(message["inspect"])
            elif message.get("render"):
                content = render_turn()
            else:
                content = read_prompt("solo_correction.md")
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        events.emit(FAILED, message="%s: %s" % (type(exc).__name__, exc))
        return built(str(exc), ok=False)
    finally:
        shutil.rmtree(renders, ignore_errors=True)
