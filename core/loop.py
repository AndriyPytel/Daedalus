"""
The Design -> Plan -> Execute -> Assertion -> Review loop.

Fixed order, deterministic routing (ADR-0003). No LLM-chosen speaker
selection: Design says what the part is and has nothing to build with it
(ADR-0019), Plan produces Steps and the list they have to satisfy, Execute
writes the code for every Step of the Plan in one call and the loop applies
them one at a time (ADR-0021), Assertion measures the finished Document against
that list once the run is over (ADR-0017), Review judges the outcome, and a
failure from either of the last two returns to Plan — never to Execute. Within
its turn Execute may spend a bounded number of read-only looks at the Document
before it writes code (ADR-0009); that is one phase reading its input, not a
negotiation between phases.

There is one edge back from Execute to Plan and it carries no geometry: a Step
that is out of date, or that cannot be built after its attempts are spent, goes
back to Plan to be written differently (ADR-0013). Still deterministic — the
loop decides when it fires, not the model — and Execute's only two moves stay
"do exactly this" and "hand back".

The loop is a function of `(intent, context)` over injected dependencies, so a
narrower Intent is all a future sub-agent needs. Nothing here imports FreeCAD;
the Document is reached only through the Tool registry.

Budgets, not round limits. On exhaustion the loop emits `blocked` with what was
tried and waits for the user. Silent failure and unbounded looping are both
defects, so both are bounded and both are reported.
"""
import base64
import json
import mimetypes
import os
import re
from dataclasses import dataclass, field
from typing import List

from core.design import entries, merge, render
from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, DESIGN, ERROR, PLAN,
    PLAN_REVISED, PREFLIGHT_FAILED, QUESTION, REVIEW_VERDICT, STEP_STARTED,
    THOUGHT, TOOL_CALL, TOOL_RESULT, EventStream,
)
from core.freecad_tools import TRAPS
from core.llm import REASONING, ProviderRefused
from core.tools import ToolError

DESIGN_PHASE = "design"
PLAN_PHASE = "plan"
EXECUTE_PHASE = "execute"
ASSERTION_PHASE = "assertion"
REVIEW_PHASE = "review"

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# Tools the loop drives itself. None of them is a Look: one writes to the
# Document, one moves the user's camera, one belongs to a phase of its own.
NOT_A_LOOK = ("apply_step", "review_views", "check_assertions")

# In the order `capture_review_views` writes them, for Review's own prose.
_VIEW_NAMES = ("isometric", "the front", "above", "the right")


class PhaseError(Exception):
    """A phase gave back something the loop cannot act on."""


@dataclass
class Budgets:
    attempts_per_step: int = 3
    review_cycles: int = 2
    # Without this, Execute declaring Step after Step obsolete never terminates.
    # One ceiling over all six causes of a replan (see `_Revisions`): separate
    # budgets would let them cycle into each other and would raise the
    # worst-case number of Plan calls in a run. This is the floor under
    # `revisions_ceiling`, which scales the allowance with the Plan's length.
    revisions: int = 3
    looks_per_turn: int = 2
    # A model that only ever asks would never plan. After this many rounds Plan
    # is told to choose for itself and say what it chose (FCC-25). It counts
    # what a *person* was asked; the drawing answering is not being asked.
    questions: int = 2
    # How many of Plan's questions may be put back to the drawing before they
    # are put to the user (FCC-90). A re-read is still a provider call, and
    # without a ceiling a Plan that keeps asking and a drawing that keeps
    # answering never terminates.
    rereads: int = 3


