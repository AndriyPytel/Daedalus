"""
The loop's builder: one model choosing its own route over the kernel, the read tools and the render, with no Design, Plan, Plan Gate or Review around it.

Each `code` call is one `apply_step` call, which lints, rehearses and lands it on the live Document; the first `finish` is answered with the Domain's Assertion, and every `finish` is refused until the Domain's done-check holds. A reply keyed by one of the Domain's own calls is answered by that call, or by the `code` call it answers with.
"""
import json
import os
import shutil
import tempfile

from core.agent.agent import _feedback, _parsed, _say
from core.events import (APPLIED, ATTACHED, BUDGET_EXHAUSTED, COMPLETED, ERROR, FAILED, INTENT,
                         PREFLIGHT_FAILED, TOOL_CALL, TOOL_RESULT)
from core.freecad_apply import TRAPS
from core.llm import Session
from core.loop.prompt import _TRAPS_PLACEHOLDER, _as_image
from core.loop.results import LoopResult
from core.prompts import read_prompt
from core.tools import ToolError

READ_TOOLS = ("snapshot", "overall_size", "describe_object", "describe_type", "supported_types",
              "materials", "dependencies")

DECLINED_AFTER = 3
DECLINED = "model declined or could not start"


def with_traps(body):
    """*body* with the traps this FreeCAD build has filled in."""
    return body.replace(_TRAPS_PLACEHOLDER, "\n".join("- " + t for t in TRAPS))


def _answer(result):
    payload = _feedback(result, None)
    payload.pop("checkpoint", None)
    return payload


def run_solo(intent, *, domain, llm, kernel, tools, events, attachments=(), cancelled=None,
             max_turns):
    """
    Run *intent* under *domain* to an accepted `finish`, the turn ceiling or *cancelled*, and hand back a `LoopResult` that is ok only where something landed and the Domain's done-check holds.
    *tools* is the registry whose `apply_step` takes the whole `code` call.
    """
    events.emit(INTENT, text=intent)
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(p), "path": p} for p in attachments])

    session = Session(system=with_traps(domain.system))
    content = domain.opening
    renders = tempfile.mkdtemp(prefix="fcc-solo-")
    state = {"applied": 0, "failures": 0, "renders": 0, "asserted": False, "stopped": None,
             "refused_finishes": 0}
    ownership_refusals = {}

    def built(reason, ok=True):
        missing = domain.done_check(kernel) if ok and state["applied"] else None
        return LoopResult(ok=ok and state["applied"] > 0 and missing is None,
                          reason=missing or reason, steps_applied=state["applied"])

    def refused(traceback, gate):
        state["failures"] += 1
        events.emit(PREFLIGHT_FAILED, step=state["applied"] + 1, attempt=state["failures"],
                    traceback=traceback, gate=gate)

    def code_turn(code):
        events.emit(TOOL_CALL, name="code", arguments={"code": code})
        landed = tools.call("apply_step", {"code": code})
        if landed.get("status") == "applied":
            state["applied"] += 1
            state["failures"] = 0
            events.emit(APPLIED, step=state["applied"], label="code", created=landed["created"])
        else:
            if landed.get("gate") == "ownership":
                seen = landed.get("traceback")
                ownership_refusals[seen] = ownership_refusals.get(seen, 0) + 1
                if ownership_refusals[seen] == 2:
                    state["stopped"] = "the same deletion was refused twice: %s" % seen
            refused(landed.get("traceback") or "The Document refused the call.",
                    landed.get("gate") or None)
        answer = {key: value for key, value in landed.items() if key != "description"}
        events.emit(TOOL_RESULT, name="code", result=answer)
        return json.dumps(answer, default=str)

    def probe_turn(code):
        events.emit(TOOL_CALL, name="probe", arguments={"code": code})
        answer = _answer(kernel.probe(code))
        events.emit(TOOL_RESULT, name="probe", result=answer)
        return json.dumps(answer, default=str)

    def inspect_turn(call):
        call = call if isinstance(call, dict) else {}
        name, arguments = str(call.get("tool") or ""), call.get("arguments") or {}
        events.emit(TOOL_CALL, name=name, arguments=dict(arguments))
        try:
            if name not in READ_TOOLS:
                raise ToolError("%r is not a tool you can inspect with" % name)
            answer = tools.call(name, arguments)
        except ToolError as exc:
            answer = {"error": str(exc)}
        events.emit(TOOL_RESULT, name=name, result=answer)
        return json.dumps(answer, default=str)

    def call_turn(name, argument):
        argument = argument if isinstance(argument, dict) else {}
        events.emit(TOOL_CALL, name=name, arguments=argument)
        answer = domain.calls[name](argument)
        if "code" in answer:
            return code_turn(answer["code"])
        events.emit(TOOL_RESULT, name=name, result=answer)
        return json.dumps(answer, default=str)

    def render_turn(view):
        state["renders"] += 1
        view = view if isinstance(view, str) else None
        events.emit(TOOL_CALL, name="render", arguments={"view": view})
        drawn = kernel.render(os.path.join(renders, "%d.png" % state["renders"]), view)
        picture = _as_image(drawn.render) if drawn.ok else None
        answer = {"rendered": view or "isometric"} if picture else {
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
            if "finish" not in message:
                state["refused_finishes"] = 0
            if "finish" in message:
                missing = domain.done_check(kernel)
                if missing:
                    events.emit(TOOL_RESULT, name="finish_refused", result={"text": missing})
                    state["refused_finishes"] += 1
                    if state["refused_finishes"] >= DECLINED_AFTER and not state["applied"]:
                        events.emit(FAILED, message=DECLINED)
                        return built(DECLINED, ok=False)
                    content = missing
                    continue
                if not state["asserted"]:
                    state["asserted"] = True
                    answer = domain.assertion()
                    if answer:
                        content, views = answer
                        events.emit(TOOL_RESULT, name="finish_assertion", result={
                            "returned": True, "views": views,
                            "text": "\n".join(b["text"] for b in content if b.get("type") == "text")})
                        continue
                events.emit(COMPLETED, summary=str(message["finish"]))
                return built(str(message["finish"]))
            if "code" in message:
                content = code_turn(str(message["code"]))
                if state["stopped"]:
                    events.emit(FAILED, message=state["stopped"])
                    return built(state["stopped"], ok=False)
            elif "probe" in message:
                content = probe_turn(str(message["probe"]))
            elif "inspect" in message:
                content = inspect_turn(message["inspect"])
            elif message.get("render"):
                content = render_turn(message["render"])
            elif call := next((name for name in domain.calls if name in message), None):
                content = call_turn(call, message[call])
            else:
                content = read_prompt("solo_correction.md")
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        events.emit(FAILED, message="%s: %s" % (type(exc).__name__, exc))
        return built(str(exc), ok=False)
    finally:
        shutil.rmtree(renders, ignore_errors=True)
