"""The Plan phase: create steps and acceptance criteria."""
import json
import re
from dataclasses import dataclass, field

from core.design.core import UNDER
from core.design.merge import (
    diff,
    merge,
)
from core.design.report import render
from core.design.gaps import entries
from core.events import ANSWER, CHANGE_SET, DESIGN, QUESTION

from core.loop.budgets import PhaseError
from core.loop.describe import _A_DIRECTION_FIELD, _not_english, _settle_it
from core.loop.design import design
from core.loop.execute import (
    OP_VERBS,
    OP_VOCABULARY,
    _LOOK_IT_UP,
    _SAID_NOTHING,
    _addressed,
    _look,
    _lookable,
)
from core.loop.gate import _DECIDE, _a_question_for_the_build
from core.loop.prompt import PLAN_PHASE, _pictures, prompt

import tempfile
from core.agent.agent import run_phase
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor
from core.loop.results import Stopped


@dataclass
class Plan:
    """
    The Steps, and what the finished part has to satisfy. `question` is
    what Plan must know before it can plan at all, set *instead of*
    steps, never beside them; `rollback` is the reply's own field for
    undoing part of the History, `{"to": K, "why": "..."}`, with
    `bad_rollback` set instead of it when that field could not be used as
    written.
    """

    steps: list
    question: str = ""
    acceptance: list = field(default_factory=list)
    satisfies: list = field(default_factory=list)
    cites: list = field(default_factory=list)
    ops: list = field(default_factory=list)
    unbuildable: list = field(default_factory=list)
    look: dict = None
    rollback: dict = None
    bad_rollback: str = ""


def plan(intent, snapshot, llm, events, feedback=None, the_part=None, change_set=None,
         unanswered=(), revising=(), history=(), looking=(), looked=(), tried="",
         supervisor=None, kernel=None, undo_depth=None):
    """
    Produce an ordered list of Steps from the Intent and a Snapshot. Plan
    never sees the picture; it is handed the part Design describes, one
    key and the user's own words -- two keys once let the newer always
    win and lost a value read off SECTION B-B. A revision before anything
    is applied is handed the Plan it rewrites (`revising`) or drops Steps
    a bare complaint never named; a revision after Steps have landed is
    handed those read-only (`history`) and writes only the continuation,
    numbered from where the History leaves off, and `tried` is every
    classified failure so far so a revision does not repeat one. `undo_depth`,
    when a revision could propose one, is how many Steps the live Document's
    undo stack can still reach, bounding how far back its `rollback` may name.
    """
    request = {"intent": intent, "document": snapshot}
    if history:
        request["already_built"] = list(history)
    elif revising:
        request["the_plan_you_wrote"] = list(revising)
    if change_set is not None:
        request["what_the_part_is"] = change_set
        request["what_the_plan_must_satisfy"] = _acceptance_of(the_part)
    elif the_part:
        request["what_the_part_is"] = render(the_part)
        request["what_the_plan_must_satisfy"] = _acceptance_of(the_part)
    if feedback:
        request["what_went_wrong_last_time"] = feedback
    if tried:
        request["already_failed_this_way"] = tried
    if unanswered:
        request["nobody_answered"] = {"the_questions": list(unanswered),
                                      "what_to_do": _DECIDE}
    if looking:
        request["tools_you_may_look_with"] = list(looking)
        request["what_to_do_now"] = _LOOK_IT_UP
    if looked:
        request["what_the_tools_say"] = list(looked)

    supervisor = supervisor or SessionTreeSupervisor()
    task = json.dumps(request, indent=2, default=str)
    
    with tempfile.TemporaryDirectory() as working_dir:
        declared, session, declarations = run_phase(
            pc.PLAN, working_dir,
            {"design": the_part or {}},
            llm, task,
            kernel=kernel, supervisor=supervisor, stream=events,
            system=prompt(PLAN_PHASE)
        )
    
    reply = declared if isinstance(declared, dict) else {}
    steps, satisfies, cites, ops, unbuildable = _steps_of(
        reply.get("steps", []), reply.get("op_vocabulary"))
    rollback, bad_rollback = _rollback_field(reply.get("rollback"), len(history), undo_depth)

    look = reply.get("look")
    if not steps and looking and isinstance(look, dict) and look:
        return Plan(steps=[], look=look)

    question = str(reply.get("question") or "").strip()
    if not steps and question:
        return Plan(steps=[], question=question)
    if not steps and not rollback and not bad_rollback:
        raise PhaseError("Plan produced no Steps for this Intent.")
    return Plan(steps=steps, satisfies=satisfies, cites=cites, ops=ops,
                unbuildable=unbuildable, acceptance=_acceptance_of(the_part),
                rollback=rollback, bad_rollback=bad_rollback)