def revisions_ceiling(budgets, steps) -> int:
    """
    How many replans this Plan is allowed, from how long it is (FCC-61).

    A four-Step Plan and a forty-Step Plan cannot have the same allowance: a
    long Plan has more places to hand back from, and every Execute -> Plan
    handback spends one. Still a single ceiling over all six causes, for the
    reason recorded on `Budgets`; only its size is computed.
    """
    return max(budgets.revisions, len(steps) // 5)


class _Revisions:
    """
    How many times the Plan has been rewritten, and why (FCC-22).

    Six unrelated things replan: the Plan leaves part of the description
    unplanned, the user amends at the Plan Gate, Execute finds a Step out of
    date, Execute cannot build one, the acceptance checks fail, and Review
    rejects the result. They share one ceiling, which is deliberate — but they
    used to share one *counter* too, so whichever branch spent the last unit was
    reported as the cause of all of them, and a run stopped by two amendments
    told the user Execute kept finding the Plan out of date.
    """

    UNCOVERED = "uncovered"
    AMENDED = "amended"
    OBSOLETE = "obsolete"
    UNBUILDABLE = "unbuildable"
    ASSERTED = "asserted"
    REJECTED = "rejected"

    _SAID = (
        (UNCOVERED, "the Plan left part of the description unplanned %s"),
        (AMENDED, "you amended the Plan %s"),
        (OBSOLETE, "Execute found the Plan out of date %s"),
        (UNBUILDABLE, "Execute could not build a Step %s"),
        (ASSERTED, "the acceptance checks failed %s"),
        (REJECTED, "Review rejected the result %s"),
    )

    def __init__(self):
        self._spent = {cause: 0 for cause, _ in self._SAID}

    def spend(self, cause):
        self._spent[cause] += 1

    def exhausted(self, budgets, steps) -> bool:
        return sum(self._spent.values()) >= revisions_ceiling(budgets, steps)

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

    def block_reason(self, budgets, steps, cause: str, refused: str) -> str:
        if not self._causes_spent():
            # A ceiling of nought: the run may not replan at all.
            return "The Plan may not be rewritten in this run. " + refused
        reason = ("The Plan has been rewritten %d times, which is the whole budget — %s."
                  % (revisions_ceiling(budgets, steps), self.breakdown()))
        # Where one cause spent the lot, the breakdown has already named it.
        if self._causes_spent() != [cause]:
            reason += " " + refused
        if self._spent[self.OBSOLETE] or cause == self.OBSOLETE:
            reason += (" Execute finding the Plan out of date means something about this "
                       "Document does not match what was asked for.")
        if self._spent[self.UNBUILDABLE] or cause == self.UNBUILDABLE:
            reason += (" A Step Execute cannot build is a Step Plan has to write "
                       "differently; the tracebacks are in what was tried.")
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


# What to do when the phases that judge the result say no, and a revision may
# wipe what has already been built. Three answers: stop with what is there, or
# replan — with or without leave to delete it. The last is the one the Plan
# Gate has no word for, since REJECT stops a run but does not licence a
# restart (FCC-77).
#
# "Accepted" and "stopped without accepting" were two answers until they were
# found to be one: both end the run and both keep what was built, and which of
# them happened is already on the stream — the `assertion` and `review_verdict`
# events say whether anything passed.
ACCEPT = "accept"           # stop here; what was built stays, and it will do
REVISE = "revise"           # feedback, a new Plan, a new Plan Gate
START_OVER = "start_over"   # revise, and it may delete what was built

DECISIONS = (ACCEPT, REVISE, START_OVER)

_MAY_START_AGAIN = (
    "\n\nThe user has decided you may start again: you may delete what was built and "
    "plan the part from nothing."
)


def revise_by_default(failure):
    """
    The decider when nothing is wired up to ask.

    Level 1, Level 2 and headless runs use this, and it is the behaviour the
    loop had before there was a question: no automatic acceptance of a failed
    result, and no automatic licence to wipe the model.
    """
    return REVISE


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
    Read a file as the content block the provider takes for it.

    A path is meaningless to the model — it cannot open files. Returns None if
    there is nothing to show, which a sketch-only Step produces.

    The mime comes off the file rather than being stamped `image/png` on
    everything, which is how a JPEG went out labelled as a PNG (FCC-89). A PDF
    is a `file` block rather than an `image_url` one — the same path with a
    different envelope, and no page rendering on our side. The renders we write
    ourselves are PNGs, so that path is unchanged.
    """
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")

    mime, _ = mimetypes.guess_type(path)
    mime = mime or "application/octet-stream"
    if mime == "application/pdf":
        return {"type": "file",
                "file": {"file_data": "data:%s;base64,%s" % (mime, encoded)}}
    return {"type": "image_url",
            "image_url": {"url": "data:%s;base64,%s" % (mime, encoded)}}


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


def _pictures(attachments):
    return [picture for picture in map(_as_image, attachments) if picture]


def design(body, llm, events, attachments=()):
    """
    One Design turn: what it adds to the part, and what it needs a person for.

    Every turn of the phase comes through here — the first pass at the sheet, a
    question put back to it, a person's answer being written in — because every
    one of them ends in the same place, a merge into the same model. Merge is
    the only way the description grows, so there is never an answer standing
    beside it to be adjudicated (ADR-0019). What differs between the turns is
    the body, and that is all that differs.

    A context of its own, with nothing to build (ADR-0018, kept): Design and
    Plan used to be one call, and the second pressure spoilt the first — a value
    was invented not because the model saw badly but because it needed something
    to plan with. The sheet rides along whenever there is one, because the phase
    that owns the description is the only phase that ever sees the picture.

    Hands back what to merge and what Design wants asked. A reply that is not a
    model grows the part by nothing, which is the same case as a sheet that does
    not answer: the run carries on with what it already had rather than losing
    it, the way an acceptance list nobody wrote is not fatal either.
    """
    reply = _ask(llm, events, DESIGN_PHASE, prompt(DESIGN_PHASE),
                 [{"type": "text", "text": json.dumps(body, indent=2, default=str)}]
                 + _pictures(attachments))
    the_part = reply.get("design")
    return (the_part if isinstance(the_part, dict) else {},
            str(reply.get("question") or "").strip())


@dataclass
class Plan:
    """The Steps, and what the finished part has to satisfy."""

    steps: list
    # What Plan must know before it can plan at all. Set *instead of* steps,
    # never beside them — a question is not a Plan with a caveat (FCC-25).
    question: str = ""
    # What has to be true of the finished part, once for the whole Plan rather
    # than per Step: the checks run when the run is over, against the Document
    # that was asked for, not against an intermediate state (FCC-75). Derived
    # from the description rather than written here (ADR-0020).
    acceptance: list = field(default_factory=list)
    # Every entry of the description the Plan's Steps cite between them, flat:
    # the check is whether an entry is accounted for by *any* Step, so which
    # Step accounted for it is a thing the Gate shows and the loop does not
    # need (FCC-94).
    satisfies: list = field(default_factory=list)


def plan(intent, snapshot, llm, events, feedback=None, the_part=None, unanswered=()):
    """
    Produce an ordered list of Steps from the Intent and a Snapshot.

    Plan never sees the picture. It is handed the part Design describes instead,
    under one key and in the words the user approved at the Plan Gate, and told
    plainly that is what it is (ADR-0019).

    One key, because two were how the 100 mm was lost: the description and the
    answers to Plan's own questions used to arrive separately and Plan
    consistently preferred the newer, so a value read off SECTION B-B vanished
    without ever being overruled. Every answer is now merged into the
    description before Plan sees it, and there is nothing left to adjudicate.

    Beside the rendering, the same description entry by entry, addressed. That
    is the vocabulary a Step cites from and it is the Acceptance the finished
    part is checked against, and it is one list rather than two so that what
    Plan is asked to account for and what the run measures cannot differ
    (ADR-0020).
    """
    request = {"intent": intent, "document": snapshot}
    if the_part:
        request["what_the_part_is"] = render(the_part)
        request["what_the_plan_must_satisfy"] = _acceptance_of(the_part)
    if feedback:
        request["what_went_wrong_last_time"] = feedback
    if unanswered:
        # Not a value and not a source — an instruction. Nobody answered, so
        # Plan chooses and says in the Step what it chose (FCC-25).
        request["nobody_answered"] = {"the_questions": list(unanswered),
                                      "what_to_do": _DECIDE}

    reply = _ask(llm, events, PLAN_PHASE, prompt(PLAN_PHASE),
                 json.dumps(request, indent=2, default=str))
    steps, satisfies = _steps_of(reply.get("steps", []))
    question = str(reply.get("question") or "").strip()
    if not steps and question:
        return Plan(steps=[], question=question)
    if not steps:
        raise PhaseError("Plan produced no Steps for this Intent.")
    return Plan(steps=steps, satisfies=satisfies, acceptance=_acceptance_of(the_part))


def _steps_of(planned):
    """
    The prose of each Step, and every entry of the description the Plan cites.

    A Step is `{"do": …, "satisfies": [...]}`, or the bare string it has always
    been. What a Step satisfies goes into its own prose rather than beside it,
    because the Plan Gate shows prose: a reader who cannot tell from the Gate
    which part of the part a Step is for cannot tell that one physical cavity
    has been planned three times, which is what Steps 4, 5 and 6 of the recorded
    run were (FCC-94).

    A Step citing nothing is not refused here. The rule is coverage over the
    whole Plan — no entry may be left to no Step — and a Plan is the only thing
    that can be judged against it; a Step may legitimately carry work no entry
    of the description names.
    """
    steps, satisfies = [], []
    for step in planned:
        if isinstance(step, dict):
            prose = str(step.get("do") or step.get("step") or "").strip()
            cited = [_as_written(entry) for entry in (step.get("satisfies") or ())
                     if _as_written(entry)]
        else:
            prose, cited = str(step).strip(), []
        if not prose:
            continue
        steps.append(prose + (" (satisfies: %s)" % ", ".join(cited) if cited else ""))
        satisfies.extend(cited)
    return steps, satisfies


def _as_written(entry):
    """An address as one line, because it is matched by the words it is written in."""
    return " ".join(str(entry).split())


def _acceptance_of(the_part):
    """
    What the finished part has to satisfy: the description's own entries.

    Plan used to write this list, and between the sheet and the assert there
    were two rewrites. In the run that named the defect both lied — every entry
    said `(read)` although the values came out of a re-read answering questions
    the first pass had marked not defined, and the depth entry carried 50 mm, so
    Assertion would have measured 50 mm on the built part and reported a pass on
    a part the drawing never showed. Derived, there is nothing left to rewrite:
    each entry *is* a value of the model, with the datum it sits under and the
    flag it was recorded with (ADR-0020).

    Empty is not an error and never was. A description holding nothing means no
    Assertion phase at all, which is exactly what a Plan that wrote no list
    produced (FCC-75), and it is what a run with no drawing and no answers gets.

    An assumed value is a criterion too, and marked assumed. Leaving it out
    would hide from the checks the kind of value most likely to be wrong, and
    from the user at the Gate the fact that it is theirs to correct.
    """
    return [entry["says"] for entry in entries(the_part)]


def _unconsumed(the_part, satisfies):
    """
    The entries of the description that no Step of the Plan accounts for.

    The mechanical catch for the defect the epic is named after: the revised
    Plan of the recorded run dropped the 100 mm front to rear and no rule,
    prompt or judgement noticed. Checked over addresses and not over prose, so
    it is a set difference rather than a comparison of sentences.

    Coverage, not exclusivity. One entry may legitimately feed several Steps —
    an overall height feeds both the base solid and a through hole's depth — but
    no entry may feed none.

    Only what was `read`. An assumed value is still a criterion and is still
    measured, but a Plan may not be refused for failing to build a number
    nobody supplied.

    Matched as written, and never by prefix of an address — a Plan allowed to
    cite `features` and cover everything beneath it would be the check defeating
    itself. What is allowed is the value trailing the address, because the list
    Plan cites from carries both on one line (`envelope → length: 150 mm
    (read)`), and a Plan that copies the line it was handed means the entry that
    line is. Refusing it would make the check fail on the one Plan that did
    exactly as it was told.
    """
    cited = {_as_written(entry) for entry in satisfies}
    return [entry["about"] for entry in entries(the_part)
            if entry["flag"] == "read"
            and not any(said == entry["about"] or said.startswith(entry["about"] + ":")
                        for said in cited)]


def _plan_the_rest(unconsumed) -> str:
    """What Plan is told about the entries it left to nobody."""
    return ("No Step of your Plan cited these entries of the description, so nothing in "
            "the Plan builds them:\n"
            + "\n".join("- " + entry for entry in unconsumed)
            + "\nPlan them, and name each one in the `satisfies` of the Step that "
              "satisfies it, written exactly as it is above.")


@dataclass
class Attempt:
    code: str
    traceback: str


@dataclass
class ExecuteOutcome:
    """The code for one Step, and the name its undo entry gets."""

    code: str = None
    label: str = "Copilot Step"


@dataclass
class Written:
    """
    What one Execute turn produced: the code for every Step it was asked about.

    Keyed by the Step's number in the Plan rather than by position in the
    reply. A regeneration is asked about a tail, and a reply that leaves a Step
    out has to leave a hole at that Step — shifting every Step after it onto
    the wrong code is the silent failure this ticket exists to remove (FCC-95).
    """

    steps: dict = field(default_factory=dict)
    obsolete: str = None

    def for_step(self, number) -> ExecuteOutcome:
        return self.steps.get(number) or ExecuteOutcome()


def execute(steps, tools, llm, events, start=0, attempts=(), budgets=None, as_described=""):
    """
    Write the code for every Step from `start` to the end of the Plan, in one turn.

    Execute used to be called once per Step, and that is where the run's cost
    was: 22 of roughly 29 provider calls for six applied Steps, and all fifteen
    looks were API discovery rather than geometry — not one returned a
    coordinate the code then used. Every coordinate was derived at runtime
    inside the Step's own code, off `Shape.BoundBox`, off `Shape.Faces`, off
    `base.Length.Value`. That is what makes one call possible: a Step does not
    need the Document its predecessor will leave behind, because it reads it
    when it runs (ADR-0021, superseding ADR-0009 in part).

    A regeneration is the same call with `start` at the Step that failed. Step
    N and every Step after it are written again, because the code for N+1 was
    written assuming objects Step N was going to create — regenerating N alone
    breaks at the *next* Step's Preflight, silently and one Step late. The
    Snapshot is read here, so the new code is written against the Document as
    it actually stands: the Steps before the failure applied, that one did not.

    `attempts` are the failures at the Step `start` names, and only that Step's.
    A Step that landed carries none of its history forward.

    It may still spend a bounded number of *looks* on the read-only Tools first
    (ADR-0009, kept). What is gone is the repetition: identical looks used to
    recur because nothing carried across Steps, and there are no longer
    separate Steps for them to recur between.

    `as_described` is the part Design holds, rendered. Execute stays on text
    deliberately — the whole drawing is noise against code that is written from
    the Steps and read back off the geometry.
    """
    budgets = budgets or Budgets()
    request = {"steps": [{"step": number, "do": prose}
                         for number, prose in enumerate(steps[start:], start=start + 1)],
               "document": tools.call("snapshot")}
    if as_described:
        request["what_the_part_is"] = as_described
    if attempts:
        request["your_failed_attempts_at_this_step"] = [
            {"code": a.code, "traceback": a.traceback} for a in attempts
        ]
        request["what_to_do_now"] = (
            "Step %d failed and is not in the Document; the Steps before it are, and the "
            "Document above is how they left it. Write Step %d again, and every Step after "
            "it — the code you wrote for those assumed objects Step %d never created."
            % (start + 1, start + 1, start + 1)
        )

    looked = []
    for remaining in range(budgets.looks_per_turn, -1, -1):
        request["tools_you_may_look_with"] = _lookable(tools) if remaining else []
        if not remaining:
            request["this_is_your_last_turn"] = (
                "You have no looks left. Reply with code for every Step, or with an obsolete "
                "reason if the Plan can no longer be done. A look now is a failed attempt."
            )
        if looked:
            request["what_you_looked_at"] = looked

        reply = _ask(llm, events, EXECUTE_PHASE, prompt(EXECUTE_PHASE),
                     json.dumps(request, indent=2, default=str))

        if reply.get("obsolete"):
            return Written(obsolete=str(reply["obsolete"]))
        written = _written(reply.get("steps"), start)
        if written:
            return Written(steps=written)
        if not reply.get("look") or not remaining:
            break
        looked.append(_look(tools, reply["look"], events))

    return Written()


def _written(replied, start):
    """
    The reply's Steps, by Step number, dropping anything without code.

    The number the reply carries wins over its position, and the position is
    only the fallback for a reply that numbered nothing.
    """
    written = {}
    for position, entry in enumerate(replied or (), start=start + 1):
        if not isinstance(entry, dict) or not entry.get("code"):
            continue
        number = entry.get("step")
        written[number if isinstance(number, int) else position] = ExecuteOutcome(
            code=str(entry["code"]), label=str(entry.get("label") or "Copilot Step"))
    return written


def _lookable(tools):
    """
    The Tools Execute may ask with: everything that only reads.

    `apply_step` is not among them — Execute proposes a Step, the loop decides
    when it runs, and a phase that could apply its own code would be a second,
    unbudgeted route to the Document. `review_views` is not either: it moves
    the user's camera, and letting Execute ask for a picture would be a loop
    inside a phase (ADR-0015).
    """
    return [described for described in tools.describe()
            if described["name"] not in NOT_A_LOOK]


def _emit_plan(events, kind, planned, reason=None):
    """A Plan on the stream, in one place — the panel reads both kinds alike."""
    extra = {"reason": reason} if reason is not None else {}
    events.emit(kind, steps=list(planned.steps),
                acceptance=list(planned.acceptance), **extra)


def _review_views(tools, events):
    """
    The four viewport views for Review, or nothing where there is no GUI.

    Driven by the loop, once per run, because a picture is not something a
    phase gets to ask for (ADR-0015). A build without the Tool at all — an
    older registry, a test registry — falls through to the same nothing as a
    headless one, and Review keeps the Worker's single render.
    """
    if "review_views" not in tools:
        return []
    events.emit(TOOL_CALL, name="review_views", arguments={})
    try:
        result = tools.call("review_views", {})
    except ToolError as exc:
        # Never lose a finished run over its photograph.
        events.emit(TOOL_RESULT, name="review_views", result={"error": str(exc)})
        return []
    events.emit(TOOL_RESULT, name="review_views", result=result)
    return list(result.get("views") or ())


def _look(tools, look, events):
    """Run one read-only Tool for Execute and hand back what it said."""
    name = str(look.get("tool") or "")
    arguments = look.get("arguments") or {}

    events.emit(TOOL_CALL, name=name, arguments=dict(arguments))
    if name in NOT_A_LOOK or name not in tools:
        result = {"error": "%r is not a Tool you can look with. Reply with code instead." % name}
    else:
        try:
            result = tools.call(name, arguments)
        except ToolError as exc:
            # A bad look is a thing to correct, not a reason to lose the Step.
            result = {"error": str(exc)}
    events.emit(TOOL_RESULT, name=name, result=result)

    return {"tool": name, "arguments": arguments, "result": result}


@dataclass
class Checked:
    """
    What the Assertion phase made of the finished Document.

    `failed` is a model that did not meet its acceptance list; `traceback` is
    what Plan is told. A check that could not be written is neither passed nor
    failed — `confirmed` is empty, `failed` is False, and Review judges the
    whole list alone.
    """

    failed: bool = False
    traceback: str = ""
    confirmed: list = field(default_factory=list)
    uncovered: list = field(default_factory=list)


def assertion(acceptance, described, tools, llm, events, budgets=None, cancelled=None):
    """
    Write checks for the acceptance list and run them against the result.

    A fourth context, not Review and not Preflight. Review judges and is
    deliberately not told how the result was produced (ADR-0003), so it cannot
    also author the code that checks it; Preflight is a gate in front of the
    Document on every Step, and this measures a finished one. Read-only, once
    per run.

    An `AssertionError` is the model failing its list. Any other traceback is
    the *check* failing, and it goes back to the phase that wrote it on the
    same budget a Step gets — read as a failed model it would send Plan to
    rebuild a part that is already right.
    """
    budgets = budgets or Budgets()
    request = {"acceptance": list(acceptance),
               "the_document_as_it_now_stands": described}

    cancelled = cancelled or (lambda: False)

    attempts = []
    for _ in range(budgets.attempts_per_step):
        # Asked between attempts the way Execute's are: three provider calls is
        # long enough that Stop must not wait them out.
        if cancelled():
            raise Stopped()
        if attempts:
            request["your_failed_attempts_at_the_checks"] = list(attempts)

        reply = _ask(llm, events, ASSERTION_PHASE, prompt(ASSERTION_PHASE),
                     json.dumps(request, indent=2, default=str))
        code = str(reply.get("code") or "").strip()
        uncovered = [str(entry) for entry in (reply.get("uncovered") or ())]
        if not code:
            attempts.append({"code": "", "traceback": "You replied with no code. "
                                                      "Write the checks."})
            continue

        events.emit(TOOL_CALL, name="check_assertions", arguments={"code": code})
        result = tools.call("check_assertions", {"code": code})
        events.emit(TOOL_RESULT, name="check_assertions", result=result)

        if result.get("ok"):
            confirmed = [entry for entry in acceptance if entry not in uncovered]
            checked = Checked(confirmed=confirmed, uncovered=uncovered)
            events.emit(ASSERTION, ok=True, code=code, confirmed=confirmed,
                        uncovered=uncovered, traceback="")
            return checked

        traceback = result.get("traceback") or "The checks failed without saying why."
        if _the_model_failed(traceback):
            events.emit(ASSERTION, ok=False, code=code, confirmed=[],
                        uncovered=uncovered, traceback=traceback)
            return Checked(failed=True, traceback=traceback, uncovered=uncovered)

        attempts.append({"code": code, "traceback": traceback})

    # The checks could not be written. Not a verdict on the part, and not a
    # reason to lose a finished run: Review judges the whole list instead.
    events.emit(ASSERTION, ok=None, code="", confirmed=[],
                uncovered=list(acceptance),
                traceback=attempts[-1]["traceback"] if attempts else "")
    return Checked(uncovered=list(acceptance))


_MODEL_IS_INVALID = "recompute left these objects in error"


def _the_model_failed(traceback):
    """
    Whether this traceback is about the part or about the code that checked it.

    Two ways the part is at fault. An `AssertionError` on the last line is an
    entry of the list that did not hold. A Document the copy cannot recompute
    is a part already broken before anything was measured — that one has to
    read differently in the feedback, or Plan is told a dimension is wrong
    when the model is in error.
    """
    if _MODEL_IS_INVALID in traceback:
        return True
    lines = [line for line in traceback.strip().splitlines() if line.strip()]
    return bool(lines) and lines[-1].startswith("AssertionError")


def _why_the_checks_failed(traceback):
    """The feedback Plan gets, which must not say "a dimension is wrong" of a broken part."""
    if _MODEL_IS_INVALID in traceback:
        return ("The acceptance checks could not run: the Document does not recompute "
                "cleanly, so the part is in error before anything could be measured.\n"
                + traceback)
    return ("The result does not meet its acceptance list. The check that failed says "
            "which entry:\n" + traceback)


def review(intent, views, described, llm, events, attachments=(), checked=None):
    """
    Judge the outcome against the Intent.

    Given the Intent, the views, the Description of the finished Document, and
    whatever the user showed with the Intent. Deliberately not given the Plan,
    the code, or Execute's reasoning — an independent judgement of how it
    turned out, not of how it was produced.

    One view is not enough — a mirrored tooth flank reads only from above, and
    in isometry it is foreshortened, which is why Review passed one. Neither is
    the picture enough on its own: a hole is a dimension, not a pixel, and the
    Description is where it can be read (FCC-81).

    Without the attachments this compared a render against a sentence, so "does
    this resemble what I was shown" was not a question it could ask (FCC-47).
    """
    request = {"intent": intent, "the_document_as_it_now_stands": described}
    if checked is not None:
        # Measured facts beside the views, so the verdict is about what the
        # checks did not cover rather than a second opinion on what they did
        # (ADR-0015, FCC-76).
        request["facts_the_checks_confirmed"] = list(checked.confirmed)
        request["what_the_checks_could_not_cover"] = list(checked.uncovered)
    content = [{"type": "text", "text": json.dumps(request, indent=2, default=str)}]

    pictures = [picture for picture in map(_as_image, views or ()) if picture]
    if len(pictures) > 1:
        content[0]["text"] += (
            "\n\nThe next %d images are the same result seen from %s. They are one part, "
            "not several — a defect that reads in any one of them is a defect."
            % (len(pictures), ", ".join(_VIEW_NAMES[:len(pictures)])))
    if pictures:
        content.extend(pictures)
    else:
        content[0]["text"] += ("\n\nNo render was produced, so judge on the description "
                               "alone.")

    shown = [drawing for drawing in map(_as_image, attachments) if drawing]
    if shown:
        content[0]["text"] += (
            "\n\nThe last %d attachment%s is what the user showed with the Intent, not "
            "the result. Judge whether the result resembles it."
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


class _OutOfRevisions(Exception):
    """
    The shared revision ceiling is spent. Raised where the rewrite was asked
    for, because that is the only place that knows which cause asked and what
    had been tried by then; carries the finished reason so the block does not
    have to work it out again.
    """

    def __init__(self, reason, tried):
        super().__init__(reason)
        self.reason = reason
        self.tried = tried


def run(intent, *, llm, tools, events=None, budgets=None, cancelled=None, attachments=(),
        gate=auto_approve, ask=nobody_to_ask, decide=revise_by_default) -> LoopResult:
    """
    Turn an Intent into changes to the Document.

    Everything it needs is an argument: the LLM client, the Tool registry, the
    event stream to narrate into, the budgets, `cancelled` — a callable the loop
    asks between Steps and attempts so the user is never a passenger — `gate`,
    the Plan Gate, which must approve before any Step runs, and `decide`, which
    is asked what to do about a result that did not pass before a revision may
    wipe it. Nothing is global, so two runs cannot see each other and a
    sub-agent is a narrower Intent.

    `attachments` are files the user showed with the Intent; the Design phase
    sees them, and Review sees them again at the end.
    """
    events = events if events is not None else EventStream()
    budgets = budgets or Budgets()
    progress = {"applied": 0, "tried": []}

    try:
        return _run(intent, llm, tools, events, budgets, cancelled or (lambda: False), gate,
                    progress, attachments, ask, decide)
    except Stopped:
        return _block(events, "Stopped by you. Everything applied so far is still in the "
                              "Document; undo it Step by Step if you want it gone.", [],
                      progress["applied"])
    except _OutOfRevisions as spent:
        return _block(events, spent.reason, spent.tried, progress["applied"])
    except ProviderRefused as exc:
        return _block(events, str(exc), progress["tried"], progress["applied"], said=exc.said)
    except PhaseError as exc:
        events.emit(ERROR, message=str(exc))
        return LoopResult(ok=False, reason=str(exc))
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        return LoopResult(ok=False, reason=str(exc))


def _answered(question, ask, events):
    """Put a question to the person and say on the stream what came back."""
    answer = str(ask(question) or DECIDE_FOR_ME).strip()
    events.emit(ANSWER, text=answer, by="user" if answer else "nobody")
    return answer


def _written_in(the_part, question, answer, llm, events, attachments):
    """
    A person's answer, back through Design rather than on to Plan.

    The drawing answering writes `read` and the person answering writes
    `assumed` (FCC-87 preserved), and both land in the same model by the same
    merge — which is what makes a value already on it impossible to replace.
    An answer carried alongside the description instead would be a second source
    for Plan to prefer, and preferring the newer is exactly how the depth off
    SECTION B-B was lost.
    """
    grew, asks = design({"the_part_as_you_described_it": the_part,
                         "the_question": question,
                         "the_answer_you_were_given": answer},
                        llm, events, attachments)
    return merge(the_part, grew), asks


def _describe_the_part(intent, llm, events, budgets, ask, cancelled, attachments):
    """
    The part as Design has it, before anything is planned.

    Design owns the question (ADR-0019). It asks the user itself, here, for what
    it sees empty in its own model — before Steps have been drafted, rather than
    out of a Plan that has already committed to a shape. What comes back is
    merged, so the answer is part of the description the Plan Gate shows and not
    a note beside it.

    With nothing to look at there is no turn at all: Design then holds only what
    the user's answers to Plan supply, and the run costs exactly what it costs
    without a drawing.
    """
    if not _pictures(attachments):
        return {}

    the_part, question = design({"intent": intent}, llm, events, attachments)
    the_part = merge({}, the_part)

    asked = 0
    while question and asked < budgets.questions:
        if cancelled():
            raise Stopped()
        asked += 1
        events.emit(QUESTION, text=question)
        answer = _answered(question, ask, events)
        if not answer:
            break
        the_part, question = _written_in(the_part, question, answer, llm, events,
                                         attachments)

    if the_part:
        events.emit(DESIGN, model=the_part)
    return the_part


def _plan_once_it_knows_enough(intent, snapshot, llm, events, budgets, the_part,
                               ask, cancelled, feedback=None, attachments=()):
    """
    Plan, letting it ask for what the description did not carry (FCC-25).

    "модельку шестерні" names no module, no tooth count, no thickness and no
    bore. Planning anyway means inventing them, and an invented value is not
    visible anywhere the user could correct it — it turns up in the geometry.
    So Plan may answer with a question instead of Steps, and the run waits.

    A question Plan raises goes back to Design, never to the user (ADR-0019).
    Design decides whether the sheet answers it — a `read` value — or a person
    must, which is an `assumed` one, and either way the answer is merged into
    the description before the next Plan is made. FCC-90 was an interception for
    the drawing's sake; it is now the general rule, for the description's.

    A question is not an approval: the Plan Gate is still the single point of
    approval, and it still sees every Step. `budgets.questions` bounds how much
    a *person* is asked, because a model that only asks would never plan; a
    drawing answering is not a person being asked, and spends `budgets.rereads`
    instead.

    Every Plan comes through here, first or revised: a re-plan is a new Plan and
    may ask the same way, or it invents the value it was missing (FCC-67).
    Hands back the description as it now stands, because it grew on the way.
    """
    grown = the_part
    unanswered = []
    asked_the_user = 0
    read_again = 0
    told_to_choose = False
    while True:
        if cancelled():
            raise Stopped()
        planned = plan(intent, snapshot, llm, events, feedback=feedback,
                       the_part=grown, unanswered=unanswered)
        if not planned.question:
            if grown != the_part:
                # The Gate stands over the description the Plan was made from.
                events.emit(DESIGN, model=grown)
            return planned, grown

        events.emit(QUESTION, text=planned.question)
        if told_to_choose:
            raise PhaseError("Plan asked again after being told to choose for itself, so "
                             "there is no Plan and no way to get one.")

        if read_again < budgets.rereads and _pictures(attachments):
            read_again += 1
            from_the_drawing, _ = design({"the_part_as_you_described_it": grown,
                                          "the_question": planned.question},
                                         llm, events, attachments)
            if from_the_drawing:
                grown = merge(grown, from_the_drawing)
                events.emit(ANSWER, text=render(from_the_drawing), by="drawing")
                continue

        if asked_the_user >= budgets.questions:
            # Asked enough. Not an error — the run goes on with Plan choosing
            # and saying, in each Step, what it chose.
            unanswered.append(planned.question)
            events.emit(ANSWER, text=_DECIDE, by="budget")
            told_to_choose = True
            continue

        asked_the_user += 1
        answer = _answered(planned.question, ask, events)
        if not answer:
            unanswered.append(planned.question)
            told_to_choose = True
            continue
        # Whatever Design would ask next waits for its own turn: the questioning
        # here is Plan's, and it is Plan's budget being spent.
        grown, _ = _written_in(grown, planned.question, answer, llm, events, attachments)


def _run(intent, llm, tools, events, budgets, cancelled, gate, progress, attachments, ask,
         decide=revise_by_default):
    if cancelled():
        raise Stopped()
    # What the run was shown, before anything is done with it. Nothing recorded
    # it at all: the chips came off the bar and the transcript could not say the
    # run had a picture, live or replayed (FCC-88). The name rides beside the
    # path because a clipboard paste is a temp file the panel deletes when the
    # run ends, and a Session opened tomorrow must still be able to say what was
    # attached.
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(path), "path": path}
                                     for path in attachments])

    # Once per run, ahead of every Plan: a revision plans from the description
    # that already exists rather than paying a second call to look at the same
    # picture (ADR-0018). It only ever grows from here (ADR-0019).
    part = {"the_part": _describe_the_part(intent, llm, events, budgets, ask, cancelled,
                                           attachments)}

    def replan(feedback=None):
        planned, part["the_part"] = _plan_once_it_knows_enough(
            intent, tools.call("snapshot"), llm, events, budgets, part["the_part"],
            ask, cancelled, feedback, attachments)
        return planned

    def decided(why, applied):
        """
        What the user wants done about a result that did not pass.

        Only where there is something to lose: the right to delete a model four
        minutes and seventeen Steps in is not the agent's, but with nothing
        applied there is nothing to decide about. Anything unrecognised is the
        default — the panel is what turns "nobody answered" into a stop.
        """
        if not applied:
            return REVISE
        answer = decide({"why": why, "applied": applied})
        return answer if answer in DECISIONS else REVISE

    planned = replan()
    _emit_plan(events, PLAN, planned)

    rejections = 0
    revisions = _Revisions()
    described = {}

    def rewrite(cause, refused, feedback, tried, reason=None):
        """
        Rewrite the Plan for *cause*, or stop because the budget is spent.

        Five unrelated things replan and every one of them does it the same
        way: spend one unit of the shared ceiling, plan again from a fresh
        Snapshot, and say on the stream why. Only the cause, the sentence and
        what had been tried differ, so only those are arguments.
        """
        if revisions.exhausted(budgets, steps):
            raise _OutOfRevisions(
                revisions.block_reason(budgets, steps, cause, refused), tried)
        revisions.spend(cause)
        revised = replan(feedback)
        _emit_plan(events, PLAN_REVISED, revised, reason=reason or feedback)
        return revised

    # One loop, so every Plan reaches the Gate before any of its Steps runs — a
    # revised Plan is a new Plan and used to go straight into the work (FCC-68).
    while True:
        steps = planned.steps

        # Before the Gate, because a Plan that leaves part of the description to
        # nobody must not reach the user as a finished Plan. It spends the same
        # ceiling as every other cause, so a prompt that never learns to cite
        # stops the run with a reason instead of looping (FCC-94).
        unconsumed = _unconsumed(part["the_part"], planned.satisfies)
        if unconsumed:
            planned = rewrite(_Revisions.UNCOVERED,
                              "The Plan still leaves part of the description unplanned.",
                              _plan_the_rest(unconsumed), progress["tried"],
                              reason="no Step accounted for " + ", ".join(unconsumed))
            continue

        approval = gate(list(steps)) or Approval(APPROVE)

        if approval.decision == REJECT:
            applied = progress["applied"]
            said = "You rejected the Plan, so nothing was applied."
            if applied:
                # True only of the first Plan; a rejected revision leaves what
                # the Plan before it already applied.
                said = ("You rejected the Plan, so nothing more was applied. The Document keeps "
                        "the %d Step%s an earlier Plan already applied."
                        % (applied, "" if applied == 1 else "s"))
            return _block(events, said, progress["tried"], applied)

        # AMEND, and anything that is not an approval: no Step runs on a
        # decision the Gate does not recognise.
        if approval.decision != APPROVE:
            planned = rewrite(_Revisions.AMENDED,
                              "Your latest amendment was not planned.",
                              approval.feedback, progress["tried"],
                              reason="You asked for: " + approval.feedback)
            continue

        if not steps:
            return _block(events, "The Plan came back with no Steps, so there is nothing to "
                                  "apply and nothing to review.", progress["tried"],
                          progress["applied"])
        outcome = _work_through(steps, intent, tools, llm, events, budgets, cancelled,
                                progress, render(part["the_part"]))
        applied = progress["applied"]

        # Both go back to Plan from a fresh Snapshot and spend the same budget:
        # the Step no longer makes sense, or it cannot be built (ADR-0013).
        if outcome["obsolete"] or outcome["handback"]:
            if outcome["obsolete"]:
                cause, feedback = _Revisions.OBSOLETE, outcome["obsolete"]
                refused = "Execute has found it out of date again."
            else:
                cause, feedback = _Revisions.UNBUILDABLE, outcome["handback"]
                refused = outcome["handback"]

            planned = rewrite(cause, refused, feedback, outcome["tried"])
            continue

        described = outcome["description"] or described

        # Once the run is over, before Review: what is checked is the finished
        # Document, and a failure here is a rejection (FCC-76). No acceptance
        # list means no phase at all, not a phase that checks nothing.
        checked = (assertion(planned.acceptance, described, tools, llm, events, budgets,
                             cancelled)
                   if planned.acceptance else None)
        if checked is not None and checked.failed:
            feedback = _why_the_checks_failed(checked.traceback)
            answer = decided(feedback, applied)
            if answer == ACCEPT:
                # Straight out, without Review: the user has already judged the
                # result, and a verdict nobody will act on is a provider call
                # spent on nothing.
                return LoopResult(ok=True, verdict="pass", steps_applied=applied,
                                  reason="You took the result as it is, although the "
                                         "acceptance checks did not pass.")
            if answer == START_OVER:
                feedback += _MAY_START_AGAIN

            planned = rewrite(_Revisions.ASSERTED,
                              "The acceptance checks have failed again.", feedback,
                              outcome["tried"] + [{"assertion": checked.traceback}])
            continue

        views = _review_views(tools, events)
        verdict, reason = review(intent, views, described, llm, events, attachments,
                                 checked=checked)
        events.emit(REVIEW_VERDICT, verdict=verdict, reason=reason)

        if verdict == "pass":
            return LoopResult(ok=True, verdict=verdict, reason=reason,
                              steps_applied=applied)

        feedback = reason
        answer = decided(reason, applied)
        if answer == ACCEPT:
            return LoopResult(ok=True, verdict="pass", reason=reason,
                              steps_applied=applied)
        if answer == START_OVER:
            feedback += _MAY_START_AGAIN

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
        planned = rewrite(_Revisions.REJECTED, "Review has rejected the result again.",
                          feedback, outcome["tried"] + [{"review": reason}])


def _work_through(steps, intent, tools, llm, events, budgets, cancelled, progress,
                  as_described=""):
    """
    One pass over the Plan: written in one call, applied one Step at a time.

    The code for every Step arrives together and then lands the way it always
    did — `apply_step` per Step, one Step one undoable unit, `step_started` and
    `applied` per Step with what each one created. What changed is where the
    code came from, not how it lands (ADR-0021).

    An attempt is one writing of a Step, and a failure spends one: the first
    call is every Step's first attempt, and each regeneration is another
    attempt at the Step it starts from and a first attempt at every Step after
    it. `budgets.attempts_per_step` still bounds one Step, the counter resets
    when a Step lands, and a Step that runs out still hands back to Plan
    (ADR-0013). The worst case — every Step failing every time — costs one call
    more than the per-Step loop did, which is the shape this replaced.
    """
    if cancelled():
        raise Stopped()
    described = {}
    attempts = []
    index = 0
    written = execute(steps, tools, llm, events, budgets=budgets, as_described=as_described)

    while index < len(steps):
        if cancelled():
            raise Stopped()
        step = steps[index]

        if written.obsolete:
            return {"obsolete": written.obsolete, "handback": None,
                    "description": described,
                    "tried": [_tried(step, a) for a in attempts]}

        if not attempts:
            events.emit(STEP_STARTED, index=index + 1, total=len(steps), description=step)

        outcome = written.for_step(index + 1)
        if outcome.code:
            arguments = {"code": outcome.code, "label": outcome.label}
            events.emit(TOOL_CALL, name="apply_step", arguments=dict(arguments))
            result = tools.call("apply_step", arguments)
            events.emit(TOOL_RESULT, name="apply_step", result=result)
        else:
            result = {"status": "failed", "traceback":
                      "You replied with no code for Step %d. Write it, and every Step "
                      "after it." % (index + 1)}

        if result.get("status") == "applied":
            progress["applied"] += 1
            described = result.get("description") or described
            events.emit(APPLIED, step=index + 1, label=outcome.label,
                        created=result.get("created", []))
            index += 1
            attempts = []
            continue

        traceback = result.get("traceback", "The Step failed without saying why.")
        attempts.append(Attempt(code=outcome.code or "", traceback=traceback))
        progress["tried"] = [_tried(step, a) for a in attempts]
        events.emit(PREFLIGHT_FAILED, step=index + 1, attempt=len(attempts),
                    traceback=traceback)

        if len(attempts) >= budgets.attempts_per_step:
            # Not the end of the run: the Plan is a precise instruction while it
            # is buildable and advisory the moment it is not, so this goes back
            # to Plan the way `obsolete` does (ADR-0013).
            return {
                "handback": "Step %d of %d could not be built after %d attempts: %s\n"
                            "The last traceback was:\n%s"
                            % (index + 1, len(steps), len(attempts), step,
                               attempts[-1].traceback),
                "tried": [_tried(step, a) for a in attempts],
                "obsolete": None, "description": described,
            }

        if cancelled():
            raise Stopped()
        written = execute(steps, tools, llm, events, index, attempts, budgets, as_described)

    return {"obsolete": None, "handback": None,
            "description": described, "tried": []}


def _tried(step, attempt):
    return {"step": step, "code": attempt.code, "traceback": attempt.traceback}


def _block(events, reason, tried, applied, said=""):
    events.emit(BLOCKED, reason=reason, tried=list(tried), provider_said=said)
    return LoopResult(ok=False, blocked=True, reason=reason, tried=list(tried),
                      steps_applied=applied)
