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
    ANSWER, APPLIED, BLOCKED, ERROR, PLAN, PLAN_REVISED, PREFLIGHT_FAILED,
    QUESTION, RENDER, REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL,
    TOOL_RESULT, EventStream,
)
from core.freecad_tools import TRAPS
from core.llm import REASONING, ProviderRefused
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
    # One ceiling over all three causes of a replan (see `_Revisions`): separate
    # budgets would let the three cycle into each other and would raise the
    # worst-case number of Plan calls in a run.
    revisions: int = 3
    looks_per_step: int = 2
    # A model that only ever asks would never plan. After this many rounds Plan
    # is told to choose for itself and say what it chose (FCC-25).
    questions: int = 2


class _Revisions:
    """
    How many times the Plan has been rewritten, and why (FCC-22).

    Three unrelated things replan: the user amends at the Plan Gate, Execute
    finds a Step out of date, and Review rejects the result. They share one
    ceiling, which is deliberate — but they used to share one *counter* too, so
    whichever branch spent the last unit was reported as the cause of all of
    them, and a run stopped by two amendments told the user Execute kept
    finding the Plan out of date.
    """

    AMENDED = "amended"
    OBSOLETE = "obsolete"
    REJECTED = "rejected"

    _SAID = (
        (AMENDED, "you amended the Plan %s"),
        (OBSOLETE, "Execute found the Plan out of date %s"),
        (REJECTED, "Review rejected the result %s"),
    )

    def __init__(self):
        self._spent = {self.AMENDED: 0, self.OBSOLETE: 0, self.REJECTED: 0}

    def spend(self, cause):
        self._spent[cause] += 1

    def exhausted(self, budgets) -> bool:
        return sum(self._spent.values()) >= budgets.revisions

    def _causes_spent(self) -> list:
        return [cause for cause, _ in self._SAID if self._spent[cause]]

    def breakdown(self) -> str:
        """What the budget actually went on, in the order the causes are listed."""
        said = [text % ("%d time%s" % (self._spent[cause], "" if self._spent[cause] == 1 else "s"))
                for cause, text in self._SAID if self._spent[cause]]
        if not said:
            return "nothing"
        if len(said) == 1:
            return said[0]
        return ", ".join(said[:-1]) + " and " + said[-1]

    def block_reason(self, budgets, cause: str, refused: str) -> str:
        reason = ("The Plan has been rewritten %d times, which is the whole budget — %s."
                  % (budgets.revisions, self.breakdown()))
        # Where one cause spent the lot, the breakdown has already named it.
        if self._causes_spent() != [cause]:
            reason += " " + refused
        if self._spent[self.OBSOLETE] or cause == self.OBSOLETE:
            reason += (" Execute finding the Plan out of date means something about this "
                       "Document does not match what was asked for.")
        return reason


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


DECIDE_FOR_ME = ""

_DECIDE = ("The user is not answering. Choose sensible values yourself, and write every "
           "value you chose into the prose of the Step that uses it, so a wrong choice is "
           "correctable at the Plan Gate.")


def nobody_to_ask(question):
    """
    The answerer when nothing is wired up to ask a person.

    Level 1, Level 2 and headless runs use this: it declines, which sends Plan
    down the decide-for-yourself-and-say-so path rather than stalling a run
    nobody is watching.
    """
    return DECIDE_FOR_ME


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

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "prompts")

_TRAPS_PLACEHOLDER = "{{traps}}"


def prompt(phase):
    """
    A phase's system prompt, read from `config/prompts/<phase>.md`.

    Read per turn rather than at import: the addon installs as a plain source
    tree, so editing a prompt file takes effect on the next run. A missing file
    raises — a silently empty system prompt is the worse failure.

    The traps are the one thing not in the file. They are knowledge about a
    specific FreeCAD build, not copy to tune, so they stay in code and land at
    the placeholder the file chooses.
    """
    with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
        body = handle.read().strip()
    return body.replace(_TRAPS_PLACEHOLDER, "\n".join("- " + trap for trap in TRAPS))


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


@dataclass
class Plan:
    """
    The Steps, and how the drawing they came from was read.

    `reading` is empty when nothing was attached — there is nothing to read —
    and the Plan Gate then looks exactly as it did before there was a reading
    at all.
    """

    steps: list
    reading: str = ""
    # What Plan must know before it can plan at all. Set *instead of* steps,
    # never beside them — a question is not a Plan with a caveat (FCC-25).
    question: str = ""


