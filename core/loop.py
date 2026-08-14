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
import time
from dataclasses import dataclass, field
from typing import List

from core.arithmetic import wrong_sums
from core.design import (
    ROUND_IN, UNDER, corrected, decided_for_you, entries, entry_at, merge,
    nobody_carries_the_outside, read_off_the_wrong_view, render, said_at,
    settled_by_the_tracing, what_the_trace_says, where_the_direction_is,
)
from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, DESIGN, ERROR, GATE,
    OUTSTANDING, PART, PLAN, PLAN_REVISED, PREFLIGHT_FAILED, QUESTION,
    REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, UNBUILT,
    EventStream,
)
from core import part_file, preflight, sheet_reader, sketch_check
from core.freecad_tools import TRAPS
from core.llm import REASONING, ProviderRefused
from core.tools import ToolError

DESIGN_PHASE = "design"
PLAN_PHASE = "plan"
EXECUTE_PHASE = "execute"
ASSERTION_PHASE = "assertion"
REVIEW_PHASE = "review"

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

NOT_A_LOOK = ("apply_step", "review_views", "check_assertions")

_VIEW_NAMES = ("isometric", "the front", "above", "the right")


class PhaseError(Exception):
    """A phase gave back something the loop cannot act on."""


@dataclass
class Budgets:
    """
    What a run may spend, and the ceilings that make it terminate.

    `revisions` is the floor under `revisions_ceiling`, which scales the
    allowance with the Plan's length. Without it, Execute declaring Step after
    Step obsolete never terminates. It is one ceiling over all seven causes of
    a replan (see `_Revisions`): separate budgets would let them cycle into
    each other and would raise the worst-case number of Plan calls in a run.

    `rereads` is how many times a question may be put back to the drawing, and
    nothing else. A re-read is still a provider call, and without a ceiling a
    Plan that keeps asking and a drawing that keeps answering never terminates.
    How often Plan may be told to choose for itself is not bounded here: no run
    ends for want of an answer, so the count of questions nobody answered is
    not a ceiling on anything (ADR-0022).

    `looks_per_turn` bounds Execute's looks and Plan's alike: a question about
    the build is a look, and Plan asking one over and over would otherwise
    never terminate either. It is six because a look is the cheap turn — the
    provider replies with one line of JSON, where the turn that writes the code
    for a whole Plan composes every Step — and because two was not enough to
    reach the last Step of one: a recorded run spent both on the types it built
    with and was left declaring the eighth of eight Steps unbuildable for want
    of a material uuid one free call would have given it (FCC-146).
    """

    attempts_per_step: int = 3
    review_cycles: int = 2
    revisions: int = 3
    looks_per_turn: int = 6
    rereads: int = 3


