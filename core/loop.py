"""
The Plan -> Execute -> Review loop.

Three phases, fixed order, deterministic routing (ADR-0003). No LLM-chosen
speaker selection: Plan produces Steps, Execute works through them one at a
time, Review judges the outcome, and Review's verdict returns to Plan — never
to Execute. Within its turn Execute may spend a bounded number of read-only
looks at the Document before it writes code (ADR-0009); that is one phase
reading its input, not a negotiation between phases.

The loop is a function of `(intent, context)` over injected dependencies, so a
narrower Intent is all a future sub-agent needs. Nothing here imports FreeCAD;
the Document is reached only through the Tool registry.

Budgets, not round limits. On exhaustion the loop emits `blocked` with what was
tried and waits for the user. Silent failure and unbounded looping are both
defects, so both are bounded and both are reported.
"""
import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import List

from core.events import (
    APPLIED, BLOCKED, ERROR, PLAN, PLAN_REVISED, PREFLIGHT_FAILED, RENDER,
    REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, EventStream,
)
from core.freecad_tools import TRAPS
from core.llm import REASONING
from core.tools import ToolError

PLAN_PHASE = "plan"
EXECUTE_PHASE = "execute"
REVIEW_PHASE = "review"

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class PhaseError(Exception):
    """A phase gave back something the loop cannot act on."""


@dataclass
class Budgets:
    attempts_per_step: int = 3
    review_cycles: int = 2
    # Without this, Execute declaring Step after Step obsolete never terminates.
    revisions: int = 3
    # Questions Execute may ask the Document before it has to write the code.
    # Two is enough to check a type and a property list; more is stalling.
    looks_per_step: int = 2


APPROVE = "approve"
REJECT = "reject"
AMEND = "amend"


@dataclass
class Approval:
    """
    The Plan Gate's answer.

    Approval covers every Step in the Plan; there is no per-Step approval.
    """

    decision: str = APPROVE
    feedback: str = ""


def auto_approve(steps):
    """
    The gate when nothing is wired up to ask.

    Level 1, Level 2 and headless runs use this. The panel passes a real gate,
    and a real gate is the only reason a Step ever runs in front of a user.
    """
    return Approval(APPROVE)


@dataclass
class LoopResult:
    ok: bool = False
    blocked: bool = False
    verdict: str = None
    reason: str = ""
    steps_applied: int = 0
    render: str = None
    tried: List[dict] = field(default_factory=list)

    @property
    def awaiting_user(self) -> bool:
        """Blocked means stopped and waiting, not failed and finished."""
        return self.blocked


# ── prompts ───────────────────────────────────────────────────────────────────

_TRAPS = "\n".join("- " + trap for trap in TRAPS)

PLAN_SYSTEM = """You plan changes to a FreeCAD 1.0 Document.

Break the Intent into the shortest ordered list of Steps that achieves it. A
Step is one whole modelling operation — small enough to be judged and undone on
its own, and no smaller. Describe what the Step should achieve, not the code.

Creating a sketch, attaching it to a plane and drawing its geometry is **one**
Step, not three: the parts are written in one breath and are useless apart.
Same for a feature and its parameters. Every Step must change the Document —
there is no editing mode here, so "open the sketch" and "close the sketch" are
not Steps at all.

Reply with JSON only: {"steps": ["...", "..."]}

Traps in this build:
""" + _TRAPS

EXECUTE_SYSTEM = """You write the Python for one Step against a FreeCAD 1.0 Document.

`doc`, `App`, `Part`, `Sketcher` and `PartDesign` are already in scope; you do
not need to import them. The code must be a pure function of the Document:
no Gui.*, no reading the live selection, and no literal element names such as
Edge12 — compute references from the geometry at execution time instead.

Do not guess at the API. Anything you are unsure of — whether a type exists,
what a property is called, what an object is built from — ask the running
FreeCAD with a look before you write code. It answers about this exact build.
The Step's code must still change the Document; defining a function you never
call changes nothing and counts as a failed Step.

Reply with JSON only, one of:
  {"look": {"tool": "...", "arguments": {...}}}   to ask, before committing to code
  {"code": "...", "label": "short name for the undo entry"}
or, if this Step no longer makes sense against the Document you were given:
  {"obsolete": "why"}

Traps in this build:
""" + _TRAPS

