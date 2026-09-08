"""The Plan phase: create steps and acceptance criteria."""
import json
import re
from dataclasses import dataclass, field

from core.design.design import (
    UNDER,
    diff,
    entries,
    merge,
    render,
)
from core.events import ANSWER, CHANGE_SET, DESIGN, QUESTION

from core.loop.budgets import PhaseError
from core.loop.describe import _A_DIRECTION_FIELD, _not_english
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
from core.loop.prompt import PLAN_PHASE, _ask, _pictures, prompt
from core.loop.results import Stopped


@dataclass
class Plan:
    """
    The Steps, and what the finished part has to satisfy.

    `question` is what Plan must know before it can plan at all. It is set
    *instead of* steps, never beside them — a question is not a Plan with a
    caveat.

    `acceptance` is what has to be true of the finished part, once for the
    whole Plan rather than per Step: the checks run when the run is over,
    against the Document that was asked for, not against an intermediate state.
    It is derived from the description rather than written here (ADR-0020).

    `satisfies` is every entry of the description the Plan's Steps cite between
    them, flat: the check is whether an entry is accounted for by *any* Step,
    so which Step accounted for it is a thing the Gate shows and the loop does
    not need. `cites` is the same citations kept Step by Step, which one check
    does need: a Step builds in one plane, so the entries it cites have to
    agree about which one that is.

    `look` is a read-only Tool Plan wants run, and like `question` it comes
    instead of Steps. It is offered only once Plan has asked something the
    drawing cannot answer, so it is how a question about the build is settled
    rather than a second route to the Document.

    `ops` is the operation each Step of `steps` declared, position for
    position, `None` where none was declared or the closed vocabulary was
    not in force (ADR-0026, FCC-233) — Execute is told it, and `core/lint.py`
    checks the TypeId a Step's code builds against it. `unbuildable` is every
    Step `_steps_of` refused outright because its `op` was not one of the 36:
    it never reaches Execute, so no code is ever asked for it, and the entry
    it would have satisfied is left to the ordinary coverage check to send
    back through Plan again.
    """

    steps: list
    question: str = ""
    acceptance: list = field(default_factory=list)
    satisfies: list = field(default_factory=list)
    cites: list = field(default_factory=list)
    ops: list = field(default_factory=list)
    unbuildable: list = field(default_factory=list)
    look: dict = None


