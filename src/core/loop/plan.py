"""The Plan phase: create steps and acceptance criteria."""
import json
import re
from dataclasses import dataclass, field

from core.design.core import UNDER, _under
from core.design.merge import (
    _the_number,
    diff,
    merge,
)
from core.design.report import render
from core.design.gaps import _TO_BE_READ_AGAIN, _each_value, entries
from core.design.settlement_checks import described_twice
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
from core.loop.gate import _DECIDE, _DECIDE_AND_ASSUME, _a_question_for_the_build
from core.nobody import nobody_is_answering
from core.llm import cache_control_supported
from core.loop.prompt import PLAN_PHASE, _pictures, _the_evidence, prompt
from core.kernel.kernel_worker import RESERVED_STEP_NAMES

from core.agent.agent import run_declaring
from core.agent import phase_contract as pc
from core.loop.results import Stopped


@dataclass
class Plan:
    """
    The Steps, and what the finished part has to satisfy. `question` is
    what Plan must know before it can plan at all, set *instead of*
    steps, never beside them; `rollback` is the reply's own field for
    undoing part of the History, `{"to": K, "why": "..."}`, with
    `bad_rollback` set instead of it when that field could not be used as
    written. `values` is the frozen name -> number for every value a Step
    named that Plan's own kernel had actually computed, `value_errors`
    the Steps that named one it had not; `disputed` the unmet entries a
    revision calls wrong, each with its `measured` and `because`.
    """

    steps: list
    question: str = ""
    acceptance: list = field(default_factory=list)
    satisfies: list = field(default_factory=list)
    cites: list = field(default_factory=list)
    ops: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    unbuildable: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    value_errors: list = field(default_factory=list)
    left_out: list = field(default_factory=list)
    look: dict = None
    rollback: dict = None
    bad_rollback: str = ""
    disputed: list = field(default_factory=list)


def plan(intent, snapshot, llm, events, feedback=None, the_part=None, change_set=None,
         unanswered=(), revising=(), history=(), looking=(), looked=(), tried="",
         supervisor=None, kernel=None, undo_depth=None, must_decide=False, base=None,
         attachments=()):
    """
    Produce an ordered list of Steps from the Intent and a Snapshot. Plan
    is handed the part Design describes, one key and the user's own words
    -- two keys once let the newer always win and lost a value read off
    SECTION B-B -- and the drawing too, in *attachments*, where nobody
    answers. A revision before anything
    is applied is handed the Plan it rewrites (`revising`) or drops Steps
    a bare complaint never named; a revision after Steps have landed is
    handed those read-only (`history`), writes only the continuation
    numbered from where it leaves off, carries `tried` so it does not repeat
    a classified failure, and `undo_depth` bounding how far back its
    `rollback` may name.
    """
    request = {"intent": intent, "document": snapshot}
    if base:
        request["the_base_part"] = base
    if history:
        request["already_built"] = list(history)
        if undo_depth is not None:
            request["undo_depth"] = undo_depth
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
                                      "what_to_do": _DECIDE_AND_ASSUME if must_decide else _DECIDE}
    if looking:
        request["tools_you_may_look_with"] = list(looking)
        request["what_to_do_now"] = _LOOK_IT_UP
    if looked:
        request["what_the_tools_say"] = list(looked)

    task = json.dumps(request, indent=2, default=str)
    if attachments:
        evidence = _the_evidence(attachments)
        if evidence and cache_control_supported(getattr(llm, "model", None)):
            evidence = evidence[:-1] + [dict(evidence[-1], cache_control={"type": "ephemeral"})]
        task = evidence + [{"type": "text", "text": task}]
    reply, _ = run_declaring(
        pc.PLAN, {}, llm, task, kernel=kernel, supervisor=supervisor, stream=events,
        system=prompt(PLAN_PHASE, editing=bool(base)), design=the_part or {})
    known_values = dict(getattr(kernel, "plan_values", None) or {})
    changes = []
    steps, satisfies, cites, ops, unbuildable, values, value_errors = _steps_of(
        reply.get("steps", []), reply.get("op_vocabulary"), known_values, changes,
        dict(getattr(kernel, "unharvested_plan_values", None) or {}))
    rollback, bad_rollback = _rollback_field(reply.get("rollback"), len(history), undo_depth)

    look = reply.get("look")
    if not steps and looking and isinstance(look, dict) and look:
        return Plan(steps=[], look=look)

    question = str(reply.get("question") or "").strip()
    if not steps and question:
        return Plan(steps=[], question=question)
    if not steps and not rollback and not bad_rollback and not history:
        raise PhaseError("Plan produced no Steps for this Intent.")
    return Plan(steps=steps, satisfies=satisfies, cites=cites, ops=ops, changes=changes,
                unbuildable=unbuildable, values=values, value_errors=value_errors,
                acceptance=_acceptance_of(the_part), left_out=_left_out(the_part),
                rollback=rollback, bad_rollback=bad_rollback,
                disputed=_disputes_of(reply.get("disputes")))


