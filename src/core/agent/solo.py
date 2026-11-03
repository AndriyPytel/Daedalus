"""
The loop's builder: one model choosing its own route over the kernel, the read tools and the render, with no Design, Plan, Plan Gate or Review around it.

Each `code` call is linted, rehearsed in the kernel and landed on the live Document; the first `finish` is answered with the Assertion, and every `finish` is refused until the Document's solid is the Tip of a PartDesign::Body with features.
"""
import json
import os
import shutil
import tempfile

from core.agent.agent import _feedback, _parsed, _say
from core.events import (APPLIED, ATTACHED, BUDGET_EXHAUSTED, COMPLETED, ERROR, FAILED, INTENT,
                         PREFLIGHT_FAILED, TOOL_CALL, TOOL_RESULT)
from core.freecad_apply import TRAPS, discard
from core.lint import lint
from core.llm import Session
from core.loop.prompt import _TRAPS_PLACEHOLDER, _as_image
from core.loop.results import LoopResult
from core.prompts import read_prompt
from core.tools import ToolError

READ_TOOLS = ("snapshot", "overall_size", "describe_object", "describe_type", "supported_types",
              "materials", "dependencies")

TREE_CHECK = """
import json

solids = [o for o in doc.Objects
          if not o.InList and getattr(o, "Shape", None) is not None
          and not o.Shape.isNull() and o.Shape.Solids]
found = []
for o in solids:
    tip = o.Tip if o.TypeId == "PartDesign::Body" else None
    features = [f.TypeId for f in o.Group if f.TypeId.startswith("PartDesign::")] if tip else []
    found.append({"name": o.Name, "type": o.TypeId, "tip": tip.TypeId if tip else None,
                  "features": features})
print("TREE " + json.dumps(found))
"""

NO_TREE = ("The Document's solid is not a feature tree: %s. Build the part in a PartDesign::Body "
           "from sketches and PartDesign features (Pad, Pocket, Revolution, ...) so it can be "
           "edited, then finish.")


def with_traps(body):
    """*body* with the traps this FreeCAD build has filled in."""
    return body.replace(_TRAPS_PLACEHOLDER, "\n".join("- " + t for t in TRAPS))


def without_feature_tree(kernel):
    """Why the kernel's Document does not hold a PartDesign::Body whose Tip is a feature, or `None` where it does."""
    result = kernel.check(TREE_CHECK, exploring=True)
    found = None
    for line in reversed((result.stdout or "").splitlines()):
        if line.startswith("TREE "):
            found = json.loads(line[len("TREE "):])
            break
    if not found:
        return NO_TREE % "the Document holds no solid"
    faults = []
    for entry in found:
        if entry["type"] != "PartDesign::Body":
            faults.append("%s is a %s, not a PartDesign::Body" % (entry["name"], entry["type"]))
        elif not entry["tip"] or not entry["tip"].startswith("PartDesign::") or not entry["features"]:
            faults.append("Body %s has no PartDesign feature as its Tip" % entry["name"])
    return NO_TREE % "; ".join(faults) if faults else None


def _answer(result):
    payload = _feedback(result, None)
    payload.pop("checkpoint", None)
    return payload


def run_solo(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None,
             max_turns, system, opening, assertion):
    """
    Run *intent* to an accepted `finish`, the turn ceiling or *cancelled*, and hand back a `LoopResult` that is ok only where something landed and the Document holds a feature tree.
    *tools* is the registry whose `apply_step` lands a rehearsed `code` call on the live Document, *opening* is the first message, and *assertion*, asked once at the first accepted `finish`, answers with the content to send back and the names of the views it draws, or `None`.
    """
    events.emit(INTENT, text=intent)
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(p), "path": p} for p in attachments])

    session = Session(system=with_traps(system))
    content = opening
    renders = tempfile.mkdtemp(prefix="fcc-solo-")
    state = {"applied": 0, "failures": 0, "renders": 0, "asserted": False}

    def built(reason, ok=True):
        missing = without_feature_tree(kernel) if ok and state["applied"] else None
        return LoopResult(ok=ok and state["applied"] > 0 and missing is None,
                          reason=missing or reason, steps_applied=state["applied"])

    def refused(traceback, gate):
        state["failures"] += 1
        events.emit(PREFLIGHT_FAILED, step=state["applied"] + 1, attempt=state["failures"],
                    traceback=traceback, gate=gate)
        return {"ok": False, "status": "failed", "traceback": traceback}

    def resync():
        path = tools.call("document_copy")["path"]
        kernel.check("pass", document=path)
        discard(path)

    def land(code, result):
        arguments = {"code": code, "label": "code", "design_model": False,
                     "kernel_result": {"created": result.created, "deleted": result.deleted,
                                       "modified": result.modified, "objects": result.objects,
                                       "names": result.names, "signature": result.signature}}
        events.emit(TOOL_CALL, name="apply_step", arguments=dict(arguments))
        landed = tools.call("apply_step", arguments)
        events.emit(TOOL_RESULT, name="apply_step", result=landed)
        return landed

    def code_turn(code):
        events.emit(TOOL_CALL, name="code", arguments={"code": code})
        checked = lint(code, design_model=False)
        if not checked.ok:
            answer = refused(checked.report(), "lint")
        else:
            result = kernel.check(code)
            answer = _answer(result)
            if not result.ok:
                refused(result.traceback, None)
            elif result.created or result.modified or result.deleted:
                landed = land(code, result)
                if landed.get("status") == "applied":
                    state["applied"] += 1
                    state["failures"] = 0
                    events.emit(APPLIED, step=state["applied"], label="code",
                                created=result.created)
                else:
                    resync()
                    answer = refused(landed.get("traceback") or "The Document refused the call.",
                                     landed.get("gate"))
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
            if name not in READ_TOOLS:
                raise ToolError("%r is not a tool you can inspect with" % name)
            answer = tools.call(name, arguments)
        except ToolError as exc:
            answer = {"error": str(exc)}
        events.emit(TOOL_RESULT, name=name, result=answer)
        return json.dumps(answer, default=str)

    def render_turn(view):
        state["renders"] += 1
        view = view if isinstance(view, str) else None
        events.emit(TOOL_CALL, name="render", arguments={"view": view})
        drawn = kernel.check("pass", render_path=os.path.join(renders, "%d.png" % state["renders"]),
                             render_view=view)
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
            if "finish" in message:
                missing = without_feature_tree(kernel)
                if missing:
                    events.emit(TOOL_RESULT, name="finish_refused", result={"text": missing})
                    content = missing
                    continue
                if not state["asserted"]:
                    state["asserted"] = True
                    answer = assertion()
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
            elif "probe" in message:
                content = probe_turn(str(message["probe"]))
            elif "inspect" in message:
                content = inspect_turn(message["inspect"])
            elif message.get("render"):
                content = render_turn(message["render"])
            else:
                content = read_prompt("solo_correction.md")
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        events.emit(FAILED, message="%s: %s" % (type(exc).__name__, exc))
        return built(str(exc), ok=False)
    finally:
        shutil.rmtree(renders, ignore_errors=True)