def _steps_of(planned, op_vocabulary=None):
    """
    The prose of each Step, the entries the Plan cites, the operation each kept
    Step declared, and the Steps refused outright. What a Step satisfies goes
    into its own prose, so the Gate can tell one physical cavity planned three
    times. `op` is checked against `OP_VERBS` only where `"op_vocabulary":
    "closed"` -- unmarked, that check was measured at 14.9% and rejected.
    """
    steps, satisfies, cites, ops, unbuildable = [], [], [], [], []
    closed = op_vocabulary == OP_VOCABULARY
    for step in planned:
        if isinstance(step, dict):
            prose = str(step.get("do") or step.get("step") or "").strip()
            cited = [_as_written(entry) for entry in (step.get("satisfies") or ())
                     if _as_written(entry)]
            op = str(step.get("op") or "").strip() or None
        else:
            prose, cited, op = str(step).strip(), [], None
        if not prose:
            continue
        if closed and op and op not in OP_VERBS:
            unbuildable.append({"step": prose,
                                "why": "%r is not one of the 36 approved operations" % op,
                                "entries": cited})
            continue
        if closed and _not_english(prose):
            unbuildable.append({"step": prose,
                                "why": "is not written in English",
                                "entries": cited})
            continue
        steps.append(prose + (" (satisfies: %s)" % ", ".join(_in_the_prose(entry)
                                                             for entry in cited)
                              if cited else ""))
        satisfies.extend(cited)
        cites.append(cited)
        ops.append(op if closed else None)
    return steps, satisfies, cites, ops, unbuildable


def _as_written(entry):
    """An address as one line, because it is matched by the words it is written in."""
    return " ".join(str(entry).split())


def _in_the_prose(entry):
    """
    A citation as the Gate reads it: the address, and the flag if it carried one.

    The flag survives the trim — it is what tells the Gate a Step builds a
    value nobody supplied.
    """
    address, _, value = _as_written(entry).partition(": ")
    flag = re.search(r"\((?:read|derived|assumed|suspected)\)$", value)
    return address + (" " + flag.group(0) if flag else "")


def _acceptance_of(the_part):
    """
    What the finished part has to satisfy: the description's own
    entries. An assumed value is a criterion too, and marked assumed.
    """
    return [entry["says"] for entry in entries(the_part)]


def _unconsumed(the_part, satisfies, standing=()):
    """
    The entries of the description that no Step of the Plan accounts for.

    The mechanical catch for a Plan that dropped a dimension no rule noticed.
    Only what the drawing fixes — `read` or `derived` — counts: a Plan may
    not be refused for failing to build a number nobody supplied.
    """
    cited = {_as_written(entry) for entry in list(satisfies) + list(standing)}
    return [entry["about"] for entry in entries(the_part)
            if entry["flag"] in ("read", "derived")
            and not any(said == entry["about"] or said.startswith(entry["about"] + ":")
                        for said in cited)]