REVIEW_SYSTEM = """You judge whether a finished FreeCAD Document matches what the user asked for.

You are given the Intent, a render of the result, and the objects that now
exist. You are deliberately not told how it was produced — no plan, no code, no
reasoning. Judge the outcome alone.

Reply with JSON only: {"verdict": "pass" | "fail", "reason": "..."}"""


# ── the phases ────────────────────────────────────────────────────────────────


def _ask(llm, events, phase, system, user):
    """
    One phase's turn: stream it, surface the reasoning as it forms, parse the reply.

    Reasoning is emitted chunk by chunk rather than aggregated at the end, so the
    user can tell early whether the assistant has understood them.
    """
    text = []
    for chunk in llm.stream([{"role": "system", "content": system},
                             {"role": "user", "content": user}]):
        if chunk.kind == REASONING:
            events.emit(THOUGHT, phase=phase, text=chunk.text)
        else:
            text.append(chunk.text)

    return _as_json("".join(text), phase)


def _as_image(path):
    """
    Read the render as an image content block, so Review actually looks at it.

    A path is meaningless to the model — it cannot open files. Returns None if
    there is nothing to show, which a sketch-only Step produces.
    """
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return {"type": "image_url",
            "image_url": {"url": "data:image/png;base64," + encoded}}


def _as_json(text, phase):
    fenced = _FENCE.search(text)
    candidate = (fenced.group(1) if fenced else text).strip()
    try:
        parsed = json.loads(candidate)
    except ValueError:
        raise PhaseError(
            "%s did not reply with JSON. It said: %s" % (phase, (text or "").strip()[:400])
        )
    if not isinstance(parsed, dict):
        raise PhaseError("%s replied with JSON that is not an object: %r" % (phase, parsed))
    return parsed


def plan(intent, snapshot, llm, events, feedback=None):
    """Produce an ordered list of Steps from the Intent and a Snapshot."""
    request = {"intent": intent, "document": snapshot}
    if feedback:
        request["what_went_wrong_last_time"] = feedback

    reply = _ask(llm, events, PLAN_PHASE, PLAN_SYSTEM, json.dumps(request, indent=2, default=str))
    steps = [str(step) for step in reply.get("steps", []) if str(step).strip()]
    if not steps:
        raise PhaseError("Plan produced no Steps for this Intent.")
    return steps


@dataclass
class Attempt:
    code: str
    traceback: str


@dataclass
class ExecuteOutcome:
    code: str = None
    label: str = "Copilot Step"
    obsolete: str = None


def execute(step, tools, llm, events, attempts, budgets=None):
    """
    Write the code for one Step, after asking the Document whatever it needs.

    Execute is the phase that hallucinates API, and it was the one phase that
    could not ask: it replied with code in one shot while `describe_object` and
    `supported_types` sat in the registry with no caller. It may now spend a
    bounded number of *looks* on the read-only Tools first (ADR-0009).

    `attempts` are the failures **for this Step only**. A Step is not told what
    went wrong elsewhere in the Plan; that is noise it cannot act on.
    """
    budgets = budgets or Budgets()
    # A fresh Snapshot per attempt: the user may have edited the model by hand.
    request = {"step": step, "document": tools.call("snapshot")}
    if attempts:
        request["your_failed_attempts_at_this_step"] = [
            {"code": a.code, "traceback": a.traceback} for a in attempts
        ]

    looked = []
    for remaining in range(budgets.looks_per_step, -1, -1):
        # Offering nothing on the last turn is how "you must answer now" is said
        # in a protocol whose only vocabulary is the request.
        request["tools_you_may_look_with"] = _lookable(tools) if remaining else []
        if looked:
            request["what_you_looked_at"] = looked

        reply = _ask(llm, events, EXECUTE_PHASE, EXECUTE_SYSTEM,
                     json.dumps(request, indent=2, default=str))

        if reply.get("obsolete"):
            return ExecuteOutcome(obsolete=str(reply["obsolete"]))
        if reply.get("code"):
            return ExecuteOutcome(code=str(reply["code"]),
                                  label=str(reply.get("label") or "Copilot Step"))
        if not reply.get("look") or not remaining:
            break
        looked.append(_look(tools, reply["look"], events))

    raise PhaseError("Execute replied with neither code nor an obsolete reason.")