def plan(intent, snapshot, llm, events, feedback=None, the_part=None, change_set=None,
         unanswered=(), revising=(), looking=(), looked=(), tried=""):
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

    A revision is handed the Plan it is rewriting, in its own words. Without it
    a revision is written from nothing but the complaint, and a complaint names
    only what was missing: a recorded run
    (`logs/sessions/9958ccdc8e324a74ade9b08405be465b.jsonl`) answered "these
    entries are unplanned" with a two-Step Plan covering exactly those and
    dropping the nine Steps that covered the rest, was told the nine were
    unplanned, wrote them again and dropped the two, and spent the whole
    revision budget swapping one half of the part for the other without a
    single Step ever running.

    A question nobody answered rides with it as an instruction rather than a
    value or a source: Plan chooses, and says in the Step what it chose.

    `looking` is the read-only Tools Plan may settle its own question with, and
    `looked` is what they said. Both are empty until Plan asks something the
    drawing was never going to answer: a uuid, a type's name, a property's
    spelling.

    `tried` is `Ledger.render()` (`core/ledger.py`) — this run's own failures
    so far, distilled — placed beside `what_went_wrong_last_time` rather than
    folded into it: that key is one Step's prose and one traceback, gone the
    moment this replan is written; `tried` is every classified failure since
    the run began, Step or Plan, so a revision does not ask for a shape that
    has already failed the same way more than once.

    `change_set` is `core.design.diff`'s answer to what grew since the last
    call to this same re-plan, in the one place the description changes
    between two Plan calls of one run: the reread loop of
    `_plan_once_it_knows_enough`, where a question sent back to the drawing
    grows the description and Plan is asked again before anything is applied
    (FCC-311). Given, it replaces the whole rendering under
    `what_the_part_is` — a moved datum reaches Plan as the datum that moved,
    not the whole re-serialized part beside the one it already saw. What
    Plan's Steps are held to, `what_the_plan_must_satisfy`, is still the
    acceptance of the whole current `the_part`: shrinking that too would let a
    Step stop citing an entry the change-set did not mention, though nothing
    changed about it either.
    """
    request = {"intent": intent, "document": snapshot}
    if revising:
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

    reply = _ask(llm, events, PLAN_PHASE, prompt(PLAN_PHASE),
                 json.dumps(request, indent=2, default=str))
    steps, satisfies, cites, ops, unbuildable = _steps_of(
        reply.get("steps", []), reply.get("op_vocabulary"))

    look = reply.get("look")
    if not steps and looking and isinstance(look, dict) and look:
        return Plan(steps=[], look=look)

    question = str(reply.get("question") or "").strip()
    if not steps and question:
        return Plan(steps=[], question=question)
    if not steps:
        raise PhaseError("Plan produced no Steps for this Intent.")
    return Plan(steps=steps, satisfies=satisfies, cites=cites, ops=ops,
                unbuildable=unbuildable, acceptance=_acceptance_of(the_part))


def _steps_of(planned, op_vocabulary=None):
    """
    The prose of each Step, the entries the Plan cites, the operation each
    kept Step declared, and the Steps refused outright.

    A Step is `{"do": …, "op": …, "satisfies": [...]}`, or the bare string it
    has always been. What a Step satisfies goes into its own prose rather than
    beside it, because the Plan Gate shows prose: a reader who cannot tell
    from the Gate which part of the part a Step is for cannot tell that one
    physical cavity has been planned three times, which is what Steps 4, 5
    and 6 of the recorded run were (FCC-94).

    A Step citing nothing is not refused here. The rule is coverage over the
    whole Plan — no entry may be left to no Step — and a Plan is the only thing
    that can be judged against it; a Step may legitimately carry work no entry
    of the description names.

    `op` is checked against `OP_VERBS` (ADR-0026, FCC-233) only where *planned*
    itself carries `"op_vocabulary": "closed"` — the marker FCC-232 already
    put on `kind`, kept the same shape here rather than invented a second one.
    A recorded Plan predates the closed vocabulary and carries no marker at
    all, and takes this same legacy path whether or not a Step happens to
    have an `op` field: checking one against the list is exactly the
    prompt-only alternative ADR-0026 measured at 14.9% and rejected by name.

    A Step whose `op` is off the list under the marker is refused rather than
    kept: it is left out of `steps` entirely, so it is never sent to Execute
    and no code is ever asked for it, and what it cited is left out of
    `satisfies` too, so the ordinary coverage check is what sends it back
    through Plan again — no second `unbuildable` code path was written for a
    case the first one already covers.

    A Step written in Ukrainian is refused the same way, under the same
    marker: the vocabulary is closed to one language as much as to one set
    of verbs, and Ukrainian stays the language of the Intent and of the
    conversation that produced it, never of a Step (ADR-0026, `config/
    prompts/language.md`). "Written in Ukrainian" is `_not_english`'s own
    call, not one Cyrillic character in an otherwise English sentence — a
    label or a part name a user typed in the Intent is not what this
    refuses.
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

    Plan may cite the whole line it was handed, value and all, and the values on
    that list run to paragraphs — a tangency described in prose, an edge
    selection described by colour. Pasted into a Step they are the description
    printed twice: once as the Acceptance the Gate shows beneath the Plan, and
    once inside every Step that cites it. Step 1 of the recorded run reached the
    user at 3553 characters for a Step of a thousand, and Execute is handed the
    same values addressed in `what_each_entry_says` regardless (FCC-127).

    The flag survives the trim. It is what tells the Gate a Step builds a value
    nobody supplied, and an assumption the user has not read is the one thing a
    standing approval may not stand over.
    """
    address, _, value = _as_written(entry).partition(": ")
    flag = re.search(r"\((?:read|derived|assumed|suspected)\)$", value)
    return address + (" " + flag.group(0) if flag else "")


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


def _unconsumed(the_part, satisfies, standing=()):
    """
    The entries of the description that no Step of the Plan accounts for.

    `standing` is what the Steps already applied cite between them. After a
    handback the question the check asks is narrower — the description less what
    the Document already answers for — and a revision that plans only the work
    that is left is a correct Plan. Measured against the description alone it is
    not: a recorded run was told sixteen entries were unplanned, every one of
    them satisfied by a feature standing in the Document at that moment, and
    spent a revision on a Plan that restated all eight Steps, four of which
    Execute then applied nothing for (FCC-148).

    The mechanical catch for the defect the epic is named after: the revised
    Plan of the recorded run dropped the 100 mm front to rear and no rule,
    prompt or judgement noticed. Checked over addresses and not over prose, so
    it is a set difference rather than a comparison of sentences.

    Coverage, not exclusivity. One entry may legitimately feed several Steps —
    an overall height feeds both the base solid and a through hole's depth — but
    no entry may feed none.

    Only what the drawing fixes — `read` or `derived`. An assumed value is still
    a criterion and is still measured, but a Plan may not be refused for failing
    to build a number nobody supplied. A derived one is not that: it is a value
    the views settle, measured off the line work or off a tangency instead of
    off a callout, and it is as much the part as a printed number is.

    Matched as written, and never by prefix of an address — a Plan allowed to
    cite `features` and cover everything beneath it would be the check defeating
    itself. What is allowed is the value trailing the address, because the list
    Plan cites from carries both on one line (`envelope → length: 150 mm
    (read)`), and a Plan that copies the line it was handed means the entry that
    line is. Refusing it would make the check fail on the one Plan that did
    exactly as it was told.
    """
    cited = {_as_written(entry) for entry in list(satisfies) + list(standing)}
    return [entry["about"] for entry in entries(the_part)
            if entry["flag"] in ("read", "derived")
            and not any(said == entry["about"] or said.startswith(entry["about"] + ":")
                        for said in cited)]


def _op_off_the_list(unbuildable) -> str:
    """
    What Plan is told when one of its own Steps named an operation off the
    closed 36-verb list (ADR-0026, FCC-233) — the same actionability rule
    FCC-232's refused `kind` applied: name the Step, name why, and hand back
    the list it has to pick from, so the next Plan has a reason to choose a
    different verb instead of restating the one just refused.
    """
    named = "\n".join("- %r %s" % (item["step"], item["why"]) for item in unbuildable)
    return (named
            + "\nEvery build op has to be one of these 36: "
            + ", ".join(OP_VERBS)
            + ".\nRewrite the Step(s) named above with one of them, or with a `question` "
              "if the part genuinely needs none of them. Send back every Step you already "
              "wrote that was kept, unchanged, beside the rewritten one(s).")


def _plan_the_rest(unconsumed) -> str:
    """What Plan is told about the entries it left to nobody."""
    return ("No Step of your Plan cited these entries of the description, so nothing in "
            "the Plan builds them:\n"
            + "\n".join("- " + entry for entry in unconsumed)
            + "\nPlan them, and name each one in the `satisfies` of the Step that "
              "satisfies it, written exactly as it is above.\n"
              "Your Plan is in `the_plan_you_wrote`. Send it back with these Steps added "
              "— every Step you already wrote, unchanged, and the new ones beside them. A "
              "Plan of only the entries listed here drops the Steps that built the rest, "
              "and the same check then names those instead: a recorded run swapped one "
              "half of the part for the other four times over and spent the whole revision "
              "budget without running a single Step.")


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

    A Step is one sketch in one plane, extruded one way. So the entries it says
    it satisfies have to agree about that plane — and where they do not, the
    Step is wrong before it is written, without anybody having to run it or
    know which plane of the part is which.

    Step 1 of the recorded run
    (`logs/sessions/549e6232c9e04b659e5a2d536354bd7a.jsonl`) cites
    `features → outer-profile → plane: the plane of the length and the height`
    and `features → right-mounting-lug → plane: the plane of the length and the
    depth` in the one Step, and built both in the elevation. The R12.5 rounded
    end belongs to the plan view; drawn in the elevation it turned the flat
    30 mm end face at 108 into a round nose, made the lug 25 mm tall where the
    Step's own prose says 30, and destroyed the datum every right-hand
    dimension on the part is measured from.

    `_A_DIRECTION_FIELD` is the two fields of an entry that name a direction
    rather than a distance: an address ends in one of them, and what follows
    the colon is the direction itself, in the description's own words. A Step
    may cite an entry by its address alone or with the value written out, so
    the direction is looked up in the acceptance, which carries both.
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
                               revising=(), ledger=None):
    """Plan, letting it ask for what the description did not carry, and the run waits (FCC-25).
    A question goes to the drawing first (ADR-0022); unanswered, Plan chooses for itself and writes
    the value into the Step's prose — not an approval, the Gate still sees every unanswered question.
    A question about this FreeCAD, not about the part, is settled by a look at the read-only Tools,
    unbounded since `budgets.looks_per_turn` went (FCC-304, FCC-149).
    A re-read that grew the description hands Plan `core.design.diff`'s change-set, not the whole
    document again (FCC-311).
    No question ends the run (FCC-127, FCC-132, FCC-133); a re-plan invents or asks the same way
    (FCC-67).
    """
    grown, for_the_build, change_set = the_part, False, None
    while True:
        if cancelled():
            raise Stopped()
        planned = plan(intent, snapshot, llm, events, feedback=feedback,
                       the_part=grown, change_set=change_set, unanswered=asking.unanswered,
                       revising=revising, looking=_lookable(tools) if for_the_build else (),
                       looked=asking.looked_up,
                       tried=ledger.render() if ledger else "")
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
                before, grown = grown, merge(grown, from_the_drawing)
                if grown != before:
                    change_set = diff(before, grown)
                    events.emit(CHANGE_SET, changes=change_set)
                events.emit(ANSWER, text=render(from_the_drawing), by="drawing")
                continue
            events.emit(ANSWER, text=_SAID_NOTHING, by="drawing")

        events.emit(ANSWER, text=_DECIDE, by="nobody")
        if planned.question not in asking.unanswered:
            asking.unanswered.append(planned.question)