def plan(intent, snapshot, llm, events, feedback=None, attachments=(), answers=()):
    """
    Produce an ordered list of Steps from the Intent and a Snapshot.

    `attachments` are the files the user showed with the Intent. They ride as
    image blocks beside the request, the same shape Review gets its render in;
    with none, the request is the plain string it always was.

    Plan is the only phase that sees the picture, so it is the only one that
    can say how it was read. That reading is shown at the Plan Gate and carried
    as text from there — a misreading is correctable before geometry exists
    rather than after (FCC-47).
    """
    request = {"intent": intent, "document": snapshot}
    if feedback:
        request["what_went_wrong_last_time"] = feedback
    if answers:
        request["you_asked_and_the_user_answered"] = [
            {"question": question, "answer": answer} for question, answer in answers]

    body = json.dumps(request, indent=2, default=str)
    pictures = [picture for picture in map(_as_image, attachments) if picture]
    content = [{"type": "text", "text": body}] + pictures if pictures else body

    reply = _ask(llm, events, PLAN_PHASE, prompt(PLAN_PHASE), content)
    steps = [str(step) for step in reply.get("steps", []) if str(step).strip()]
    question = str(reply.get("question") or "").strip()
    if not steps and question:
        return Plan(steps=[], reading=_reading_of(reply), question=question)
    if not steps:
        raise PhaseError("Plan produced no Steps for this Intent.")
    return Plan(steps=steps, reading=_reading_of(reply))


def _reading_of(reply):
    """
    The reading as text, however the model chose to shape it.

    Asked for as prose, but a model that answers a question about dimensions
    with an object is being reasonable rather than wrong, and dropping that
    would lose the whole point of having asked.
    """
    reading = reply.get("reading")
    if not reading:
        return ""
    if isinstance(reading, str):
        return reading.strip()
    return json.dumps(reading, indent=2, default=str)


@dataclass
class Attempt:
    code: str
    traceback: str


@dataclass
class ExecuteOutcome:
    code: str = None
    label: str = "Copilot Step"
    obsolete: str = None


def execute(step, tools, llm, events, attempts, budgets=None, reading=""):
    """
    Write the code for one Step, after asking the Document whatever it needs.

    Execute is the phase that hallucinates API, and it was the one phase that
    could not ask: it replied with code in one shot while `describe_object` and
    `supported_types` sat in the registry with no caller. It may now spend a
    bounded number of *looks* on the read-only Tools first (ADR-0009).

    `attempts` are the failures **for this Step only**. A Step is not told what
    went wrong elsewhere in the Plan; that is noise it cannot act on.

    `reading` is Plan's reading of the drawing, as text. Execute stays on text
    deliberately: it is called per Step and again per look, so the picture would
    multiply across dozens of calls, and a Step concerns one feature for which
    the rest of the drawing is noise.
    """
    budgets = budgets or Budgets()
    request = {"step": step, "document": tools.call("snapshot")}
    if reading:
        request["reading_of_the_drawing"] = reading
    if attempts:
        request["your_failed_attempts_at_this_step"] = [
            {"code": a.code, "traceback": a.traceback} for a in attempts
        ]

    looked = []
    for remaining in range(budgets.looks_per_step, -1, -1):
        request["tools_you_may_look_with"] = _lookable(tools) if remaining else []
        if not remaining:
            request["this_is_your_last_turn"] = (
                "You have no looks left. Reply with code, or with an obsolete reason if the "
                "Step can no longer be done. A look now is a failed attempt."
            )
        if looked:
            request["what_you_looked_at"] = looked

        reply = _ask(llm, events, EXECUTE_PHASE, prompt(EXECUTE_PHASE),
                     json.dumps(request, indent=2, default=str))

        if reply.get("obsolete"):
            return ExecuteOutcome(obsolete=str(reply["obsolete"]))
        if reply.get("code"):
            return ExecuteOutcome(code=str(reply["code"]),
                                  label=str(reply.get("label") or "Copilot Step"))
        if not reply.get("look") or not remaining:
            break
        looked.append(_look(tools, reply["look"], events))

    return ExecuteOutcome()


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


def review(intent, render, objects, llm, events, attachments=()):
    """
    Judge the outcome against the Intent.

    Given the Intent, the render, the final Document state, and whatever the
    user showed with the Intent. Deliberately not given the Plan, the code, or
    Execute's reasoning — an independent judgement of how it turned out, not of
    how it was produced.

    Without the attachments this compared a render against a sentence, so "does
    this resemble what I was shown" was not a question it could ask (FCC-47).
    """
    request = {"intent": intent, "objects_that_now_exist": objects}
    content = [{"type": "text", "text": json.dumps(request, indent=2, default=str)}]

    picture = _as_image(render)
    if picture:
        content.append(picture)
    else:
        content[0]["text"] += "\n\nNo render was produced, so judge on the objects alone."

    shown = [drawing for drawing in map(_as_image, attachments) if drawing]
    if shown:
        content[0]["text"] += (
            "\n\nThe last %d image%s is what the user showed with the Intent, not the "
            "result. Judge whether the render resembles it."
            % (len(shown), "s are" if len(shown) > 1 else " is")
        )
        content.extend(shown)

    reply = _ask(llm, events, REVIEW_PHASE, prompt(REVIEW_PHASE), content)

    verdict = str(reply.get("verdict", "")).lower()
    if verdict not in ("pass", "fail"):
        raise PhaseError("Review replied with an unusable verdict: %r" % reply.get("verdict"))
    return verdict, str(reply.get("reason", ""))


