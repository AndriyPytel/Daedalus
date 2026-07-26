"""
The Plan -> Execute -> Review loop.

Three phases, fixed order, deterministic routing (ADR-0003). No LLM-chosen
speaker selection: Plan produces Steps, Execute works through them one at a
time, Review judges the outcome, and Review's verdict returns to Plan — never
to Execute.

The loop is a function of `(intent, context)` over injected dependencies, so a
narrower Intent is all a future sub-agent needs. Nothing here imports FreeCAD;
the Document is reached only through the Tool registry.

Budgets, not round limits. On exhaustion the loop emits `blocked` with what was
tried and waits for the user. Silent failure and unbounded looping are both
defects, so both are bounded and both are reported.
"""
import json
import re
from dataclasses import dataclass, field
from typing import List

from core.events import (
    APPLIED, BLOCKED, ERROR, PLAN, PLAN_REVISED, PREFLIGHT_FAILED, RENDER,
    REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, EventStream,
)
from core.freecad_tools import TRAPS
from core.llm import REASONING

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
    # Not in the spec's two budgets, but Execute declaring Step after Step
    # obsolete is otherwise an unbounded loop, and that is a defect too.
    revisions: int = 3


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

Break the Intent into the smallest ordered list of Steps that achieves it. Each
Step is one unit of change, small enough to be attempted, judged and undone on
its own. Describe what the Step should achieve, not the code for it.

Reply with JSON only: {"steps": ["...", "..."]}

Traps in this build:
""" + _TRAPS

EXECUTE_SYSTEM = """You write the Python for one Step against a FreeCAD 1.0 Document.

`doc` and `App` are in scope. The code must be a pure function of the Document:
no Gui.*, no reading the live selection, and no literal element names such as
Edge12 — compute references from the geometry at execution time instead.

Reply with JSON only, either:
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


def execute(step, snapshot, llm, events, attempts):
    """
    Write the code for one Step.

    `attempts` are the failures **for this Step only**. A Step is not told what
    went wrong elsewhere in the Plan; that is noise it cannot act on.
    """
    request = {"step": step, "document": snapshot}
    if attempts:
        request["your_failed_attempts_at_this_step"] = [
            {"code": a.code, "traceback": a.traceback} for a in attempts
        ]

    reply = _ask(llm, events, EXECUTE_PHASE, EXECUTE_SYSTEM,
                 json.dumps(request, indent=2, default=str))

    if reply.get("obsolete"):
        return ExecuteOutcome(obsolete=str(reply["obsolete"]))
    code = reply.get("code")
    if not code:
        raise PhaseError("Execute replied with neither code nor an obsolete reason.")
    return ExecuteOutcome(code=str(code), label=str(reply.get("label") or "Copilot Step"))


def review(intent, render, objects, llm, events):
    """
    Judge the outcome against the Intent.

    Given the Intent, the render and the final Document state. Deliberately not
    given the Plan, the code, or Execute's reasoning — an independent judgement
    of how it turned out, not of how it was produced.
    """
    request = {"intent": intent, "render": render, "objects_that_now_exist": objects}
    reply = _ask(llm, events, REVIEW_PHASE, REVIEW_SYSTEM,
                 json.dumps(request, indent=2, default=str))

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
            # A fresh Snapshot every attempt: the user may have edited the model
            # by hand since the last one, and the Document is the source of truth.
            outcome = execute(step, tools.call("snapshot"), llm, events, attempts)

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