def _rollback_field(raw, applied, undo_depth=None) -> tuple:
    """
    The `rollback` field of a Plan reply, validated against how many Steps
    of `already_built` it was handed and, when known, how many the live
    Document's undo stack can still reach: `to` must be an already-applied
    Step no further back than the stack goes, `max(0, applied - undo_depth)
    <= to < applied`. Returns `(rollback, bad_reason)`, exactly one of the
    two set, so a malformed or unreachable field is refused rather than
    silently dropped, crashed on, or left to end the run.
    """
    if raw is None:
        return None, ""
    if not isinstance(raw, dict):
        return None, ("%r is not a rollback object -- it must be "
                      '{"to": K, "why": "..."}.' % (raw,))
    to = raw.get("to")
    why = str(raw.get("why") or "").strip()
    earliest = max(0, applied - undo_depth) if undo_depth is not None else 0
    if not isinstance(to, int) or isinstance(to, bool) or not (earliest <= to < applied):
        if undo_depth is not None and isinstance(to, int) and not isinstance(to, bool) \
                and 0 <= to < earliest:
            return None, ('rollback.to=%d would undo %d Steps, but the undo stack only '
                          "reaches back %d -- name a Step no earlier than %d."
                          % (to, applied - to, undo_depth, earliest))
        return None, ('rollback.to=%r must be an integer naming an already-applied Step, '
                      "from %d to %d." % (to, earliest, applied - 1 if applied else -1))
    if not why:
        return None, "rollback.why is empty -- give one sentence of reason."
    return {"to": to, "why": why}, ""


def _bad_rollback(reason) -> str:
    """What Plan is told when its `rollback` field could not be used as written."""
    return (reason
            + "\nSend back the continuation with a valid `rollback` field -- "
              '{"to": K, "why": "..."} naming an already-applied Step, K from 0 up to the '
              "last Step applied -- or with none at all if nothing needs to be undone. Send "
              "back every Step of the continuation you already wrote, unchanged, beside it.")


def _op_off_the_list(unbuildable) -> str:
    """
    What Plan is told when one of its own Steps named an operation off
    the closed 36-verb list.
    """
    named = "\n".join("- %r %s" % (item["step"], item["why"]) for item in unbuildable)
    return (named
            + "\nEvery build op has to be one of these 36: "
            + ", ".join(OP_VERBS)
            + ".\nRewrite the Step(s) named above with one of them, or with a `question` "
              "if the part genuinely needs none of them. Send back every Step you already "
              "wrote that was kept, unchanged, beside the rewritten one(s).")


def _plan_the_rest(unconsumed, history=False) -> str:
    """
    What Plan is told about the entries it left to nobody.

    Before anything is applied, Plan's own Plan is what it corrects
    (`the_plan_you_wrote`); once Steps have landed, they are History and
    only the continuation is Plan's to write, so the instruction differs
    only in what it asks for back.
    """
    said = ("No Step of your Plan cited these entries of the description, so nothing in "
            "the Plan builds them:\n"
            + "\n".join("- " + entry for entry in unconsumed)
            + "\nPlan them, and name each one in the `satisfies` of the Step that "
              "satisfies it, written exactly as it is above.\n")
    if history:
        return (said + "`already_built` is History and not yours to touch. Add Steps to "
                "the continuation that cover the entries listed above, beside whatever "
                "continuation Steps you already wrote.")
    return (said + "Your Plan is in `the_plan_you_wrote`. Send it back with these Steps "
            "added — every Step you already wrote, unchanged, and the new ones beside "
            "them. A Plan of only the entries listed here drops the Steps that built the "
            "rest, and the same check then names those instead: a recorded run swapped "
            "one half of the part for the other four times over and spent the whole "
            "revision budget without running a single Step.")


def _do_the_sums(miscounted) -> str:
    """What Plan is told about the arithmetic it stated and did not do."""
    return ("These sums are in your Steps and they do not come out:\n"
            + "\n".join("- " + said for said in miscounted)
            + "\nWork each one out again and send the Plan back with the right number in "
              "the Step that uses it — every Step you already wrote, with these corrected. "
              "Angles are in degrees, and a distance along a direction is the distance "
              "along the geometry times the cosine of the angle between them.\n"
              "If the number that does come out contradicts an entry of the description, "
              "that is two `read` values that cannot both be true and it is not yours to "
              "settle: say so in `question` rather than choosing a third number. A "
              "recorded run assumed one, was refused by Execute for the arithmetic, "
              "assumed another, was refused in the same words, and ended with an empty "
              "Document.")