def _lookable(tools):
    """
    The Tools Execute may ask with: everything that only reads.

    `apply_step` is not among them — Execute proposes a Step, the loop decides
    when it runs, and a phase that could apply its own code would be a second,
    unbudgeted route to the Document.
    """
    return [described for described in tools.describe() if described["name"] != "apply_step"]


def _look(tools, look, events):
    """Run one read-only Tool for Execute and hand back what it said."""
    name = str(look.get("tool") or "")
    arguments = look.get("arguments") or {}

    events.emit(TOOL_CALL, name=name, arguments=dict(arguments))
    if name == "apply_step" or name not in tools:
        result = {"error": "%r is not a Tool you can look with. Reply with code instead." % name}
    else:
        try:
            result = tools.call(name, arguments)
        except ToolError as exc:
            # A bad look is a thing to correct, not a reason to lose the Step.
            result = {"error": str(exc)}
    events.emit(TOOL_RESULT, name=name, result=result)

    return {"tool": name, "arguments": arguments, "result": result}


def review(intent, render, objects, llm, events):
    """
    Judge the outcome against the Intent.

    Given the Intent, the render and the final Document state. Deliberately not
    given the Plan, the code, or Execute's reasoning — an independent judgement
    of how it turned out, not of how it was produced.
    """
    request = {"intent": intent, "objects_that_now_exist": objects}
    content = [{"type": "text", "text": json.dumps(request, indent=2, default=str)}]

    picture = _as_image(render)
    if picture:
        content.append(picture)
    else:
        content[0]["text"] += "\n\nNo render was produced, so judge on the objects alone."

    reply = _ask(llm, events, REVIEW_PHASE, REVIEW_SYSTEM, content)

    verdict = str(reply.get("verdict", "")).lower()
    if verdict not in ("pass", "fail"):
        raise PhaseError("Review replied with an unusable verdict: %r" % reply.get("verdict"))
    return verdict, str(reply.get("reason", ""))


# ── the loop ──────────────────────────────────────────────────────────────────


class Stopped(Exception):
    """The user stopped the run. Not a failure — a decision."""


def run(intent, *, llm, tools, events=None, budgets=None, cancelled=None,
        gate=auto_approve) -> LoopResult:
    """
    Turn an Intent into changes to the Document.

    Everything it needs is an argument: the LLM client, the Tool registry, the
    event stream to narrate into, the budgets, `cancelled` — a callable the loop
    asks between Steps and attempts so the user is never a passenger — and
    `gate`, the Plan Gate, which must approve before any Step runs. Nothing is
    global, so two runs cannot see each other and a sub-agent is a narrower
    Intent.
    """
    events = events if events is not None else EventStream()
    budgets = budgets or Budgets()

    try:
        return _run(intent, llm, tools, events, budgets, cancelled or (lambda: False), gate)
    except Stopped:
        return _block(events, "Stopped by you. Everything applied so far is still in the "
                              "Document; undo it Step by Step if you want it gone.", [], 0)
    except PhaseError as exc:
        events.emit(ERROR, message=str(exc))
        return LoopResult(ok=False, reason=str(exc))
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        return LoopResult(ok=False, reason=str(exc))