# ── the loop ──────────────────────────────────────────────────────────────────


class Stopped(Exception):
    """The user stopped the run. Not a failure — a decision."""


def run(intent, *, llm, tools, events=None, budgets=None, cancelled=None, attachments=(),
        gate=auto_approve, ask=nobody_to_ask) -> LoopResult:
    """
    Turn an Intent into changes to the Document.

    Everything it needs is an argument: the LLM client, the Tool registry, the
    event stream to narrate into, the budgets, `cancelled` — a callable the loop
    asks between Steps and attempts so the user is never a passenger — and
    `gate`, the Plan Gate, which must approve before any Step runs. Nothing is
    global, so two runs cannot see each other and a sub-agent is a narrower
    Intent.

    `attachments` are files the user showed with the Intent; Plan sees them.
    """
    events = events if events is not None else EventStream()
    budgets = budgets or Budgets()
    progress = {"applied": 0, "tried": []}

    try:
        return _run(intent, llm, tools, events, budgets, cancelled or (lambda: False), gate,
                    progress, attachments, ask)
    except Stopped:
        return _block(events, "Stopped by you. Everything applied so far is still in the "
                              "Document; undo it Step by Step if you want it gone.", [],
                      progress["applied"])
    except ProviderRefused as exc:
        return _block(events, str(exc), progress["tried"], progress["applied"], said=exc.said)
    except PhaseError as exc:
        events.emit(ERROR, message=str(exc))
        return LoopResult(ok=False, reason=str(exc))
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        return LoopResult(ok=False, reason=str(exc))


def _plan_once_it_knows_enough(intent, snapshot, llm, events, budgets, attachments,
                               ask, cancelled):
    """
    Plan, letting it ask for what the Intent did not say (FCC-25).

    "модельку шестерні" names no module, no tooth count, no thickness and no
    bore. Planning anyway means inventing them, and an invented value is not
    visible anywhere the user could correct it — it turns up in the geometry.
    So Plan may answer with a question instead of Steps, and the run waits.

    A question is not an approval: the Plan Gate is still the single point of
    approval, and it still sees every Step. `budgets.questions` bounds this,
    because a model that only asks would never plan.
    """
    answers = []
    told_to_choose = False
    while True:
        if cancelled():
            raise Stopped()
        planned = plan(intent, snapshot, llm, events, attachments=attachments, answers=answers)
        if not planned.question:
            return planned

        events.emit(QUESTION, text=planned.question)
        if told_to_choose:
            raise PhaseError("Plan asked again after being told to choose for itself, so "
                             "there is no Plan and no way to get one.")
        if len(answers) >= budgets.questions:
            # Asked enough. Not an error — the run goes on with Plan choosing
            # and saying, in each Step, what it chose.
            answers.append((planned.question, _DECIDE))
            events.emit(ANSWER, text=_DECIDE, by="budget")
            told_to_choose = True
            continue

        answer = str(ask(planned.question) or DECIDE_FOR_ME).strip()
        if not answer:
            told_to_choose = True
        answers.append((planned.question, answer or _DECIDE))
        events.emit(ANSWER, text=answer, by="user" if answer else "nobody")