def _disputes_of(raw) -> list:
    """The disputes of a Plan reply that name an `entry` and carry a `measured` and a `because`."""
    keys = ("entry", "measured", "because")
    return [{key: str(item[key]).strip() for key in keys}
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict) and all(str(item.get(key) or "").strip() for key in keys)]


def _steps_of(planned, op_vocabulary=None, known_values=None, changes=None, unharvested=None):
    """
    The prose of each Step, the entries the Plan cites, the operation each kept
    Step declared (its `changes` land in *changes* when given), and the Steps refused outright. What a Step satisfies goes
    into its own prose, so the Gate can tell one physical cavity planned three
    times; `op` is checked against `OP_VERBS` only where `"op_vocabulary":
    "closed"` -- unmarked, that check was measured at 14.9% and rejected. A
    Step's own `values` names the numbers it reads from Plan's own kernel,
    one not in *known_values* collected as a value nobody computed, the same
    shape `unbuildable` already is rather than raised as a Step failure.
    """
    known_values = known_values or {}
    unharvested = unharvested or {}
    steps, satisfies, cites, ops, unbuildable, values, value_errors = \
        [], [], [], [], [], {}, []
    closed = op_vocabulary == OP_VOCABULARY
    for step in planned:
        if isinstance(step, dict):
            prose = str(step.get("do") or step.get("step") or "").strip()
            cited = [_as_written(entry) for entry in (step.get("satisfies") or ())
                     if _as_written(entry)]
            op = str(step.get("op") or "").strip() or None
            named_values = [str(name).strip() for name in (step.get("values") or ())
                            if str(name).strip()]
            named_changes = [_as_written(entry) for entry in (step.get("changes") or ())
                             if _as_written(entry)]
        else:
            prose, cited, op, named_values = str(step).strip(), [], None, []
            named_changes = []
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
        for name in named_values:
            if name in RESERVED_STEP_NAMES:
                value_errors.append({"step": prose,
                                     "why": "%r is a reserved name -- give this one "
                                            "a new name" % name})
            elif name in known_values:
                values[name] = known_values[name]
            elif name in unharvested:
                value_errors.append({"step": prose,
                                     "why": "%r exists in your kernel but holds a %s, and a Step "
                                            "can only use plain data (numbers, strings, booleans, "
                                            "lists and dicts of those)" % (name, unharvested[name])})
            else:
                value_errors.append({"step": prose,
                                     "why": "%r was never computed in your kernel" % name})
        steps.append(prose + (" (satisfies: %s)" % ", ".join(_in_the_prose(entry)
                                                             for entry in cited)
                              if cited else ""))
        satisfies.extend(cited)
        cites.append(cited)
        ops.append(op if closed else None)
        if changes is not None:
            changes.append(named_changes)
    return steps, satisfies, cites, ops, unbuildable, values, value_errors


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
    Every value of both features of a pair described twice with differing
    values is left out: no part holds both, so neither is checked.
    """
    out = [_under("features", feature.get("id")) for pair in described_twice(the_part)
           for feature in pair[:2]]
    return [entry["says"] for entry in entries(the_part)
            if not any(entry["about"] == at or entry["about"].startswith(at + UNDER) for at in out)]


def _left_out(the_part):
    """One line for each pair `_acceptance_of` leaves out: the features and the values that differed."""
    return ["%s and %s are one feature described twice; neither is checked (%s)" % (
                a.get("id"), b.get("id"),
                "; ".join("%s: %s vs %s" % (at, said_a, said_b) for at, said_a, said_b in differing))
            for a, b, differing in described_twice(the_part)]


_BOOKKEEPING = ("plane", "edges", "position", "angle")


def _a_feature_and_its_leaf(address):
    parts = _as_written(address).split(" \u2192 ")
    return (tuple(parts[:2]), parts[2]) if parts[0] == "features" and len(parts) > 2 else (None, None)


def _unconsumed(the_part, satisfies, standing=()):
    """
    The entries of the description that no Step of the Plan accounts for.

    Only what the drawing fixes -- `read` or `derived` -- counts. A Step citing
    a feature's main value, any address but plane, edges, position or angle,
    covers that feature's bookkeeping addresses.
    """
    cited = {_as_written(entry) for entry in list(satisfies) + list(standing)}
    cited_features = {feature for feature, leaf in map(_a_feature_and_its_leaf, cited)
                      if feature and leaf not in _BOOKKEEPING}
    return [entry["about"] for entry in entries(the_part)
            if entry["flag"] in ("read", "derived")
            and not any(said == entry["about"] or said.startswith(entry["about"] + ":")
                        for said in cited)
            and not (_a_feature_and_its_leaf(entry["about"])[0] in cited_features
                     and _a_feature_and_its_leaf(entry["about"])[1] in _BOOKKEEPING)]


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


def _cited_without_a_number(the_part, cites):
    """
    The addresses a Step cites whose value Design left without its number.

    Execute reads `design[...]['mm']`, so a Step built on one dies with a
    KeyError far from the cause; a value waiting to be read again, or one
    displaced under `was` with a magnitude, carries no `mm` or `deg`.
    """
    cited = {_as_written(entry).partition(": ")[0] for step in cites for entry in step}
    return [at for at, node in _each_value(the_part, "")
            if at in cited and not {"mm", "deg"} & set(node)
            and (node.get("value") == _TO_BE_READ_AGAIN
                 or _the_number(str(node.get("was") or "")))]


def _no_number_to_build_on(addresses) -> str:
    """What Plan is told about a Step citing an address that has no number: a patch, not a rewrite."""
    return ("These addresses of the description have no number, so no Step can build on them:\n"
            + "\n".join("- " + address for address in addresses)
            + "\nThis is a patch. Take each address out of the `satisfies` and the prose of "
              "every Step citing it, or write the Step with a number you decide and say it is "
              "assumed. Send back your Plan with every other Step unchanged and in the same order.")


def _plan_the_rest(unconsumed, history=False) -> str:
    """
    What Plan is told about the entries it left to nobody: a patch, not a rewrite.

    Plan may add the entry to the `satisfies` of an existing Step or add a Step
    at the end of the Plan, and changes nothing else; a full rewrite once swapped
    one half of the part for the other four times over. Once Steps have landed
    they are History and the patch goes to the continuation.
    """
    where = "the continuation" if history else "your Plan in `the_plan_you_wrote`"
    return ("No Step of your Plan cited these entries of the description, so nothing in "
            "the Plan builds them:\n"
            + "\n".join("- " + entry for entry in unconsumed)
            + "\nThis is a patch. Either name each entry, written exactly as above, in the "
              "`satisfies` of an existing Step, or add a Step for it at the end of the Plan. "
              "Send back %s with every other Step unchanged and in the same order."
            % where
            + (" `already_built` is History and not yours to touch." if history else ""))


def _value_undefined(value_errors) -> str:
    """
    What Plan is told when a Step names a value its own kernel never
    computed -- a typo in the name, or a value it meant to compute and
    did not get to before declaring.
    """
    named = "\n".join("- %r %s" % (item["step"], item["why"]) for item in value_errors)
    return (named
            + "\nCompute the missing value as code in your kernel first, then send back "
              "every Step you already wrote, unchanged, with the Step(s) named above "
              "either naming the value you now computed or rewritten to use one that "
              "already exists. A name a Step already applied is frozen -- give a new "
              "value a name of its own rather than rebinding one already in use.")


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


_NUMBER = r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_PIECES = re.compile(r"\b%s\s+(?:pieces|segments|parts)\b|\binto\s+%s\b" % (_NUMBER, _NUMBER), re.I)
_A_LATER_FEATURE = re.compile(r"\b(?:pocket|hole|groove|thread|notch|slot|cut|bore|pattern|lug|boss)", re.I)
_INHERITED_INSIDE = re.compile(r"(?=.*\binherit)(?=.*\b(?:inside|inner|internal)\b)", re.I | re.S)


def _kind_of_step(prose, op):
    first = prose.split(None, 1)[0] if prose.strip() else ""
    word = re.sub(r"[^a-z]", "", (op or first).lower())
    if word in ("fillet", "chamfer", "draft"):
        return word
    if word in ("pocket", "hole", "thickness") or "pattern" in word or word == "multitransform":
        return "thickness" if word == "thickness" else "cut"
    return None


def _fillet_before_a_cut(steps, ops):
    """
    The Fillet, Chamfer or Draft Steps planned before a Pocket, Hole, pattern or Thickness Step
    without saying why, as `{"at", "step", "later"}` with `at` 1-based.

    A reason is the later feature and the number of pieces it cuts the edge into, or, for a
    Thickness alone, that the round is on the inside corners and the wall inherits it. A Draft
    followed by a Fillet or Chamfer is the draft-before-the-rounds order and is not refused.
    """
    kinds = [_kind_of_step(step, op) for step, op in zip(steps, list(ops) + [None] * len(steps))]
    found = []
    for at, kind in enumerate(kinds):
        if kind not in ("fillet", "chamfer", "draft"):
            continue
        later = [kinds[after] for after in range(at + 1, len(kinds)) if kinds[after] in ("cut", "thickness")]
        if not later:
            continue
        if kind == "draft" and any(k in ("fillet", "chamfer") for k in kinds[at + 1:]):
            continue
        prose = steps[at]
        if _PIECES.search(prose) and _A_LATER_FEATURE.search(prose):
            continue
        if set(later) == {"thickness"} and _INHERITED_INSIDE.search(prose):
            continue
        first = next(after for after in range(at + 1, len(kinds)) if kinds[after] in ("cut", "thickness"))
        found.append({"at": at + 1, "step": prose, "later": first + 1})
    return found


def _round_after_the_cuts(early, steps):
    """What Plan is told about a round planned before a cut: a patch naming only that Step."""
    return ("\n".join("- Step %d (%s) comes before Step %d (%s)."
                      % (item["at"], item["step"], item["later"], steps[item["later"] - 1])
                      for item in early)
            + "\nA fillet, chamfer or draft is planned after every Pocket, Hole, pattern and "
              "Thickness unless the Step says why it cannot be: it names the later feature and "
              "the number of pieces that feature cuts the edge into, or, before a Thickness, it "
              "says the round is on the inside corners and the wall inherits it. This is a patch. "
              "Either move each Step above to the end of the Plan, or add that reason to it, "
              "and send back the Plan with every other Step unchanged and in the same order.")


def _plan_once_it_knows_enough(intent, snapshot, llm, events, budgets, the_part,
                               cancelled, asking, tools, feedback=None, attachments=(),
                               revising=(), history=(), ledger=None, supervisor=None,
                               kernel=None, base=None):
    """
    Plan, letting it ask for what the description did not carry, and the
    run waits. A question goes to the drawing first; unanswered, Design
    settles it itself, `budgets.settles` times at most, and writes the chosen
    value into the model with `flag: assumed`, so Plan reads it back like any
    other field. A
    revision with Steps already landed is asked, once, how far the live
    Document's undo stack still reaches, so a `rollback` too deep is
    refused before it ever ends the run.
    """
    grown, for_the_build, change_set = the_part, False, None
    nobody_answered = 0
    patience = 0 if nobody_is_answering() else budgets.unanswered_questions
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
                       undo_depth=undo_depth, kernel=kernel,
                       must_decide=nobody_answered > patience or nobody_is_answering(),
                       base=base, attachments=attachments if nobody_is_answering() else ())
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
                events.emit(DESIGN, model=grown, the_base_part=base)
            return planned, grown

        events.emit(QUESTION, text=planned.question)

        if _a_question_for_the_build(planned.question):
            asking.looks += 1
            for_the_build = True
            continue
        elif asking.read_again < budgets.rereads and _pictures(attachments) \
                and not nobody_is_answering():
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

        settled = None
        if asking.settled < budgets.settles and not nobody_is_answering():
            settled, _ = design({"intent": intent,
                                 "the_part_as_you_described_it": grown,
                                 "the_question": _settle_it(planned.question)},
                                llm, events, attachments)
        if settled:
            before, grown = grown, merge(grown, settled, budgets.record_conflicts)
            if grown != before:
                asking.settled += 1
                change_set = diff(before, grown)
                events.emit(CHANGE_SET, changes=change_set)
                events.emit(ANSWER, text=render(settled), by="design")
                continue
            grown = before

        nobody_answered += 1
        if nobody_answered > patience + 1:
            raise PhaseError("Plan kept asking after it was told to decide and write "
                             "`assumed`: %s" % planned.question)
        events.emit(ANSWER, by="nobody",
                    text=_DECIDE_AND_ASSUME if nobody_answered > patience
                    else _DECIDE)
        if planned.question not in asking.unanswered:
            asking.unanswered.append(planned.question)