def _two_directions_in_one_step(planned):
    """
    A Step whose own citations disagree about which way it is building.

    Constraint: a Step is one plane; disagreeing citations are wrong before
    anyone runs it.
    Measured: a recorded run's Step 1 ruined the part's own datum this way.
    """
    of = _addressed(planned.acceptance)

    found = []
    for at, cited in enumerate(planned.cites or (), start=1):
        for field in _A_DIRECTION_FIELD:
            said = {}
            for entry in cited:
                address, _, value = str(entry).partition(": ")
                address = address.strip()
                value = value.strip() or of.get(address, "")
                if address.endswith(field) and value:
                    said.setdefault(value, address)
            if len(said) > 1:
                found.append((at, field[len(UNDER):], said))
    return found


def _one_direction_per_step(mixed):
    """What to tell Plan about a Step that is building two ways at once."""
    return "\n".join(
        "Step %d says it satisfies %d different values of `%s`: %s. A Step is one "
        "sketch in one plane extruded one way, so it cannot satisfy two of them — "
        "the entries it cites have to agree about the direction. Split it into one "
        "Step per plane, each citing only the entries that lie in that one, and say "
        "in the prose of each which plane it sketches in."
        % (at, len(said), field, "; ".join("`%s` (%s)" % (value, address)
                                           for value, address in said.items()))
        for at, field, said in mixed)


def _plan_once_it_knows_enough(intent, snapshot, llm, events, budgets, the_part,
                               cancelled, asking, tools, feedback=None, attachments=(),
                               revising=(), history=(), ledger=None, supervisor=None):
    """
    Plan, letting it ask for what the description did not carry, and the
    run waits. A question goes to the drawing first; unanswered, Design
    settles it itself and writes the chosen value into the model with
    `flag: assumed`, so Plan reads it back like any other field. A
    revision with Steps already landed is asked, once, how far the live
    Document's undo stack still reaches, so a `rollback` too deep is
    refused before it ever ends the run.
    """
    grown, for_the_build, change_set = the_part, False, None
    undo_depth = tools.call("undo_depth")["depth"] if history else None
    while True:
        if cancelled():
            raise Stopped()
        planned = plan(intent, snapshot, llm, events, feedback=feedback,
                       the_part=grown, change_set=change_set, unanswered=asking.unanswered,
                       revising=revising, history=history,
                       looking=_lookable(tools) if for_the_build else (),
                       looked=asking.looked_up,
                       tried=ledger.render() if ledger else "", supervisor=supervisor,
                       undo_depth=undo_depth)
        change_set = None
        if planned.look:
            asking.looked_up.append(_look(tools, planned.look, events))
            for_the_build = False
            continue
        mixed = _two_directions_in_one_step(planned)
        if mixed and asking.re_aimed < budgets.revisions:
            asking.re_aimed += 1
            feedback, revising = _one_direction_per_step(mixed), planned.steps
            continue
        if not planned.question:
            if grown != the_part:
                events.emit(DESIGN, model=grown)
            return planned, grown

        events.emit(QUESTION, text=planned.question)

        if _a_question_for_the_build(planned.question):
            asking.looks += 1
            for_the_build = True
            continue
        elif asking.read_again < budgets.rereads and _pictures(attachments):
            asking.read_again += 1
            from_the_drawing, _ = design({"intent": intent,
                                          "the_part_as_you_described_it": grown,
                                          "the_question": planned.question},
                                         llm, events, attachments)
            if from_the_drawing:
                before, grown = grown, merge(grown, from_the_drawing, budgets.record_conflicts)
                if grown != before:
                    change_set = diff(before, grown)
                    events.emit(CHANGE_SET, changes=change_set)
                events.emit(ANSWER, text=render(from_the_drawing), by="drawing")
                continue
            events.emit(ANSWER, text=_SAID_NOTHING, by="drawing")

        settled, _ = design({"intent": intent,
                             "the_part_as_you_described_it": grown,
                             "the_question": _settle_it(planned.question)},
                            llm, events, attachments)
        if settled:
            before, grown = grown, merge(grown, settled, budgets.record_conflicts)
            if grown != before:
                change_set = diff(before, grown)
                events.emit(CHANGE_SET, changes=change_set)
                events.emit(ANSWER, text=render(settled), by="design")
                continue
            grown = before

        events.emit(ANSWER, text=_DECIDE, by="nobody")
        if planned.question not in asking.unanswered:
            asking.unanswered.append(planned.question)