def _run(intent, llm, tools, events, budgets, cancelled, gate, progress, attachments, ask):
    snapshot = tools.call("snapshot")
    planned = _plan_once_it_knows_enough(intent, snapshot, llm, events, budgets,
                                         attachments, ask, cancelled)
    steps = planned.steps
    events.emit(PLAN, steps=list(steps), reading=planned.reading)

    rejections = 0
    revisions = _Revisions()

    while True:
        approval = gate(list(steps)) or Approval(APPROVE)
        if approval.decision == APPROVE:
            break
        if approval.decision == REJECT:
            return _block(events, "You rejected the Plan, so nothing was applied.", [], 0)

        if revisions.exhausted(budgets):
            return _block(events, revisions.block_reason(
                budgets, _Revisions.AMENDED,
                "Your latest amendment was not planned."), [], 0)
        revisions.spend(_Revisions.AMENDED)
        planned = plan(intent, tools.call("snapshot"), llm, events,
                       feedback=approval.feedback, attachments=attachments)
        steps = planned.steps
        events.emit(PLAN_REVISED, steps=list(steps), reading=planned.reading,
                    reason="You asked for: " + approval.feedback)

    render = None
    objects = []

    while True:
        outcome = _work_through(steps, intent, tools, llm, events, budgets, cancelled,
                                progress, planned.reading)
        applied = progress["applied"]

        if outcome["blocked"]:
            return _block(events, outcome["reason"], outcome["tried"], applied)

        if outcome["obsolete"]:
            if revisions.exhausted(budgets):
                return _block(
                    events,
                    revisions.block_reason(
                        budgets, _Revisions.OBSOLETE,
                        "Execute has found it out of date again."),
                    outcome["tried"], applied,
                )
            revisions.spend(_Revisions.OBSOLETE)
            planned = plan(intent, tools.call("snapshot"), llm, events,
                           feedback=outcome["obsolete"], attachments=attachments)
            steps = planned.steps
            events.emit(PLAN_REVISED, steps=list(steps), reading=planned.reading,
                        reason=outcome["obsolete"])
            continue

        render = outcome["render"] or render
        objects = outcome["objects"] or objects

        verdict, reason = review(intent, render, objects, llm, events, attachments)
        events.emit(REVIEW_VERDICT, verdict=verdict, reason=reason)

        if verdict == "pass":
            return LoopResult(ok=True, verdict=verdict, reason=reason,
                              steps_applied=applied, render=render)

        rejections += 1
        if rejections > budgets.review_cycles:
            return _block(
                events,
                "Review rejected the result %d times; the budget of %d Review cycles per Plan "
                "is spent." % (rejections, budgets.review_cycles),
                outcome["tried"] + [{"review": reason}], applied,
            )

        # Bounded by review_cycles above, but it spends the shared revision
        # budget as well — invisibly, until FCC-22.
        if revisions.exhausted(budgets):
            return _block(
                events,
                revisions.block_reason(
                    budgets, _Revisions.REJECTED,
                    "Review has rejected the result again."),
                outcome["tried"] + [{"review": reason}], applied,
            )
        revisions.spend(_Revisions.REJECTED)
        planned = plan(intent, tools.call("snapshot"), llm, events,
                       feedback=reason, attachments=attachments)
        steps = planned.steps
        events.emit(PLAN_REVISED, steps=list(steps), reading=planned.reading, reason=reason)


def _work_through(steps, intent, tools, llm, events, budgets, cancelled, progress, reading=""):
    """One pass over the Plan. Returns why it stopped."""
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
            outcome = execute(step, tools, llm, events, attempts, budgets, reading)

            if outcome.obsolete:
                return {"blocked": False, "obsolete": outcome.obsolete,
                        "render": render, "objects": objects,
                        "tried": [_tried(step, a) for a in attempts]}

            if outcome.code:
                arguments = {"code": outcome.code, "label": outcome.label}
                events.emit(TOOL_CALL, name="apply_step", arguments=dict(arguments))
                result = tools.call("apply_step", arguments)
                events.emit(TOOL_RESULT, name="apply_step", result=result)
            else:
                result = {"status": "failed", "traceback":
                          "You had no looks left and replied with neither code nor an "
                          "obsolete reason. Write the code for this Step."}

            if result.get("status") == "applied":
                progress["applied"] += 1
                render = result.get("render") or render
                objects = result.get("objects") or objects
                events.emit(APPLIED, step=index, label=outcome.label,
                            created=result.get("created", []))
                if result.get("render"):
                    events.emit(RENDER, path=result["render"], step=index)
                break

            traceback = result.get("traceback", "The Step failed without saying why.")
            attempts.append(Attempt(code=outcome.code or "", traceback=traceback))
            progress["tried"] = [_tried(step, a) for a in attempts]
            events.emit(PREFLIGHT_FAILED, step=index, attempt=len(attempts),
                        traceback=traceback)

            if len(attempts) >= budgets.attempts_per_step:
                return {
                    "blocked": True,
                    "reason": "Step %d of %d (%s) failed %d times; the attempt budget for a "
                              "Step is spent." % (index, len(steps), step, len(attempts)),
                    "tried": [_tried(step, a) for a in attempts],
                    "obsolete": None, "render": render, "objects": objects,
                }

    return {"blocked": False, "obsolete": None, "render": render, "objects": objects, "tried": []}


def _tried(step, attempt):
    return {"step": step, "code": attempt.code, "traceback": attempt.traceback}


def _block(events, reason, tried, applied, said=""):
    events.emit(BLOCKED, reason=reason, tried=list(tried), provider_said=said)
    return LoopResult(ok=False, blocked=True, reason=reason, tried=list(tried),
                      steps_applied=applied)