def revisions_ceiling(budgets, steps) -> int:
    """
    How many replans this Plan is allowed, from how long it is (FCC-61).

    A four-Step Plan and a forty-Step Plan cannot have the same allowance: a
    long Plan has more places to hand back from, and every Execute -> Plan
    handback spends one. Still a single ceiling over all seven causes, for the
    reason recorded on `Budgets`; only its size is computed.
    """
    return max(budgets.revisions, len(steps) // 5)


class _Revisions:
    """
    How many times the Plan has been rewritten, and why (FCC-22).

    Seven unrelated things replan: the Plan leaves part of the description
    unplanned, a sum it states does not come out, the user amends at the Plan
    Gate, Execute finds a Step out of date, Execute cannot build one, the
    acceptance checks fail, and Review rejects the result. They share one ceiling, which is deliberate — but they
    used to share one *counter* too, so whichever branch spent the last unit was
    reported as the cause of all of them, and a run stopped by two amendments
    told the user Execute kept finding the Plan out of date.
    """

    UNCOVERED = "uncovered"
    MISCOUNTED = "miscounted"
    AMENDED = "amended"
    OBSOLETE = "obsolete"
    UNBUILDABLE = "unbuildable"
    ASSERTED = "asserted"
    REJECTED = "rejected"

    _SAID = (
        (UNCOVERED, "the Plan left part of the description unplanned %s"),
        (MISCOUNTED, "the Plan's own arithmetic did not come out %s"),
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

    def spent_total(self) -> int:
        return sum(self._spent.values())

    def named(self, cause) -> str:
        """The cause in words, without the count `breakdown` puts on it."""
        return (dict(self._SAID)[cause] % "").strip()

    def exhausted(self, budgets, steps) -> bool:
        return self.spent_total() >= revisions_ceiling(budgets, steps)

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
        """
        Why the run stopped, and what the budget went on.

        A ceiling of nought is a run that may not replan at all, and it has no
        breakdown to give. Where one cause spent the lot the breakdown has
        already named it, so the refusal is not repeated after it.
        """
        if not self._causes_spent():
            return "The Plan may not be rewritten in this run. " + refused
        reason = ("The Plan has been rewritten %d times, which is the whole budget — %s."
                  % (revisions_ceiling(budgets, steps), self.breakdown()))
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

    `standing` is approval that also covers the Plans this one turns into. A
    Plan is revised for six reasons and five of them are the run correcting
    itself — a Step Execute could not build, checks that did not hold, a part
    of the description nothing planned — and each revision stopped at the Gate
    for an approval the user had, in substance, already given. In a recorded
    session that came to sixty-nine minutes of a hundred and thirty-five spent
    waiting at a Gate, against six seconds of FreeCAD (FCC-101). A standing
    approval says "get on with it": the run stops asking about revisions of
    this Plan, and every other question it can put to the user — the user's own
    objects, a rebuild of the model, what to do about a result that did not
    pass — is untouched, because those are the ones that are not about the Plan.

    What it does not cover is a value nobody supplied that the user has not
    read. A Step states what it assumed precisely so a wrong guess is
    correctable at the Gate, and the guess is made again from nothing every
    time the Plan is revised. In a recorded session
    (`logs/sessions/141a41df15544013a9740a45138fa7ad.jsonl`, the 2023 pipe
    clamp) an undimensioned cutout was approved as a rectangle, came back from
    the first revision as a semicircular R15 and from the second as a circular
    R30 that swallowed the ring the clamp exists to close — and the user saw
    only the rectangle, because the approval stood. So a revision carrying an
    assumption they have not read reaches the Gate however standing the
    approval is: what stands is approval of the path, and an invented value is
    not the path.
    """

    decision: str = APPROVE
    feedback: str = ""
    standing: bool = False


def auto_approve(steps):
    """
    The gate when nothing is wired up to ask.

    Level 1, Level 2 and headless runs use this. The panel passes a real gate,
    and a real gate is the only reason a Step ever runs in front of a user.
    """
    return Approval(APPROVE)


ACCEPT = "accept"
REVISE = "revise"
START_OVER = "start_over"

DECISIONS = (ACCEPT, REVISE, START_OVER)

_WHAT_NOW = ("The result did not pass, and what to do about it is yours to say — take it "
             "as it is, have it revised, or start over.\n\n%s")

_DECIDED = {
    ACCEPT: "You took the result as it is.",
    REVISE: "You asked for it to be revised.",
    START_OVER: "You asked it to start over, which lets it delete what was built.",
}

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

_DECIDE = ("Nobody is asked for a value. Choose sensible values yourself, and write every "
           "value you chose into the prose of the Step that uses it, so a wrong choice is "
           "correctable at the Plan Gate.")

_SAID_NOTHING = "nothing in the views answers it."

_ABOUT_THE_BUILD = ("material", "materials", "uuid", "uuids", "card", "cards", "density",
                    "type", "types", "property", "properties", "enumeration", "spelling",
                    "spellings", "installed")

_LOOK_IT_UP = ("You asked for something no drawing prints — what this FreeCAD has, what a "
               "type is called, what a card carries. Nobody is asked and the sheet is not "
               "read: look it up with one of the Tools above and plan with what it says. "
               "Reply with {\"look\": {\"tool\": \"...\", \"arguments\": {...}}}.")

_LOOKED_AT_NOTHING = "no Tool here answers it."


def _a_question_for_the_build(question):
    """
    Whether a question is about this FreeCAD rather than about the part.

    A dimension, or which of two profiles governs, is a question the drawing
    can settle. A material card's uuid, a type's name, a property's spelling is
    not: no sheet prints one, and a recorded run spent every re-read it had
    putting "the UUID of an installed plain carbon steel material card" to the
    drawing before ending for want of an answer (FCC-149).
    """
    words = re.findall(r"[a-z]+", str(question).lower())
    return any(word in _ABOUT_THE_BUILD for word in words)


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
    """
    How a run ended, and what it leaves behind.

    `unmet` is what the acceptance checks said did not hold when the run ended,
    whatever the run ended as. A run the user took as it is still leaves these
    wrong in the Document, and the next run is entitled to know.
    """

    ok: bool = False
    blocked: bool = False
    verdict: str = None
    reason: str = ""
    steps_applied: int = 0
    tried: List[dict] = field(default_factory=list)
    unmet: List[str] = field(default_factory=list)

    @property
    def awaiting_user(self) -> bool:
        """Blocked means stopped and waiting, not failed and finished."""
        return self.blocked


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


def _ask(llm, events, phase, system, user):
    """
    One phase's turn: stream it, surface the reasoning as it forms, parse the reply.

    Reasoning is emitted chunk by chunk rather than aggregated at the end, so the
    user can tell early whether the assistant has understood them.

    A reply carrying no object is asked once more, quoting what it said. Every
    phase here answers in JSON and some models will not, or will not
    consistently: measured on the gateway, kimi-k3 replied to Execute with "I
    need to check the Hole feature's properties before writing the counterbore
    steps" — which is a *look*, and a look is a key of the object Execute
    replies with, so the model had the answer and wrote it as a sentence
    (`logs/sessions/ea390959745a453488138fddcc879635.jsonl`). One re-ask costs a
    turn; the alternative cost the run, ten approved Steps into it.

    Exactly one, and the second refusal is the phase failing as it did before.
    A model that will not answer in the shape twice is not going to on the
    third, and a loop of re-asks would spend the run proving it.

    Reasoning and then no answer is a third case, measured against the same
    gateway: kimi-k3 ends such a stream on "stop", under its own cap, having
    written reasoning and no answer. Running out of room is the other cause of
    an empty reply and arrives as a refusal, not as this.
    """
    body, thought = _streamed(llm, events, phase, system, user)
    if not isinstance(_object_of(body), dict):
        body, thought = _streamed(llm, events, phase, system,
                                  _in_the_shape_this_time(user, body))

    if thought and not body.strip():
        raise PhaseError(
            "%s streamed its reasoning and then stopped without answering. Send the "
            "Intent again, or pick a model that thinks less before it commits." % phase
        )
    return _as_json(body, phase)


def _streamed(llm, events, phase, system, user):
    """One turn, as the text it wrote and whether it reasoned before writing it."""
    text, thought = [], False
    for chunk in llm.stream([{"role": "system", "content": system},
                             {"role": "user", "content": user}]):
        if chunk.kind == REASONING:
            thought = True
            events.emit(THOUGHT, phase=phase, text=chunk.text)
        else:
            text.append(chunk.text)
    return "".join(text), thought


_IN_THE_SHAPE = (
    "Your last reply carried no JSON object, so nothing could be done with it. "
    "You said:\n%s\n\n"
    "Answer again, and put the JSON object this phase takes in the reply. Whatever you "
    "said above is either one of that object's own keys or it is not the answer: a phase "
    "that needs to look something up first asks for the look *in* the JSON, and a phase "
    "that wants to say why does it inside the object, not instead of it. Prose beside the "
    "object is read; prose without one is not."
)


def _in_the_shape_this_time(user, said):
    """
    The same request again, with what came back quoted and the shape restated.

    It quotes the model back to itself, because a directive about JSON in
    general is one the last reply already disobeyed, and it names the one thing
    that is nearly always what happened: the sentence *is* the answer, in the
    wrong clothes.
    """
    directive = _IN_THE_SHAPE % (said or "").strip()[:400]
    if isinstance(user, list):
        return list(user) + [{"type": "text", "text": directive}]
    return "%s\n\n%s" % (user, directive)


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
    """
    The reply as the object it carries, wherever in the reply that object is.

    A phase that answered correctly and then explained itself used to end the
    run. Measured on `logs/sessions/6fbd31e98fb540818086611841fa9aff.jsonl`:
    Design replied `{"design": {}, "question": ""}` and then a paragraph saying
    why the two strings it was asked about are the item numbers of a notes list
    and not dimensions — a right answer, a useful explanation, and a dead run,
    because the whole reply had to parse or none of it did.

    So the fence is tried, then the whole reply, then the first balanced object
    in it. Only an object: a reply with no JSON in it at all is still a phase
    that did not do its job, and it still says so quoting what was said.
    """
    parsed = _object_of(text)
    if parsed is None:
        raise PhaseError(
            "%s did not reply with JSON. It said: %s" % (phase, (text or "").strip()[:400])
        )
    if not isinstance(parsed, dict):
        raise PhaseError("%s replied with JSON that is not an object: %r" % (phase, parsed))
    return parsed


def _object_of(text):
    """The object a reply carries, by any of the three routes, or None."""
    fenced = _FENCE.search(text or "")
    candidate = (fenced.group(1) if fenced else (text or "")).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        return _the_object_in(candidate)


def _the_object_in(text):
    """
    The first balanced `{...}` in a reply, parsed, or None.

    Balanced rather than greedy: the last `}` in the reply belongs to whatever
    the model wrote last, which on a reply that ends in prose is a sentence with
    a brace quoted in it. Strings are stepped over so a brace inside one does
    not close the object.
    """
    start = text.find("{")
    while start != -1:
        depth, at, in_string, escaped = 0, start, False, False
        while at < len(text):
            character = text[at]
            if in_string:
                in_string = not (character == '"' and not escaped)
                escaped = character == "\\" and not escaped
            elif character == '"':
                in_string, escaped = True, False
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:at + 1])
                    except ValueError:
                        break
            at += 1
        start = text.find("{", start + 1)
    return None


def _pictures(attachments):
    return [picture for picture in map(_as_image, attachments) if picture]


def _sheets(attachments):
    """Every attachment this run could read as a sheet, in the order shown."""
    return [sheet for sheet in (sheet_reader.read(path) for path in attachments)
            if sheet is not None]


def _the_evidence(attachments):
    """
    What Design is shown: the sheet cut up and its text layer extracted.

    A drawing used to go out whole and at whatever resolution the provider chose
    to keep — 143 DPI for an 11 x 8.5 in sheet, which puts 14 px of ink in a
    dimension digit and misreads it occasionally rather than reliably. It now
    goes out as the sheet for the layout plus one picture per view at the DPI
    its own callouts ask for, with what is printed on it extracted from the file
    rather than read off the picture (FCC-102).

    An attachment `sheet_reader` cannot open is sent exactly as it was before.
    That is the whole fallback: no PyMuPDF, no Pillow, a format neither of them
    knows, and the phase is back to what it did yesterday.
    """
    shown = []
    for path in attachments:
        sheet = sheet_reader.read(path)
        if sheet is None:
            picture = _as_image(path)
            if picture:
                shown.append(picture)
            continue
        for piece in sheet_reader.shown(sheet):
            if "say" in piece:
                shown.append({"type": "text", "text": piece["say"]})
            else:
                picture = _as_image(piece["show"])
                if picture:
                    shown.append(picture)
    return shown


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
                 + _the_evidence(attachments))
    the_part = reply.get("design")
    return (the_part if isinstance(the_part, dict) else {},
            str(reply.get("question") or "").strip())


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
    """

    steps: list
    question: str = ""
    acceptance: list = field(default_factory=list)
    satisfies: list = field(default_factory=list)
    cites: list = field(default_factory=list)
    look: dict = None


def plan(intent, snapshot, llm, events, feedback=None, the_part=None, unanswered=(),
         revising=(), looking=(), looked=()):
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
    """
    request = {"intent": intent, "document": snapshot}
    if revising:
        request["the_plan_you_wrote"] = list(revising)
    if the_part:
        request["what_the_part_is"] = render(the_part)
        request["what_the_plan_must_satisfy"] = _acceptance_of(the_part)
    if feedback:
        request["what_went_wrong_last_time"] = feedback
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
    steps, satisfies, cites = _steps_of(reply.get("steps", []))

    look = reply.get("look")
    if not steps and looking and isinstance(look, dict) and look:
        return Plan(steps=[], look=look)

    question = str(reply.get("question") or "").strip()
    if not steps and question:
        return Plan(steps=[], question=question)
    if not steps:
        raise PhaseError("Plan produced no Steps for this Intent.")
    return Plan(steps=steps, satisfies=satisfies, cites=cites,
                acceptance=_acceptance_of(the_part))


def _addressed(acceptance):
    """
    {address: what it says} — the description as something to look a value up in.

    The acceptance is already one line per entry, `address: value (flag)`, and
    the address is what a Step cites. Split apart it is the lookup Execute
    needs; left as prose it is one more thing to read a number out of.
    """
    addressed = {}
    for entry in acceptance or ():
        address, _, value = str(entry).partition(": ")
        if value:
            addressed[address.strip()] = value.strip()
    return addressed


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
    steps, satisfies, cites = [], [], []
    for step in planned:
        if isinstance(step, dict):
            prose = str(step.get("do") or step.get("step") or "").strip()
            cited = [_as_written(entry) for entry in (step.get("satisfies") or ())
                     if _as_written(entry)]
        else:
            prose, cited = str(step).strip(), []
        if not prose:
            continue
        steps.append(prose + (" (satisfies: %s)" % ", ".join(_in_the_prose(entry)
                                                             for entry in cited)
                              if cited else ""))
        satisfies.extend(cited)
        cites.append(cited)
    return steps, satisfies, cites


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
    flag = re.search(r"\((?:read|derived|assumed)\)$", value)
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

    `unbuildable` is why a Step was left out, from a reply that wrote code for
    the others. A complaint that arrives beside working code is about that Step
    and no more, and it is the only thing that lets Plan write it differently.
    """

    steps: dict = field(default_factory=dict)
    obsolete: str = None
    unbuildable: str = None

    def for_step(self, number) -> ExecuteOutcome:
        return self.steps.get(number) or ExecuteOutcome()


def execute(steps, tools, llm, events, start=0, attempts=(), budgets=None, as_described="",
            looks=None, entries=()):
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

    `entries` is that same description addressed — the address a Step cites
    against the value it resolves to. A Step names what it satisfies and the
    value it needs is then a lookup rather than a reading of prose. In the run
    that named this the lookup was missing and the values were invented from
    the geometry to hand: `arm_z = sum((g.StartPoint.y + g.EndPoint.y) * 0.5
    for g in arm_lines) / len(arm_lines)` for a hole the description places on
    the longitudinal centre plane (FCC-115).

    What the run already knows is handed over before Execute can ask for it
    again, which is the whole point of keeping it: a look it does not have to
    take is a provider turn it does not have to spend. A look still spends the
    allowance even where the answer was already here — what it costs is the
    turn, not the Tool call — so the saving is in not needing to ask, which is
    what `Looks.still_true` is for.

    A reply that wrote code is not a verdict on the Plan: whatever it objects
    to, it objects to about the Steps it left out, and those go back to Plan
    one at a time. Voiding the whole Plan there is how a recorded run threw
    away Steps nobody had attempted and replanned three times over with nothing
    applied.
    """
    budgets = budgets or Budgets()
    request = {"steps": [{"step": number, "do": prose}
                         for number, prose in enumerate(steps[start:], start=start + 1)],
               "document": tools.call("snapshot")}
    if as_described:
        request["what_the_part_is"] = as_described
    if entries:
        request["what_each_entry_says"] = _addressed(entries)
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
        again = _the_same_way_twice(attempts)
        if again:
            request["you_have_failed_this_the_same_way_twice"] = again

    looked = list(looks.still_true()) if looks is not None else []
    for remaining in range(budgets.looks_per_turn, -1, -1):
        request["tools_you_may_look_with"] = _lookable(tools) if remaining else []
        if not remaining:
            request["this_is_your_last_turn"] = (
                "Your looks are spent, so the list above is empty: every Tool still exists "
                "and none was withheld. Reply with code for every Step you can write. A "
                "look now is a failed attempt. `obsolete` is for a Plan that no longer "
                "matches the Document above — never for one Step you could not look a "
                "value up for, because it throws away the Steps you can build with it. "
                "Leave that Step out instead and say why in `unbuildable`: the Steps "
                "before it land, and it goes back to Plan on its own."
            )
        if looked:
            request["what_you_looked_at"] = looked

        reply = _ask(llm, events, EXECUTE_PHASE, prompt(EXECUTE_PHASE),
                     json.dumps(request, indent=2, default=str))

        written = _written(reply.get("steps"), start)
        if written:
            return Written(steps=written,
                           unbuildable=str(reply.get("unbuildable") or reply.get("obsolete"))
                           if (reply.get("unbuildable") or reply.get("obsolete")) else None)
        if reply.get("obsolete"):
            return Written(obsolete=str(reply["obsolete"]))
        if reply.get("unbuildable"):
            return Written(unbuildable=str(reply["unbuildable"]))
        if not reply.get("look") or not remaining:
            break
        looked.append(_look(tools, reply["look"], events, looks))

    return Written()


_FAILED_THIS_WAY = (
    ("neither removed nor added any material",
     "the cut ran the wrong way or missed the solid. It is not the size of the cut "
     "and it is not how the profile is built: the same shape, extruded the other "
     "way, is the whole of the fix. Change the *direction* this time — set "
     "`Reversed`, set `Type='ThroughAll'` or `Midplane`, or put the sketch on the "
     "opposite face — and check the sign against the Body's BoundBox before you "
     "commit to it. Rewriting the same extrusion more legibly will fail "
     "identically."),
    ("left a Body in more than one piece",
     "the profiles in one sketch do not meet, so they padded into separate lumps. "
     "Renaming the features does not join them. Either make the profiles share "
     "their endpoints so the outline closes as one loop, or build each piece as "
     "its own feature on the solid that is already there."),
    ("recompute left these objects in error",
     "the Document does not rebuild. The object named in the traceback is the one "
     "to fix, and it is fixed by changing what it references or what it is given "
     "— not by building the same thing again under another name."),
    ("is not part of the enumeration",
     "the spelling is not one this build accepts. Ask `describe_type` for the "
     "property's enumeration and use a value from it verbatim; guessing a second "
     "spelling is the same move that failed."),
    ("end face of tapered extrusion is empty",
     "the draft angle closes the profile before the extrusion gets to the end of "
     "it. A smaller angle, a shorter cut, or a larger profile is the change; the "
     "same three numbers arranged differently is not."),
)


def _the_same_way_twice(attempts):
    """
    The directive for a Step that has now failed twice for the same reason, or
    None.

    Only the *last two* attempts, and only where they are the same kind of
    failure. Two different failures in a row is progress — the first thing was
    fixed and a second one surfaced — and telling a run that is getting
    somewhere to change tack would be wrong.

    `_FAILED_THIS_WAY` pairs each failure with what a *different* attempt at it
    would have to change. It is keyed on a phrase Preflight itself writes, so a
    failure this build stops reporting stops being classified rather than being
    classified wrongly. Naming them at all is the answer to a recorded run that
    failed the same Step three times over with the identical "moved no
    material" traceback: each rewrite renamed the objects, restructured the
    loop that built the profiles and changed nothing about the direction of the
    cut the traceback was describing, then it replanned and did it again. The
    traceback said what to change; what it did not say was that the answer
    being tried had already been tried.
    """
    if len(attempts) < 2:
        return None
    latest, before = attempts[-1].traceback or "", attempts[-2].traceback or ""
    for phrase, directive in _FAILED_THIS_WAY:
        if phrase in latest and phrase in before:
            return (
                "This Step has now failed %d times with the same failure, and the attempts "
                "differ from each other in ways the traceback never asked about. Do not "
                "write a third variation of the same approach: %s\n\nIf you cannot see a "
                "different approach that the Steps as written allow, leave this Step out "
                "rather than repeating yourself — the Steps before it land and Plan is "
                "given this one back to write differently."
                % (sum(1 for a in attempts if phrase in (a.traceback or "")), directive)
            )
    return None


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


def _emit_plan(events, kind, planned, reason=None, unanswered=(), decided=(),
               **counted):
    """
    A Plan on the stream, in one place — the panel reads both kinds alike.

    `unanswered` rides with it because a Plan that chose its own values is the
    only place those choices are correctable, and the Gate cannot show what it
    was never handed (FCC-96). `decided` is the same argument for the values the
    model settled before Plan ever saw them: nothing is asked between the
    attachment and the Gate, so the Gate is where they are read (ADR-0022).

    `counted` is which revision this is, of how many, and what caused it. A
    revised Plan used to carry the reason alone, so three Revised Plans running
    into the same wall read as three fresh starts and the run looked like it
    had stalled for no reason.
    """
    extra = {"reason": reason} if reason is not None else {}
    events.emit(kind, steps=list(planned.steps), acceptance=list(planned.acceptance),
                unanswered=list(unanswered), decided=list(decided), **extra, **counted)


def _review_views(tools, events):
    """
    The four viewport views for Review, or nothing where there is no GUI.

    Driven by the loop, once per run, because a picture is not something a
    phase gets to ask for (ADR-0015). A build without the Tool at all — an
    older registry, a test registry — falls through to the same nothing as a
    headless one, and Review keeps the Worker's single render.

    A Tool that fails is narrated and then let go: a finished run is not lost
    over its photograph.
    """
    if "review_views" not in tools:
        return []
    events.emit(TOOL_CALL, name="review_views", arguments={})
    try:
        result = tools.call("review_views", {})
    except ToolError as exc:
        events.emit(TOOL_RESULT, name="review_views", result={"error": str(exc)})
        return []
    events.emit(TOOL_RESULT, name="review_views", result=result)
    return list(result.get("views") or ())


class Looks:
    """
    Every look this run has taken, so none of them is paid for twice (FCC-99).

    A look is not expensive because the Tool is slow — the Tool answers in
    milliseconds. It is expensive because it costs a whole turn: Execute
    replies with a question instead of code, the loop answers it, and Execute
    is asked again. A recorded run asked what the same scratch object had eight
    times and what `PartDesign::Pocket` has five, across the regenerations of
    one Plan, and each of those thirteen answers had already been given. At two
    minutes a turn that is most of half an hour spent re-reading a property
    list that had not changed.

    So the answers are kept and handed to Execute *before* it asks. Which of
    them are still true is the only real question here, and it has two answers.
    What a *type* has is a fact about this build: it cannot change while
    FreeCAD is running, so it is true for the whole run. What an *object* has
    is a fact about the Document, and every applied Step is a chance for it to
    have changed — so those are kept only until the next Step lands, and after
    that they are dropped rather than repeated as though they still held.

    `ABOUT_THE_BUILD` names the looks whose answer is a property of the build
    and not of the Document. `CARRIED` is how many looks Execute is handed
    unasked: beyond it the oldest go, because what was looked at most recently
    is what the Steps being written now are about, and an unbounded list would
    grow a request the whole run long.
    """

    ABOUT_THE_BUILD = ("describe_type", "supported_types", "materials")

    CARRIED = 8

    def __init__(self):
        self._taken = []
        self._step = 0

    def a_step_landed(self):
        """A Step applied, so what was read off the Document may have moved."""
        self._step += 1

    def _key(self, name, arguments):
        return name, json.dumps(arguments, sort_keys=True, default=str)

    def _current(self, name):
        return None if name in self.ABOUT_THE_BUILD else self._step

    def remember(self, name, arguments, result):
        key = self._key(name, arguments)
        self._taken = [taken for taken in self._taken if taken["key"] != key]
        self._taken.append({"key": key, "step": self._current(name), "tool": name,
                            "arguments": arguments, "result": result})

    def answer(self, name, arguments):
        """
        What this run already knows about this exact look, or None.

        Only where it is still true: an answer read off a Document two Steps
        ago is not an answer about this one.
        """
        key = self._key(name, arguments)
        current = self._current(name)
        for taken in reversed(self._taken):
            if taken["key"] == key and taken["step"] == current:
                return taken["result"]
        return None

    def still_true(self):
        """
        What Execute is given without having to ask, most recent last.

        Everything about the build, and everything read off the Document since
        the last Step landed.
        """
        standing = [taken for taken in self._taken
                    if taken["step"] is None or taken["step"] == self._step]
        return [{"tool": taken["tool"], "arguments": taken["arguments"],
                 "result": taken["result"]}
                for taken in standing[-self.CARRIED:]]


def _look(tools, look, events, looks=None):
    """
    Run one read-only Tool for Execute and hand back what it said.

    A look this run has already taken and still holds is answered from what it
    said then, without calling the Tool again. It is still narrated, because a
    transcript that showed the question and not the answer would read as a look
    that went nowhere.

    `NOT_A_LOOK` is refused here rather than run: one of those Tools writes to
    the Document, one moves the user's camera and one belongs to a phase of its
    own, so none of them is a look. A Tool that raises is answered with its
    error, because a bad look is a thing to correct, not a reason to lose the
    Step.
    """
    name = str(look.get("tool") or "")
    arguments = look.get("arguments") or {}

    events.emit(TOOL_CALL, name=name, arguments=dict(arguments))
    remembered = looks.answer(name, arguments) if looks is not None else None
    if remembered is not None:
        result = remembered
    elif name in NOT_A_LOOK or name not in tools:
        result = {"error": "%r is not a Tool you can look with. Reply with code instead." % name}
    else:
        try:
            result = tools.call(name, arguments)
        except ToolError as exc:
            result = {"error": str(exc)}
        if looks is not None and "error" not in result:
            looks.remember(name, arguments, result)
    events.emit(TOOL_RESULT, name=name, result=result,
                already_known=remembered is not None)

    return {"tool": name, "arguments": arguments, "result": result,
            "answered_from": "what you were already told" if remembered is not None else None}


def _what_this_build_has(tools):
    """
    The looks nobody should have to spend a turn on, taken before Execute is.

    The material cards are a fact about the build: true from the first Step to
    the last, and the Tool answers off a list FreeCAD already holds. Drawing
    them from the same allowance as a question about the Document is what left
    a recorded run with its looks spent at the Step that assigns a material,
    being told a look now was a failed attempt — so it declared the Step
    unbuildable and said the Tool was unavailable (FCC-146).

    Unfiltered, because a page of cards is what the request wants to carry: a
    name to match against was the question in the first place. A budget the
    model does not have to spend is the only one it cannot run out of at the
    last Step.

    Not narrated, because nothing asked: this is knowledge the run is handed
    with the request, not a question anybody put and an answer that came back.
    A build whose Tools do not hold the cards is handed nothing, which is the
    case every registry without a `materials` Tool is already in.
    """
    looks = Looks()
    if "materials" in tools:
        try:
            looks.remember("materials", {}, tools.call("materials", {}))
        except ToolError:
            pass
    return looks


@dataclass
class Checked:
    """
    What the Assertion phase made of the finished Document.

    `failed` is a model that did not meet its acceptance list; `traceback` is
    what Plan is told. A check that could not be written is neither passed nor
    failed — `confirmed` is empty, `failed` is False, and Review judges the
    whole list alone.

    `unmet` is the entries that did not hold, named one by one. The traceback
    says the same thing in prose, and prose is what Plan reads; this is what
    survives the end of the run.
    """

    failed: bool = False
    traceback: str = ""
    confirmed: list = field(default_factory=list)
    uncovered: list = field(default_factory=list)
    unmet: list = field(default_factory=list)



_A_SIZE = re.compile(r"(-?\d+(?:\.\d+)?)\s*mm")
_TRUE_TO = 0.5


def the_envelope_measured(acceptance):
    """
    The check nobody had to write: the part's three overall sizes.

    Preflight measures topology — objects in error, a feature that moved no
    material, a Body in pieces — and never a dimension, so a lug built 25 mm
    tall where the drawing says 30, and a split that stops 16 mm short of the
    end face it has to reach, both came back as success
    (`logs/sessions/549e6232c9e04b659e5a2d536354bd7a.jsonl`).

    Sorted against sorted, deliberately. Which way round the part was built is
    not something anything in this codebase knows — the description names its
    directions in words and the Document has X, Y and Z — and a check that had
    to know would be a check that guessed. What it can say without guessing is
    that a part 108 x 30 x 30 has been built and not one 108 x 30 x 50, which
    is the failure worth catching and the one an assertion written by a
    provider is least likely to cover.

    Empty where the envelope does not carry three plain sizes: a part described
    in two directions, or in words with no millimetres in them, is not one this
    can measure, and inventing a third would fail every run that has one.

    `_A_SIZE` is what a size looks like in the words the envelope writes them
    in, and `_TRUE_TO` is how close a built one has to be: half a millimetre,
    because a drawing this reads is dimensioned to a tenth and the fillets on
    an outer contour move a bounding box by less than that.

    `_ENVELOPE_CHECK` is written out rather than built up, because it is read
    by whoever reads a failed run. It measures `optimalBoundingBox`, since a
    triangulated one lands inside the true extreme on a curved face, which is
    the whole of ADR-0022's argument.
    """
    sizes = []
    for address, value in _addressed(acceptance).items():
        if address.startswith("envelope" + UNDER):
            found = _A_SIZE.findall(value)
            if len(found) == 1:
                sizes.append(float(found[0]))
    if len(sizes) != 3:
        return ""
    return _ENVELOPE_CHECK % (sorted(sizes), _TRUE_TO, sorted(sizes), _TRUE_TO)


_ENVELOPE_CHECK = """
solids = [o for o in doc.Objects if getattr(o, 'Shape', None) is not None
          and o.Shape.Solids and not o.Shape.isNull()]
assert solids, 'the acceptance names an envelope and the Document has no solid'
shape = max(solids, key=lambda o: o.Shape.Volume).Shape
box = shape.optimalBoundingBox()
built = sorted([box.XLength, box.YLength, box.ZLength])
asked = %s
assert all(abs(a - b) <= %s for a, b in zip(built, asked)), (
    'these did not hold: the envelope. The description asks for a part measuring '
    + ' x '.join('%%.2f' %% one for one in %s) + ' mm overall and the one built '
    'measures ' + ' x '.join('%%.2f' %% one for one in built) + ' mm, which is more '
    'than %s mm out. The sizes are sorted, so this says nothing about which way '
    'round it was built - only that it is not that part.')
"""


def assertion(acceptance, described, tools, llm, events, budgets=None, cancelled=None,
              after_a_retry=()):
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

    The envelope is measured first and without asking anybody: three sizes
    against three sizes is arithmetic, and a run that built the wrong part
    should not have to wait for a provider to say so. The entries a retry
    touched go with the request, because a Step that failed a check and passed
    on the retry is a Step whose feature was changed to get past it —
    shortened, moved, turned around — and passing the second time says the
    workaround was legal, never that it is still the feature the drawing asked
    for; those are the entries to measure rather than to take on trust.

    `cancelled` is asked between attempts the way Execute's is: three provider
    calls is long enough that Stop must not wait them out. Checks that could
    not be written at all are neither a verdict on the part nor a reason to
    lose a finished run — Review judges the whole list instead.
    """
    budgets = budgets or Budgets()
    request = {"acceptance": list(acceptance),
               "the_document_as_it_now_stands": described}
    if after_a_retry:
        request["entries_a_retry_touched"] = list(after_a_retry)

    cancelled = cancelled or (lambda: False)

    measured = the_envelope_measured(acceptance)
    if measured:
        events.emit(TOOL_CALL, name="check_assertions", arguments={"code": measured})
        result = tools.call("check_assertions", {"code": measured})
        events.emit(TOOL_RESULT, name="check_assertions", result=result)
        if not result.get("ok"):
            traceback = result.get("traceback") or "The envelope check failed."
            unmet = _unmet_in(traceback) or ["the envelope"]
            events.emit(ASSERTION, ok=False, code=measured, confirmed=[],
                        uncovered=[], traceback=traceback, unmet=unmet)
            return Checked(failed=True, traceback=traceback, unmet=unmet)

    attempts = []
    for _ in range(budgets.attempts_per_step):
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
            unmet = _unmet_in(traceback) or list(acceptance)
            events.emit(ASSERTION, ok=False, code=code, confirmed=[],
                        uncovered=uncovered, traceback=traceback, unmet=unmet)
            return Checked(failed=True, traceback=traceback, uncovered=uncovered,
                           unmet=unmet)

        attempts.append({"code": code, "traceback": traceback})

    events.emit(ASSERTION, ok=None, code="", confirmed=[],
                uncovered=list(acceptance),
                traceback=attempts[-1]["traceback"] if attempts else "")
    return Checked(uncovered=list(acceptance))


_MODEL_IS_INVALID = "recompute left these objects in error"

_DID_NOT_HOLD = "these did not hold: "


def _unmet_in(traceback):
    """
    The acceptance entries a failed check named, one per entry.

    The checks end in a single assert whose message lists every entry that did
    not hold, separated by semicolons — the Assertion prompt specifies that
    shape, so this reads it rather than guessing at one. A Document that does
    not recompute has no entry list at all: nothing was measured, so nothing
    can be named, and the caller falls back to the whole list.

    Parsed rather than returned as data because the checks are code the model
    writes and runs in another process; what comes back is a traceback.
    """
    for line in traceback.strip().splitlines():
        if _DID_NOT_HOLD not in line:
            continue
        listed = line.split(_DID_NOT_HOLD, 1)[1]
        return [entry.strip() for entry in listed.split(";") if entry.strip()]
    return []


def _the_model_failed(traceback):
    """
    Whether this traceback is about the part or about the code that checked it.

    Two ways the part is at fault. An `AssertionError` is an entry of the list
    that did not hold. A Document the copy cannot recompute is a part already
    broken before anything was measured — that one has to read differently in
    the feedback, or Plan is told a dimension is wrong when the model is in
    error.

    Any line rather than the last one, because the checks now end in a single
    assert naming every entry that failed, and a message listing forty of them
    is not going to stay on one line. A traceback's own frames are indented, so
    the only line that can begin with the exception name is the exception.
    """
    if _MODEL_IS_INVALID in traceback:
        return True
    return any(line.startswith("AssertionError")
               for line in traceback.strip().splitlines())


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

    The views arrive in the order `capture_review_views` writes them, which is
    what `_VIEW_NAMES` names them by for Review's own prose. What the checks
    confirmed and what they could not cover go with them, so the verdict is
    about what the checks did not cover rather than a second opinion on what
    they did (ADR-0015).
    """
    request = {"intent": intent, "the_document_as_it_now_stands": described}
    if checked is not None:
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


class _Asking:
    """
    What has been asked in this run, and of whom.

    Run-wide rather than per Plan. Every counter here used to be a local of
    `_plan_once_it_knows_enough`, so a re-plan — which is a new Plan but not a
    new run — reset the lot: the same question went to the user twice, the
    re-read ceiling refilled, and "asked again after being told to choose"
    meant one thing on the first Plan and something else on the fourth
    (FCC-96).

    `unanswered` is the questions nobody answered, in the order they were
    raised. Plan is handed all of them every time, and the Plan Gate is shown
    them beside the Steps that chose without them. `re_aimed` is how many times
    a Plan has been sent back for building two directions in one Step: run-wide
    like the rest, because a Plan that keeps mixing them would otherwise be
    sent back for ever.

    `looked_up` is what the Tools said to the questions the drawing could not
    answer, and `looks` is how many of them were taken. Both are run-wide for
    the same reason as the rest: an answer already looked up is handed to every
    later Plan rather than looked up again.
    """

    def __init__(self):
        self.unanswered = []
        self.looked_up = []
        self.read_again = 0
        self.looks = 0
        self.re_aimed = 0


def run(intent, *, llm, tools, events=None, budgets=None, cancelled=None, attachments=(),
        gate=auto_approve, ask=nobody_to_ask, decide=revise_by_default,
        outstanding=()) -> LoopResult:
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

    `outstanding` is what the acceptance checks of an earlier run on this same
    Document said did not hold and nobody fixed. A run that inherits it is told
    so before it plans anything, and carries it in its own acceptance list, so
    a second drawing is not built on top of a part that already failed its
    first (FCC-98). It comes back out on the result, still outstanding or not,
    for whoever drives the next run.
    """
    events = events if events is not None else EventStream()
    budgets = budgets or Budgets()
    progress = {"applied": 0, "tried": [], "unmet": list(outstanding), "satisfied": []}

    def outcome(result):
        """
        Stamp what is still wrong with the Document onto whatever the run
        returned.

        Every exit carries it, including the ones that raised: a run stopped
        halfway leaves the Document exactly as wrong as the last check found
        it, and that is the state the next run inherits. Done in one place
        because there are nine ways out of `_run` and the eight that are not
        the happy one are the ones that matter here.
        """
        result.unmet = list(progress["unmet"])
        return result

    try:
        return outcome(_run(intent, llm, tools, events, budgets,
                            cancelled or (lambda: False), gate, progress, attachments,
                            ask, decide, list(outstanding)))
    except Stopped:
        return outcome(_block(events, "Stopped by you. Everything applied so far is still in "
                                      "the Document; undo it Step by Step if you want it "
                                      "gone.", [], progress["applied"]))
    except _OutOfRevisions as spent:
        return outcome(_block(events, spent.reason, spent.tried, progress["applied"]))
    except ProviderRefused as exc:
        return outcome(_block(events, str(exc), progress["tried"], progress["applied"],
                              said=exc.said))
    except PhaseError as exc:
        events.emit(ERROR, message=str(exc))
        return outcome(LoopResult(ok=False, reason=str(exc)))
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        return outcome(LoopResult(ok=False, reason=str(exc)))


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

    Design owns the question (ADR-0019), and this is the one that survives
    (ADR-0022): where its own model says a value is not merely empty but
    unknowable from the sheet, it asks, once, before any Step has been drafted.
    The run waits, because it is the only question left and so it may afford
    to. Unanswered it is not fatal — the model settles the value like every
    other, and the choice rides to the Gate.

    With nothing to look at there is no turn at all: Design then holds only what
    the Intent supplies, and the run costs exactly what it costs without a
    drawing.
    """
    if not _pictures(attachments):
        return {}

    the_part, question = design({"intent": intent}, llm, events, attachments)
    the_part = merge({}, the_part)

    if question:
        if cancelled():
            raise Stopped()
        events.emit(QUESTION, text=question)
        answer = _answered(question, ask, events)
        if answer:
            the_part, _ = _written_in(the_part, question, answer, llm, events,
                                      attachments)

    the_part = _every_callout_accounted_for(the_part, attachments, llm, events,
                                            budgets, cancelled)
    the_part = _the_outside_is_carried(the_part, attachments, llm, events, budgets,
                                       cancelled)

    if the_part:
        events.emit(DESIGN, model=the_part)
    the_part = _the_sweep_is_the_sheets(the_part, attachments)
    the_part = _the_directions_are_the_drawings(the_part, attachments, llm, events,
                                                budgets, cancelled)
    the_part = _every_number_is_the_sheets(the_part, attachments, llm, events,
                                           budgets, cancelled)
    disagreements = _the_geometry_disagrees(attachments, events)
    the_part = merge(the_part, dict(the_part, conflicts=disagreements)
                     if disagreements else the_part)
    the_part = _every_direction_settled(the_part, _round_evidence(attachments))
    the_part = _the_tracing_governs(the_part)
    the_part = _written_down(the_part, attachments, events)
    return the_part


def _every_number_is_the_sheets(the_part, attachments, llm, events, budgets,
                                cancelled):
    """
    A number on the description that is on no sheet, put back to the drawing.

    `_every_callout_accounted_for` asks which of the sheet's numbers the
    description never used. This asks the other direction, which is the one
    ADR-0019 is named after: the run that read a depth of 100 off a section
    planned 150 x 50 x 50 two turns later, and the 50 was on nothing.

    Measured over the 32 descriptions recorded in `logs/sessions`, each against
    the sheet its own run was given: 4 to 8 numbers fire. Held against each of
    the other three drawings, they invent 150 to 180 in total, which is why
    `_off_the_sheet` intersects. `tests/test_modelmania_evidence.py` produces
    both bands.

    Put to the drawing once and then left on the part as a conflict. Nothing is
    settled and no run stops: the number may be right and derived from
    something this cannot see, and the Gate is where a person says so.
    """
    found = _off_the_sheet(attachments, the_part)
    if not found:
        return the_part

    if budgets.rereads:
        if cancelled():
            raise Stopped()
        grew, _ = design({"the_part_as_you_described_it": the_part,
                          "the_question": _where_is_this_number(found)},
                         llm, events, attachments)
        if grew:
            the_part = merge(the_part, grew)
        found = _off_the_sheet(attachments, the_part)

    return merge(the_part, {"conflicts": [
        {"about": "the value %g on this part" % number,
         "stands": "no callout prints it, no dimension line measures it and "
                   "nothing traced is that size",
         "was_also_said": "the drawing"}
        for number in found]}) if found else the_part


def _off_the_sheet(attachments, the_part):
    """
    The numbers every sheet of this run says are on no sheet.

    `invented` is asked of one sheet, which is the right level for it: a sheet
    either prints a number or does not. Over several attachments the answers
    intersect — a number printed on the second sheet is on a sheet, and the
    first sheet not printing it is what a two-sheet drawing looks like.
    `_uncited` unions instead, because every callout on every sheet has to be
    accounted for by the one description.
    """
    said = [set(sheet_reader.invented(sheet, the_part))
            for sheet in _sheets(attachments)]
    return sorted(set.intersection(*said)) if said else []


def _where_is_this_number(found):
    return (
        "These numbers are on your model and on no sheet: %s. Each one is "
        "either printed somewhere you can point at, or measured between two "
        "faces the drawing dimensions, or worked out from numbers that are — "
        "in which case say which, and mark it `derived`. Where it is none of "
        "those, it was assumed, and an assumption belongs in `not_defined` "
        "with what you looked in. Reply with those fields only."
        % ", ".join("%g" % number for number in found))


A_READING_BUDGET = 60.0


def _the_geometry_disagrees(attachments, events, budget=A_READING_BUDGET):
    """
    What the sheet prints against what the sheet draws, as conflicts.

    The reading is a closed walk of lines and arcs in millimetres now
    (FCC-118), the sheet's own distances reach the faces they measure
    (FCC-121), and `sketch_check` builds the one against the other. Nothing
    consumed the verdict: a sheet whose digit was misread by 8 mm arrived at
    the Plan Gate exactly as silent as a sheet that agrees with itself.

    Recorded, never settled, and no ladder. Which of the two is wrong — the
    digit, or a face the tracer took to the inside of its own line — is not
    something a re-read can be asked and not something this can decide, so it
    goes on the part in the words of both and the user settles it at the Gate.
    That is what `conflicts` is, and it is the only phase that has both numbers
    in front of it.

    Measured on `tests/modelmania`: 11 printed distances reach a traced outline
    across the four sheets, and none becomes a disagreement. A run against them
    gains no conflict, which is the right answer rather than a weak check: the
    sheets agree with themselves.

    And it is a floor rather than a net. A dimension reaches the geometry
    through the feet of its own extension lines, so a tracing further out than
    `sketch_check.A_FOOT` has no face for the foot to name and there is nothing
    to compare — this cannot catch a digit misread by 8 mm, and widening the
    band to reach one writes wrong faces instead: measured, a band of a tenth
    of the printed value produces three disagreements of 4.5 to 4.9 mm on
    sheets known to agree with themselves. The misread digit is caught where it
    needs no correspondence at all, by the sheet measuring its own numbers
    (`Printed.measures`) and by `uncited`.

    An outline the solver could not be asked about is said out loud, for the
    same reason a re-read that found nothing says so: a sheet nothing was wrong
    with and a sheet nobody could check both arrive at the Gate with no
    conflict on them, and only one of the two is a verdict. A child that never
    came up, a child that died and could not be replaced, and a budget that ran
    out are all that same sentence: those outlines are unchecked and the run
    goes on. Nothing here may end a run — that is what makes it a reading check
    and not a Step.

    `budget` is the phase's, not the outline's. A timeout each is no bound at
    all: a sweep can spend one timeout for every outline without returning a
    verdict. The four sheets contain 142 outlines, so the phase's minute is a
    child that has stopped answering rather than a normal reading time.
    """
    traced = _every_outline_traced(attachments)
    if not traced:
        return []

    worker = _a_freecad_of_our_own()
    found = []
    if worker is None:
        unchecked = [view for outline, view, _ in traced if outline.entities]
    else:
        unchecked = []
        spent_by = time.monotonic() + budget
        try:
            for outline, view, between in traced:
                left = spent_by - time.monotonic()
                if left <= 0:
                    if outline.entities:
                        unchecked.append(view)
                    continue
                found.extend(_what_the_solver_said(
                    worker, outline, view, between, unchecked,
                    min(left, preflight.DEFAULT_TIMEOUT)))
        finally:
            worker.kill()
    if unchecked:
        events.emit(ERROR, message="The solver could not be asked about %d of the "
                                   "outlines read off these sheets: %s. The run goes "
                                   "on; those outlines are unchecked."
                                   % (len(unchecked), ", ".join(sorted(set(unchecked)))))
    return found


def _every_outline_traced(attachments):
    """
    Every outline read off every sheet, with the view and the distances it
    belongs to, before any child is started.

    Read first so that a child which never comes up still knows what it was
    going to be asked about: "nothing was checked" is only sayable by something
    holding the list of what there was.
    """
    traced = []
    for path in attachments:
        sheet = sheet_reader.read(path)
        if sheet is None:
            continue
        for drawn in sheet.drawn:
            traced.extend((outline, drawn.view, sheet.between)
                          for outline in drawn.outlines)
    return traced


def _what_the_solver_said(worker, outline, view, between, unchecked, timeout=None):
    """
    One outline's verdict, as the conflicts it puts on the description.

    A constraint the solver calls redundant reads the same way here as one it
    calls conflicting: both are a reading nobody in this phase can settle —
    which of the two printed numbers to drop is not something a re-read can be
    asked — so both go to the Gate, in the same words.
    """
    verdict = sketch_check.checked_by(worker, outline, view, between, timeout)
    if verdict is None:
        if outline.entities:
            unchecked.append(view)
        return ()
    found = [{"about": "%s, in %s" % (trouble.said, view),
              "stands": "the sheet prints it",
              "was_also_said": "the view draws it otherwise"}
             for trouble in verdict.disagrees]
    found.extend({"about": "%s, in %s" % (trouble.said, view),
                  "stands": "the solver cannot satisfy this reading"}
                 for trouble in verdict.conflicts + verdict.redundant)
    return found


def _a_freecad_of_our_own():
    """
    A headless FreeCAD to build the reading in, or nothing.

    Never the live one, and never this thread. `sketch_check` makes a Document,
    writes Sketcher constraints into it and solves — and `_describe_the_part`
    runs on the loop's worker thread, where a Document mutation takes FreeCAD
    down with it (`core/apply.py`, FCC-126). One warm child for the whole
    sweep: four sheets are 142 outlines, and a process each is a process each.

    Nothing to start is not an error, and the caller says so on the transcript:
    a machine with no headless FreeCAD reads its drawing exactly as it does
    today, but a check that ran on no outline at all is not allowed to look
    like a check that found nothing.
    """
    try:
        worker = preflight.PreflightWorker()
        worker.start()
        return worker
    except Exception:
        return None


def _round_evidence(attachments):
    """What every attached sheet was traced curved at, view by view."""
    found = {}
    for path in attachments:
        found.update(sheet_reader.round_evidence(sheet_reader.read(path)))
    return found


def _the_directions_are_the_drawings(the_part, attachments, llm, events, budgets,
                                     cancelled):
    """
    A direction read off the wrong view put back to the drawing before anybody
    settles it for it.

    The hard stop below was right about the finding and wrong about the remedy.
    The recorded run (`logs/sessions/daa17fb149474317840ef98439cfef6f.jsonl`,
    the 2023 pipe clamp) was stopped before the Plan Gate by four findings, and
    all four were the check's own fault: two judged a saw cut and an open
    cutout as though they were round, and two held the Ø42 bore to account for
    a view whose circle the tracer had in pieces. The description had read the
    sheet correctly and the run died for it, with nothing between "something is
    wrong" and "stop".

    So the finding is worked, the way the outer contour is: put back to Design
    with what the sheet actually measures — the view each circle was traced in
    and at what width — because that is a thing a re-read can act on and "one of
    these is wrong" is not. Nobody is asked and nothing stops here: what the
    drawing does not settle, `_every_direction_settled` settles off the same
    measurement (ADR-0022).

    A re-read may overrule here, which nothing else in the phase may do. It is
    the merge's rule that made this unfixable — filled stays filled, so a wrong
    axis cannot be talked out of itself — and the exception is exactly as wide
    as the evidence: one address, named by a finding, carrying the measurement
    that earned it, with the displaced value kept beside it (`corrected`).

    The re-reading stops where it stops finding fewer: a look that changed
    nothing will not change anything.
    """
    round_here = _round_evidence(attachments)
    found = read_off_the_wrong_view(the_part, round_here)
    looked = 0
    while found and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"the_part_as_you_described_it": the_part,
                          "the_question": _look_at_the_view_again(found)},
                         llm, events, attachments)
        if not grew:
            break
        the_part = _corrected_by(the_part, grew, found, round_here)
        the_part = merge(the_part, grew)
        still = read_off_the_wrong_view(the_part, round_here)
        if len(still) >= len(found):
            break
        found = still

    return the_part


def _corrected_by(the_part, grew, found, round_here):
    """
    The re-read written where the directions the findings are about live, and
    nowhere else.

    Every other field of the fragment goes through the ordinary merge and fills
    only what is empty. These were named by a check that measured the sheet, so
    a different answer at one of them is the correction that was asked for
    rather than a second opinion arriving late.

    The address a finding carries is not always the one the answer belongs at,
    which is `where_the_direction_is`. Written at the position `pointing`
    addresses, a direction lands on a field the prompt never asked about, the
    axis stands, the merge drops the reply for being filled already, and the
    re-read ends having changed nothing at all.

    What earns the overrule is the measurement, not the finding's account of
    the reading it stands against — the trace where there is one, and where
    there is none the finding is all the evidence there is.
    """
    for finding in found:
        address = where_the_direction_is(the_part, str(finding.get("about") or ""))
        offered = said_at(grew, address)
        if offered is None or offered == said_at(the_part, address):
            continue
        the_part = corrected(the_part, address, entry_at(grew, address),
                             what_the_trace_says(the_part, address, round_here)
                             or finding.get("stands"))
    return the_part


def _look_at_the_view_again(found):
    return (
        "These directions cannot be right as they are written:\n%s\nA feature is "
        "round in the view its axis points at and in no other, so where the trace "
        "names the view a circle of that width is actually in, that is the view — "
        "say which way the axis points given that view, and correct `round_in` to "
        "it. Where the finding is that two features disagree, one of the two is "
        "misread: say which, and what it should be. Reply with those fields only."
        % "\n".join("  - %s: %s" % (entry["about"], entry["stands"])
                     for entry in found))


def _every_direction_settled(the_part, round_here):
    """
    A direction read off the wrong view, settled off the view the circle is
    actually in.

    It was detected all along and acted on by nobody. In the recorded run
    (`logs/sessions/549e6232c9e04b659e5a2d536354bd7a.jsonl`, the 2023 pipe
    clamp) `pointing` fired eight times — both M6 holes, the bore and the ring
    split, every one of them "either the axis or this position was read off the
    wrong view" — the Gate was approved, and all of them were built along the
    ring axis. The screw that exists to pinch the split came out parallel to it.
    Then it became a stop, and a run that read the sheet correctly died for it,
    telling the user to edit `logs/parts/*.yaml` and start again (FCC-108,
    FCC-110).

    It is neither now. This was never a question for a person: the module
    holding the answer is the module asking it, which is FCC-111's telling
    rather than asking. Where the tracer found the circle — one width, one view,
    measured — that view is what the direction is written from, through
    `corrected`, with the direction it displaced kept beside it under `was`
    (ADR-0022). It is a measurement, so it is `derived`, and what fixes it is
    the `because` the correction carries — the same line `from` would hold on a
    value Design wrote itself. What no measurement reaches stays a conflict on
    the part and rides to the Gate in the words of both, settled by nobody.
    """
    for finding in read_off_the_wrong_view(the_part, round_here):
        view = finding.get("instead")
        if not view:
            continue
        address = str(finding["about"])
        the_part = corrected(the_part, address,
                             {"value": _pointing_at(address, view), "flag": "derived",
                              ROUND_IN: view},
                             finding["stands"])
    return the_part


def _the_tracing_governs(the_part):
    """
    A conflict one of whose readings was traced, written into the feature.

    `settled_by_the_tracing` says which conflicts those are and where they
    point; this is the write, through `corrected`, so the reading it displaces
    stays on the model under `was` and the Plan Gate shows both (ADR-0022). The
    value is `assumed`, because a tracing is what the model settled the
    disagreement on and not what the sheet dimensioned, and its `because` is
    the conflict — which stays where it is, in the words of both, for the user
    to overrule at the Gate.
    """
    for finding in settled_by_the_tracing(the_part):
        the_part = corrected(the_part, finding["about"],
                             {"value": finding["instead"], "flag": "assumed"},
                             finding["stands"])
    return the_part


def _pointing_at(address, view):
    """
    The direction a circle traced in *view* gives whatever is written at
    *address* — a profile lies in that view's plane, an axis stands normal to it.

    Which of the two it is, is the field the finding addressed: `axis` or
    `plane`, the last step of the address `_which_field_is_round` built.
    """
    field = address.rsplit(UNDER, 1)[-1]
    return ("the plane of %s" if field == "plane" else
            "perpendicular to the plane of %s") % view


def _the_sweep_is_the_sheets(the_part, attachments):
    """
    `printed_on_the_sheet` put back to what the sheet prints.

    The phase is shown the text layer and asked to account for it; what it
    writes under this key is its account, and an account is a paraphrase. The
    list itself is deterministic, exact, and carries what the sheet's own
    dimension lines measure at each number — so it is written here, over
    whatever came back, and the description keeps the arithmetic that catches a
    misread digit (FCC-112).

    After the re-read loop, never before: that check counts the part against
    the sheet, and it would find every callout accounted for by having been
    copied in.
    """
    swept = [entry for path in attachments
             for entry in sheet_reader.printed_on_the_sheet(sheet_reader.read(path))]
    if not the_part or not swept:
        return the_part
    return dict(the_part, printed_on_the_sheet=swept)


def _written_down(the_part, attachments, events):
    """
    The reading saved as a file, and read back out of it.

    Read back rather than passed on, because a file nothing downstream uses is
    a file nobody keeps true. From here on the run is planned, checked and
    built out of what is on disk, so a user who opens it and corrects an axis
    has corrected the run — and two readings of one sheet are two files to
    diff rather than two transcripts to compare (FCC-108).
    """
    source = next((path for path in attachments if sheet_reader.read(path)),
                  attachments[0] if attachments else "")
    path = part_file.saved(the_part, source,
                           read_from=_how_it_was_read(sheet_reader.read(source)))
    if not path:
        return the_part
    events.emit(PART, path=path)
    return part_file.read(path)


def _how_it_was_read(sheet):
    """What the sheet was measured with, so the reading carries its own terms."""
    if sheet is None:
        return None
    seen = []
    for crop in sheet.crops:
        name = crop.name.split(", part ")[0]
        if name not in seen:
            seen.append(name)
    return {"views": seen, "notes": list(sheet.notes)}


def _the_outside_is_carried(the_part, attachments, llm, events, budgets,
                            cancelled):
    """
    The one finding a run may not walk past: the shape of the part is nobody's.

    `silhouette` has raised it since it existed and nothing has ever acted on
    it. In the recorded run
    (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`, the 2023 pipe
    clamp) it stood in `conflicts` saying "no feature carries the outer contour,
    so the outside of the part is whatever profile the envelope's overall sizes
    are padded into" — the Gate showed eight Steps, the user approved them, and
    the Plan padded a profile it invented. A conflict settled by nobody is a
    conflict that decided for itself.

    So it is put to the drawing first, which is where the contour is, and where
    the sheet will not say, Design is told to settle it and to write down what
    it settled it off — an `assumed` profile with its `because`, which the Plan
    Gate shows above the Steps. Nobody is asked and the run does not stop: the
    defect was never that a profile was chosen, it was that the Plan chose one
    invisibly (ADR-0022). Every other conflict rides to the Gate as it always
    has: this is the only one that says the *outside* is missing, and it is the
    only one no later phase can notice.

    The re-reading stops where it stops finding fewer: a look that changed
    nothing will not change anything.
    """
    why = nobody_carries_the_outside(the_part)
    looked = 0
    while why and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"the_part_as_you_described_it": the_part,
                          "the_question": _the_outer_profile(why)},
                         llm, events, attachments)
        if not grew:
            break
        the_part = merge(the_part, grew)
        still = nobody_carries_the_outside(the_part)
        if still == why:
            break
        why = still

    why = nobody_carries_the_outside(the_part)
    if not why:
        return the_part

    grew, _ = design({"the_part_as_you_described_it": the_part,
                      "the_question": _settle_the_outer_profile(why)},
                     llm, events, attachments)
    return merge(the_part, grew) if grew else the_part


def _settle_the_outer_profile(why):
    return (_the_outer_profile(why) + " The sheet has been asked and did not settle it, "
            "so settle it yourself: give every value you choose the flag `assumed` and a "
            "`because` saying what you read it off — the silhouette, the overall sizes, "
            "the end radii. It is shown to the user above the Steps, and a choice they "
            "can see is a choice they can correct.")


def _the_outer_profile(why):
    return (
        "The description says the outside of this part is not square — %s — and no "
        "feature carries that contour, so what would be built is a prism of the overall "
        "sizes. The outer profile is a feature like any other: give it an `id`, a `kind` "
        "naming the shape in an engineer's words, the plane it lies in, a radius for "
        "every arc and a position for every one of those radius centres against a named "
        "datum. Read it off the view the silhouette is drawn in." % why)


def _every_callout_accounted_for(the_part, attachments, llm, events, budgets,
                                 cancelled):
    """
    The sweep of the sheet, checked against the sheet instead of against itself.

    The description is required to account for every callout printed on the
    drawing — each one becomes a value or is named in `not_defined` with a
    reason — and until now the list it was checked against was the model's own
    list of what it had noticed. That check cannot fail in the one way that
    matters: a callout nobody saw is missing from the sweep and from the part,
    and counting one against the other finds nothing wrong.

    Held against the text layer it does fail, and it fails naming the number.
    What comes back goes through the same merge as everything else, so a value
    already read stays read and this can only fill what was empty.

    Only where there is a text layer. A raster attachment supplies no list, and
    this then does nothing at all rather than inventing something to check.

    The re-reading stops where it stops finding fewer: a look that changed
    nothing will not change anything.
    """
    missed = _uncited(attachments, the_part)
    looked = 0
    while missed and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"the_part_as_you_described_it": the_part,
                          "the_question": _look_again_at(missed)},
                         llm, events, attachments)
        if not grew:
            break
        the_part = merge(the_part, grew)
        still = _uncited(attachments, the_part)
        if len(still) >= len(missed):
            break
        missed = still
    return the_part


def _uncited(attachments, the_part):
    missed = []
    for sheet in _sheets(attachments):
        missed.extend(sheet_reader.uncited(sheet, the_part))
    return missed


def _look_again_at(missed):
    return (
        "These callouts are printed on the sheet's own text layer and no value "
        "in your model uses them: %s. Each one is a dimension the draughtsman "
        "put there, so each one belongs somewhere — on a feature, on the "
        "envelope, or in `not_defined` with the views you looked in and the "
        "reason it could not be used. Look at the views again and follow each "
        "leader to the feature its arrowhead touches. Where a number is already "
        "on your model under a different reading, leave it: reply only with "
        "what is missing." % ", ".join(sorted(set(missed))))



_A_DIRECTION_FIELD = (UNDER + "plane", UNDER + "axis")


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
                               revising=()):
    """
    Plan, letting it ask for what the description did not carry (FCC-25).

    "модельку шестерні" names no module, no tooth count, no thickness and no
    bore. Planning anyway means inventing them, and an invented value is not
    visible anywhere the user could correct it — it turns up in the geometry.
    So Plan may answer with a question instead of Steps, and the run waits.

    A question Plan raises goes to the drawing, and never to a person
    (ADR-0022). Design decides whether the sheet answers it — a `read` value —
    and where it does not, Plan is told to choose for itself and to write every
    value it chose into the prose of the Step that uses it, which is where the
    Gate shows it. FCC-90 was an interception for the drawing's sake; it is now
    the whole route.

    A question the drawing was never going to answer does not go to it. A
    material card's uuid, what a type is called, which card carries a density
    are facts about this FreeCAD, and a Tool holds them: Plan is handed the
    read-only Tools and settles its own question with a look, bounded by
    `budgets.looks_per_turn` and charged to nothing else. A recorded run put
    "the UUID of an installed plain carbon steel material card" to the sheet
    three times, spent every re-read it had on it and stopped (FCC-149).

    Every question about the part reaches the sheet first, including one raised
    after Plan was told to choose for itself. That question is a *new* obstacle
    — the run that found the outer profile self-contradictory found it on the
    third try — and the sheet very often settles it. Asking the drawing used to
    be skipped there, because the run stopped one line above it (FCC-96).

    A question is not an approval: the Plan Gate is still the single point of
    approval, and it still sees every Step — and now the questions nobody
    answered, beside the Steps that chose without them. `budgets.rereads` bounds
    the drawing being asked and nothing else; how often Plan may be told to
    choose is not bounded at all, because a run that reaches the Gate with
    questions nobody answered is the case this route was written for.

    Every Plan comes through here, first or revised: a re-plan is a new Plan and
    may ask the same way, or it invents the value it was missing (FCC-67). What
    has been asked is `asking`, and it belongs to the run rather than to this
    call, so a re-plan does not start the questioning over.

    Hands back the description as it now stands, because it grew on the way.

    A Plan that disagrees with itself about which way a Step builds is neither
    a question nor a block: the description already says which way each entry
    goes, so what it needs is telling, not asking. Where the description grew
    before the Plan was settled it is emitted again, because the Gate stands
    over the description the Plan was made from.

    No question ends the run. A value nobody supplied is settled by the model,
    which says in `because` what it settled it off, and the Gate reads both; a
    stop here would end a run for want of an answer, which is the one thing no
    site may do (FCC-127, FCC-133). A question already outstanding is not
    listed twice, so the Gate shows what was asked and not how often. A sheet
    that answered nothing is said out loud, because the transcript could not
    otherwise tell a re-read that found nothing from a re-read that never
    happened, which is the first thing anybody reading a stalled run wants to
    know. A run that cannot move is a different matter and is stopped where
    progress is measured, not here (FCC-132).
    """
    grown, for_the_build = the_part, False
    while True:
        if cancelled():
            raise Stopped()
        planned = plan(intent, snapshot, llm, events, feedback=feedback,
                       the_part=grown, unanswered=asking.unanswered, revising=revising,
                       looking=_lookable(tools) if for_the_build else (),
                       looked=asking.looked_up)
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
            if asking.looks < budgets.looks_per_turn:
                asking.looks += 1
                for_the_build = True
                continue
            events.emit(ANSWER, text=_LOOKED_AT_NOTHING, by="tools")
        elif asking.read_again < budgets.rereads and _pictures(attachments):
            asking.read_again += 1
            from_the_drawing, _ = design({"the_part_as_you_described_it": grown,
                                          "the_question": planned.question},
                                         llm, events, attachments)
            if from_the_drawing:
                grown = merge(grown, from_the_drawing)
                events.emit(ANSWER, text=render(from_the_drawing), by="drawing")
                continue
            events.emit(ANSWER, text=_SAID_NOTHING, by="drawing")

        events.emit(ANSWER, text=_DECIDE, by="nobody")
        if planned.question not in asking.unanswered:
            asking.unanswered.append(planned.question)


def _run(intent, llm, tools, events, budgets, cancelled, gate, progress, attachments, ask,
         decide=revise_by_default, outstanding=()):
    """
    The run itself: describe the part, plan it, gate it, build it, judge it.

    What an earlier run left wrong is emitted before the drawing is even read.
    It is a fact about the Document this one starts from, and it belongs at the
    top of the transcript rather than nowhere. What the run was shown is
    emitted next, before anything is done with it: the name rides beside the
    path because a clipboard paste is a temp file the panel deletes when the
    run ends, and a Session opened tomorrow must still be able to say what was
    attached.

    Design runs once per run, ahead of every Plan: a revision plans from the
    description that already exists rather than paying a second call to look at
    the same picture (ADR-0018), and it only ever grows from there (ADR-0019).

    One loop, so every Plan reaches the Gate before any of its Steps runs — a
    revised Plan is a new Plan. Two checks come before the Gate. A Plan that
    leaves part of the description to nobody must not reach the user as a
    finished Plan, and it spends the same ceiling as every other cause, so a
    prompt that never learns to cite stops the run with a reason instead of
    looping. A number the Plan worked out wrongly is correctable while it is
    still a number: by the time Execute has multiplied it out the user has
    approved it and a revision is gone. Only the sums a Step states about
    itself — this decides nothing about geometry nobody wrote down.

    Both ends of the wait at the Gate go on the stream, because the wait is the
    longest silence in a run and a session read back has to tell a user
    thinking for half an hour from a provider taking half an hour. A standing
    approval stands over the path, never over a number nobody supplied: a Step
    stating an assumption the user has not read is asked about, standing
    approval or not, and a Gate that is skipped is on the stream as an approval
    saying which one it is standing on. AMEND, and anything that is not an
    approval, replans: no Step runs on a decision the Gate does not recognise.

    A Step that comes back a second time with nothing applied in between is not
    a Step Plan can write differently. What Execute refuses is the description
    behind it, and rewriting the words leaves the run where it is: a recorded
    run handed Step 1 of 8 back over an arm that could not both be 73 mm long
    and reach the tangent plane the sheet gives, replanned, handed the same
    Step back, and spent the budget with an empty Document. That stop sits
    behind the ceiling, which is the harder stop and already says what went on
    the budget. Otherwise both handbacks go back to Plan from a fresh Snapshot
    and spend the same budget: the Step no longer makes sense, or it cannot be
    built (ADR-0013).

    The acceptance checks run once the run is over and before Review, against
    the finished Document, and a failure there is a rejection. No acceptance
    list means no phase at all, not a phase that checks nothing. What they
    found is carried on `progress["unmet"]` whatever happens next, and a pass
    clears it, including the entries inherited from the run before. A user who
    takes a failed result goes straight out without Review: they have already
    judged it, and a verdict nobody will act on is a provider call spent on
    nothing. A rejection by Review is bounded by `review_cycles` and spends the
    shared revision budget as well.
    """
    if cancelled():
        raise Stopped()
    if outstanding:
        events.emit(OUTSTANDING, entries=list(outstanding))
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(path), "path": path}
                                     for path in attachments])

    part = {"the_part": _describe_the_part(intent, llm, events, budgets, ask, cancelled,
                                           attachments)}

    asking = _Asking()

    def replan(feedback=None, revising=()):
        planned, part["the_part"] = _plan_once_it_knows_enough(
            intent, tools.call("snapshot"), llm, events, budgets, part["the_part"],
            cancelled, asking, tools, feedback, attachments, revising)
        return planned

    def decided(why, applied):
        """
        What the user wants done about a result that did not pass.

        Three answers, for a moment where a revision may wipe what has already
        been built. `ACCEPT` stops here and what was built stays; `REVISE` is
        feedback, a new Plan and a new Plan Gate; `START_OVER` is a revision
        that may also delete what was built. The last is the one the Plan Gate
        has no word for, since REJECT stops a run but does not licence a
        restart. Accepting and stopping without accepting are one answer rather
        than two: both end the run and both keep what was built, and which of
        them happened is already on the stream — the `assertion` and
        `review_verdict` events say whether anything passed.

        Only where there is something to lose: the right to delete a model four
        minutes and seventeen Steps in is not the agent's, but with nothing
        applied there is nothing to decide about. Anything unrecognised is the
        default — the panel is what turns "nobody answered" into a stop.

        Asked and answered on the stream. It is the last thing that happens in
        a run that did not pass and it used to happen entirely inside the
        panel, so a Session read back showed a red `assertion` event and then
        nothing: no record that the user had been offered the choice, none of
        which one they took, and a Document carried into the next run with six
        entries still wrong and nothing anywhere saying so (FCC-98).
        """
        if not applied:
            return REVISE
        events.emit(QUESTION, text=_WHAT_NOW % why)
        answer = decide({"why": why, "applied": applied})
        answer = answer if answer in DECISIONS else REVISE
        events.emit(ANSWER, by="user", text=_DECIDED[answer])
        return answer

    planned = replan(_still_wrong(outstanding) if outstanding else None)
    _emit_plan(events, PLAN, planned, unanswered=asking.unanswered,
               decided=decided_for_you(part["the_part"]))

    rejections = 0
    revisions = _Revisions()
    stuck = {"applied": None}
    described = {}
    standing = {"approval": False, "read": set()}
    looks = _what_this_build_has(tools)

    def rewrite(cause, refused, feedback, tried, reason=None):
        """
        Rewrite the Plan for *cause*, or stop because the budget is spent.

        Seven unrelated things replan and every one of them does it the same
        way: spend one unit of the shared ceiling, plan again from a fresh
        Snapshot, and say on the stream why. Only the cause, the sentence and
        what had been tried differ, so only those are arguments.

        The Plan being rewritten goes with the complaint. A revision is an
        amendment of a Plan that exists, and Plan was being asked for one
        without being shown it — so it wrote from the complaint alone, which
        names only what was wrong.
        """
        if revisions.exhausted(budgets, steps):
            raise _OutOfRevisions(
                revisions.block_reason(budgets, steps, cause, refused), tried)
        revisions.spend(cause)
        revised = replan(feedback, revising=list(steps))
        _emit_plan(events, PLAN_REVISED, revised, reason=reason or feedback,
                   unanswered=asking.unanswered,
                   decided=decided_for_you(part["the_part"]),
                   revision=revisions.spent_total(),
                   of=revisions_ceiling(budgets, steps), because=revisions.named(cause))
        return revised

    while True:
        steps = planned.steps

        unconsumed = _unconsumed(part["the_part"], planned.satisfies,
                                 progress["satisfied"])
        if unconsumed:
            planned = rewrite(_Revisions.UNCOVERED,
                              "The Plan still leaves part of the description unplanned.",
                              _plan_the_rest(unconsumed), progress["tried"],
                              reason="no Step accounted for " + ", ".join(unconsumed))
            continue

        miscounted = [sum_ for step in steps for sum_ in wrong_sums(step)]
        if miscounted:
            planned = rewrite(_Revisions.MISCOUNTED,
                              "The Plan's arithmetic still does not come out.",
                              _do_the_sums(miscounted), progress["tried"],
                              reason="; ".join(miscounted))
            continue

        unread = _assumed(steps) - standing["read"]
        overruled = {"assumed": len(unread)} if standing["approval"] and unread else {}
        events.emit(GATE, steps=len(steps),
                    asked=bool(unread) or not standing["approval"], **overruled)
        if standing["approval"] and not unread:
            approval = Approval(APPROVE, standing=True)
            events.emit(GATE, decision=APPROVE, standing=True)
        else:
            approval = gate(list(steps)) or Approval(APPROVE)
            standing["approval"] = bool(getattr(approval, "standing", False)
                                        and approval.decision == APPROVE)
            if approval.decision == APPROVE:
                standing["read"] |= _assumed(steps)
            events.emit(GATE, decision=approval.decision,
                        standing=standing["approval"])

        if approval.decision == REJECT:
            applied = progress["applied"]
            said = "You rejected the Plan, so nothing was applied."
            if applied:
                said = ("You rejected the Plan, so nothing more was applied. The Document keeps "
                        "the %d Step%s an earlier Plan already applied."
                        % (applied, "" if applied == 1 else "s"))
            return _block(events, said, progress["tried"], applied)

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
                                progress, render(part["the_part"]), looks,
                                planned.acceptance, planned.cites)
        applied = progress["applied"]

        if outcome["handback"] and stuck["applied"] == applied \
                and not revisions.exhausted(budgets, steps):
            return _block(events, _GOING_NOWHERE % outcome["handback"],
                          outcome["tried"], applied)
        if outcome["handback"]:
            stuck["applied"] = applied

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

        checking = _also_check(planned.acceptance, outstanding)
        not_reached = _not_reached(outcome.get("unbuilt") or [], checking)
        acceptance = [entry for entry in checking if entry not in not_reached]
        checked = (assertion(acceptance, described, tools, llm, events, budgets,
                             cancelled, after_a_retry=outcome.get("retried"))
                   if acceptance else None)
        progress["unmet"] = (list(checked.unmet) if checked is not None else []) \
            + list(not_reached)
        if checked is not None and checked.failed:
            feedback = _why_the_checks_failed(checked.traceback)
            answer = decided(feedback, applied)
            if answer == ACCEPT:
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

        planned = rewrite(_Revisions.REJECTED, "Review has rejected the result again.",
                          feedback, outcome["tried"] + [{"review": reason}])


_GOING_NOWHERE = (
    "Execute handed a Step back for the second time and nothing has been applied in "
    "between, so rewriting it again would leave the run where it is. What it says "
    "cannot be built is the description it was planned from, not the words the Step "
    "was written in:\n%s\nCorrect the description — the values it holds in conflict, "
    "and the datums and overalls a superseded feature left behind — and run it again."
)


_INVENTED = "assum"


def _assumed(steps):
    """
    The prose of every Step that states a value nobody supplied.

    Compared as text and whole, rather than by the number inside it: what the
    Gate shows the user is the sentence, and a sentence they have not read is
    one they cannot correct — whether it changed the radius, the shape it is a
    radius of, or the datum it is measured from.

    Found by the word `_INVENTED` names, because Plan is told to write every
    value it chose into the prose that uses it — "assumed module 2" — so the
    word is where an invented number is visible, and it is the only place it is
    visible before the geometry.
    """
    return {" ".join(str(step).split()) for step in steps
            if _INVENTED in str(step).lower()}


def _still_wrong(outstanding):
    """What the first Plan of an inheriting run is told, before it plans anything."""
    return (
        "This Document was left by an earlier run whose acceptance checks did not pass, "
        "and nobody fixed what they found. These entries were wrong then and are wrong "
        "now:\n"
        + "\n".join("  - " + entry for entry in outstanding)
        + "\nPlan the part the drawing asks for, and put right what is listed above as "
          "part of it. Do not plan around it and do not assume the existing features are "
          "correct where this list says they are not."
    )


def _also_check(acceptance, outstanding):
    """
    The acceptance list, with anything an earlier run left wrong added to it.

    A second drawing describes the part it wants and says nothing about what
    the run before it got wrong, so its acceptance list cannot see the six
    entries that were already failing. Adding them is what makes the checks at
    the end of this run an answer about the whole Document rather than about
    the changes it happened to make (FCC-98).
    """
    listed = list(acceptance)
    known = set(listed)
    return listed + [entry for entry in outstanding if entry not in known]


def _work_through(steps, intent, tools, llm, events, budgets, cancelled, progress,
                  as_described="", looks=None, entries=(), cites=()):
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

    What the Steps that landed cite goes onto `progress["satisfied"]` as they
    land, because that is what the coverage check subtracts from the description
    when a handback sends the rest of the work back to Plan.

    Which Steps only landed on a second or third try is carried back out. A
    retry is where a feature gets talked into passing: the ring split of a
    recorded run was shortened until the Body stayed in one piece, and the
    shortened one is what the Document kept.

    A Step Execute was told to leave out goes back the moment its reason
    arrives rather than after the same reply three times over: asking for it
    again is asking for the same omission, and the reason is what Plan has to
    act on (ADR-0013). A Step that spends its attempts goes back the same way —
    the Plan is a precise instruction while it is buildable and advisory the
    moment it is not. Once a Step lands, anything read off the Document was
    read off it as it was before that Step, so from there it is history rather
    than knowledge.

    A Step that changes no geometry is the exception: it is skipped rather than
    handed back, and what it was to satisfy comes out on `unbuilt`. Nothing in
    the part is waiting on it, and the one road out of a handback is Plan,
    which has no Tools and so cannot write the Step Execute could not — a
    recorded run spent three provider calls and 55 seconds arriving at a stop
    that was decided the moment the last of eight Steps was the one that could
    not be built.
    """
    if cancelled():
        raise Stopped()
    described = {}
    attempts = []
    index = 0
    retried = set()
    unbuilt = []
    looks = looks if looks is not None else Looks()
    written = execute(steps, tools, llm, events, budgets=budgets, as_described=as_described,
                      looks=looks, entries=entries)

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
        if not outcome.code and written.unbuildable and not attempts:
            if not _changes_the_shape(step):
                cited = list(cites[index]) if index < len(cites) else []
                unbuilt.append({"step": step, "why": written.unbuildable,
                                "entries": cited})
                events.emit(UNBUILT, step=index + 1, total=len(steps), description=step,
                            why=written.unbuildable, entries=cited)
                index += 1
                attempts = []
                continue
            return {
                "handback": "Step %d of %d could not be built as written: %s\n%s"
                            % (index + 1, len(steps), step, written.unbuildable),
                "tried": [], "obsolete": None, "description": described,
            }

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
            progress["satisfied"].extend(cites[index] if index < len(cites) else ())
            if attempts:
                retried.add(index)
            described = result.get("description") or described
            events.emit(APPLIED, step=index + 1, label=outcome.label,
                        created=result.get("created", []))
            looks.a_step_landed()
            index += 1
            attempts = []
            continue

        traceback = result.get("traceback", "The Step failed without saying why.")
        attempts.append(Attempt(code=outcome.code or "", traceback=traceback))
        progress["tried"] = [_tried(step, a) for a in attempts]
        events.emit(PREFLIGHT_FAILED, step=index + 1, attempt=len(attempts),
                    traceback=traceback)

        if len(attempts) >= budgets.attempts_per_step:
            return {
                "handback": "Step %d of %d could not be built after %d attempts: %s\n"
                            "The last traceback was:\n%s%s"
                            % (index + 1, len(steps), len(attempts), step,
                               attempts[-1].traceback, _always_the_same_way(attempts)),
                "tried": [_tried(step, a) for a in attempts],
                "obsolete": None, "description": described,
            }

        if cancelled():
            raise Stopped()
        written = execute(steps, tools, llm, events, index, attempts, budgets, as_described,
                          looks, entries)

    return {"obsolete": None, "handback": None, "description": described,
            "tried": [], "unbuilt": unbuilt, "retried": sorted(
                {cited for at in retried for cited in (cites[at] if at < len(cites)
                                                       else ())})}


_A_PROPERTY_OF_THE_DOCUMENT = ("material", "appearance", "colour", "color",
                               "density", "transparen", "visibility", "label")

_SHAPES_THE_PART = ("pad", "pocket", "hole", "bore", "fillet", "chamfer", "sketch",
                    "extrud", "revolv", "loft", "sweep", "mirror", "groove",
                    "thicken", "draft", "boolean", "array", "pattern", "shell",
                    "slot", "recess", "taper", "thread", "rib", "fuse", "subtract",
                    "cut", "trim", "split")


def _changes_the_shape(step):
    """
    Whether the part depends on *step* — read off the words the Step is in.

    Conservative in one direction on purpose. A Step is geometry unless it
    plainly names a property of the Document that no geometry depends on and
    names no shape operation at all, so the words nothing here recognises leave
    the run exactly where it was: handed back to Plan. Reading it the other way
    round would let an unrecognised operation be skipped, and a Step of the part
    skipped silently is the failure this must not add.

    The prose is what there is. Execute leaves a Step out before any code for it
    exists, so nothing has been written, nothing has run, and the Document
    cannot be asked — a Step that assigns a material and a Step that pads a
    profile differ, at that moment, only in what they say.
    """
    said = str(step).lower()
    return not (any(word in said for word in _A_PROPERTY_OF_THE_DOCUMENT)
                and not any(word in said for word in _SHAPES_THE_PART))


def _not_reached(unbuilt, acceptance):
    """
    The acceptance entries the Steps that were never built were to satisfy.

    Matched by address rather than whole, because a Step cites the address and
    the acceptance carries the address with the value on the same line — the
    same allowance `_unconsumed` makes for a Plan that copies the line it was
    handed.

    They are taken off what the checks are asked to measure and reported unmet
    instead: a check written for a value nobody built fails, and a failed check
    is a rejection that would send the run back round for a Step it already
    knows cannot be written.
    """
    cited = {_as_written(entry).partition(": ")[0]
             for step in unbuilt for entry in step["entries"]}
    return [entry for entry in acceptance
            if str(entry).partition(": ")[0] in cited]


def _always_the_same_way(attempts):
    """
    What Plan is told when every attempt at a Step failed for the same reason.

    Rewriting the same Step in other words sends Execute back at the same wall,
    which is what a recorded run did: three identical failures, a replan, and
    then the same three again (FCC-100). Where the failure never varied, the
    Step itself is what has to change — a different operation, a different
    order, or the Step split so the part that cannot be built is on its own.
    """
    if len(attempts) < 2:
        return ""
    for phrase, _ in _FAILED_THIS_WAY:
        if all(phrase in (attempt.traceback or "") for attempt in attempts):
            return (
                "\n\nEvery one of those %d attempts failed the same way, so this is not a "
                "Step that was written down badly and coded worse — as written, it asks for "
                "something this approach cannot do. Rewording it will send Execute back at "
                "the same wall. Change what the Step asks for: a different operation, a "
                "different order, or the Step split so that the part of it that cannot be "
                "built stands alone." % len(attempts)
            )
    return ""


def _tried(step, attempt):
    return {"step": step, "code": attempt.code, "traceback": attempt.traceback}


def _block(events, reason, tried, applied, said=""):
    events.emit(BLOCKED, reason=reason, tried=list(tried), provider_said=said)
    return LoopResult(ok=False, blocked=True, reason=reason, tried=list(tried),
                      steps_applied=applied)