def _run(intent, llm, tools, events, budgets, cancelled, gate):
    snapshot = tools.call("snapshot")
    steps = plan(intent, snapshot, llm, events)
    events.emit(PLAN, steps=list(steps))

    rejections = 0
    revisions = 0

    # The Plan Gate. Nothing below this line has run yet, and nothing will until
    # the user says so. One approval covers the whole Plan.
    while True:
        approval = gate(list(steps)) or Approval(APPROVE)
        if approval.decision == APPROVE:
            break
        if approval.decision == REJECT:
            return _block(events, "You rejected the Plan, so nothing was applied.", [], 0)

        revisions += 1
        if revisions > budgets.revisions:
            return _block(events, "The Plan has been amended %d times without being approved."
                          % (revisions - 1), [], 0)
        steps = plan(intent, tools.call("snapshot"), llm, events, feedback=approval.feedback)
        events.emit(PLAN_REVISED, steps=list(steps),
                    reason="You asked for: " + approval.feedback)

    render = None
    applied = 0
    objects = []

    while True:
        outcome = _work_through(steps, intent, tools, llm, events, budgets, cancelled)

        if outcome["blocked"]:
            return _block(events, outcome["reason"], outcome["tried"], applied)

        if outcome["obsolete"]:
            revisions += 1
            if revisions > budgets.revisions:
                return _block(
                    events,
                    "Execute keeps finding the Plan out of date — %d revisions and it has not "
                    "settled. Something about this Document does not match what was asked for."
                    % (revisions - 1),
                    outcome["tried"], applied,
                )
            steps = plan(intent, tools.call("snapshot"), llm, events,
                         feedback=outcome["obsolete"])
            events.emit(PLAN_REVISED, steps=list(steps), reason=outcome["obsolete"])
            continue

        applied += outcome["applied"]
        render = outcome["render"] or render
        objects = outcome["objects"] or objects

        verdict, reason = review(intent, render, objects, llm, events)
        events.emit(REVIEW_VERDICT, verdict=verdict, reason=reason)

        if verdict == "pass":
            return LoopResult(ok=True, verdict=verdict, reason=reason,
                              steps_applied=applied, render=render)

        # A Review cycle is a rejection that sent work back to Plan. Spending
        # the budget means the next rejection has nowhere left to go.
        rejections += 1
        if rejections > budgets.review_cycles:
            return _block(
                events,
                "Review rejected the result %d times; the budget of %d Review cycles per Plan "
                "is spent." % (rejections, budgets.review_cycles),
                outcome["tried"] + [{"review": reason}], applied,
            )

        revisions += 1
        steps = plan(intent, tools.call("snapshot"), llm, events, feedback=reason)
        events.emit(PLAN_REVISED, steps=list(steps), reason=reason)


def _work_through(steps, intent, tools, llm, events, budgets, cancelled):
    """One pass over the Plan. Returns why it stopped."""
    applied = 0
    render = None
    objects = []

    for index, step in enumerate(steps, start=1):
        if cancelled():
            raise Stopped()
        events.emit(STEP_STARTED, index=index, total=len(steps), description=step)

        attempts = []
        while True:
            if cancelled():
                raise Stopped()
            outcome = execute(step, tools, llm, events, attempts, budgets)

            if outcome.obsolete:
                return {"blocked": False, "obsolete": outcome.obsolete, "applied": applied,
                        "render": render, "objects": objects,
                        "tried": [_tried(step, a) for a in attempts]}

            arguments = {"code": outcome.code, "label": outcome.label}
            events.emit(TOOL_CALL, name="apply_step", arguments=dict(arguments))
            result = tools.call("apply_step", arguments)
            events.emit(TOOL_RESULT, name="apply_step", result=result)

            if result.get("status") == "applied":
                applied += 1
                render = result.get("render") or render
                objects = result.get("objects") or objects
                events.emit(APPLIED, step=index, label=outcome.label,
                            created=result.get("created", []))
                if result.get("render"):
                    events.emit(RENDER, path=result["render"], step=index)
                break

            traceback = result.get("traceback", "The Step failed without saying why.")
            attempts.append(Attempt(code=outcome.code, traceback=traceback))
            events.emit(PREFLIGHT_FAILED, step=index, attempt=len(attempts),
                        traceback=traceback)

            if len(attempts) >= budgets.attempts_per_step:
                return {
                    "blocked": True,
                    "reason": "Step %d of %d (%s) failed %d times; the attempt budget for a "
                              "Step is spent." % (index, len(steps), step, len(attempts)),
                    "tried": [_tried(step, a) for a in attempts],
                    "obsolete": None, "applied": applied, "render": render, "objects": objects,
                }

    return {"blocked": False, "obsolete": None, "applied": applied,
            "render": render, "objects": objects, "tried": []}


def _tried(step, attempt):
    return {"step": step, "code": attempt.code, "traceback": attempt.traceback}


def _block(events, reason, tried, applied):
    events.emit(BLOCKED, reason=reason, tried=list(tried))
    return LoopResult(ok=False, blocked=True, reason=reason, tried=list(tried),
                      steps_applied=applied)
