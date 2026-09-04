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
import copy
import json
import mimetypes
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List

from core.arithmetic import wrong_sums
from core.ledger import Ledger
from core.design import (
    A_CHECK_HAZARD, A_CONFLICT, ROUND_IN, UNDER, corrected, decided_for_you, distilled,
    each_disagreement_once, entries, entry_at, merge, nobody_carries_the_outside,
    read_off_the_wrong_view, render, said_at, settled_by_the_tracing,
    what_the_trace_says, where_the_direction_is,
)
from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, CALL_STARTED, COST, DESIGN,
    ERROR, GATE, INTENT, OUTSTANDING, PART, PLAN, PLAN_REVISED,
    PREFLIGHT_FAILED, QUESTION, REVIEW_SUSPECTED, REVIEW_VERDICT, STEP_STARTED,
    THOUGHT, TOOL_CALL, TOOL_RESULT, UNBUILT, WRITING, EventStream,
)
from core import part_file, preflight, sheet_reader, sketch_check
from core.freecad_tools import TRAPS
from core.llm import REASONING, ProviderRefused, StreamCutOff, cache_control_supported
from core.tools import ToolError

DESIGN_PHASE = "design"
PLAN_PHASE = "plan"
EXECUTE_PHASE = "execute"
ASSERTION_PHASE = "assertion"
REVIEW_SUSPECT_PHASE = "review_suspect"
REVIEW_PHASE = "review"

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

NOT_A_LOOK = ("apply_step", "review_views", "check_assertions")


TYPE_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "logs", "type_cache.json")


def _freecad_version():
    """
    FreeCAD version as a string, or "unknown" if FreeCAD is not available.

    The cache key includes the version because a different build may have
    different properties on the same type.
    """
    try:
        import FreeCAD
        version = FreeCAD.Version()
        return "%s.%s.%s" % (version[0], version[1], version[2])
    except ImportError:
        return "unknown"


_VIEW_NAMES = ("isometric", "the opposite isometric", "above", "the front")

OP_VERBS = (
    "pad", "revolve", "additive-loft", "additive-sweep", "additive-helix",
    "additive-primitive",
    "pocket", "hole", "groove", "subtractive-loft", "subtractive-sweep",
    "subtractive-helix", "subtractive-primitive",
    "fillet", "chamfer", "draft", "thickness",
    "mirror", "linear-pattern", "polar-pattern", "multi-transform", "scale",
    "sketch", "datum-plane", "datum-line", "datum-point",
    "local-coordinate-system", "sub-shape-binder", "body",
    "fuse", "cut", "common",
    "thread", "extend", "trim", "offset",
)

OP_VOCABULARY = "closed"

_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _not_english(prose):
    """
    True where Cyrillic outnumbers Latin in a Step's prose — a whole Step
    written in Ukrainian, not a Latin sentence carrying one Cyrillic proper
    noun a user typed into the Intent (a label, a part name). Counting
    rather than merely detecting one Cyrillic character is what tells
    `\"Pad the profile 6.5 mm, label it \u041f\u0440\u043e\u0444\u0456\u043b\u044c\"`
    apart from a Step actually written in Ukrainian: refusing the first
    would cost a Plan that was fine over a name it did not invent.
    """
    cyrillic = len(_CYRILLIC.findall(prose))
    return cyrillic > 0 and cyrillic > len(_LATIN_LETTER.findall(prose))


class PhaseError(Exception):
    """A phase gave back something the loop cannot act on."""


@dataclass
class Budgets:
    """
    What a run may spend, and the ceilings that make it terminate.

    `revisions` is the floor under `revisions_ceiling`, which scales the
    allowance with the Plan's length. Without it, Execute declaring Step after
    Step obsolete never terminates.

    There are two separate ceilings over the seven causes of a replan. Mechanical
    causes (UNCOVERED, MISCOUNTED) are visible without running Execute and spend
    their own budget; execution causes (OBSOLETE, UNBUILDABLE, ASSERTED, REJECTED,
    AMENDED) spend `revisions_ceiling`. This keeps mechanical failures from consuming
    the budget for failures only a real attempt can surface. The worst-case number
    of Plan calls is additive — mechanical_revisions + revisions_ceiling — because
    the mechanical ceiling never resets: a Plan that fails coverage twice has spent
    those attempts for the whole run, and a third UNCOVERED blocks immediately even
    if UNBUILDABLE triggered a replan between them (logs/sessions/247c2aaed0734866a8aa9d169f7303bd.jsonl).

    `mechanical_revisions` is that ceiling, and it is a constant rather than scaling
    with Plan length: a coverage failure is a failure of the whole Plan at once, not
    of one Step among many. It is two because that is where the corpus sits. Of 456 sessions in `logs/sessions/`,
    43 blocked on revisions, and UNCOVERED appeared in the breakdown 1 time in 2 runs,
    2 times in 1 run, and 3 times in 1 run — typical is one, observed maximum is three.
    A ceiling of two cuts that one tail run on the mechanical budget instead of letting
    it consume execution revisions, which is the whole point of the split, and it does
    not touch the runs that spent one (logs/sessions/247c2aaed0734866a8aa9d169f7303bd.jsonl
    is the 2-time case that named the defect).

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

    `trailing_gap_retries` is the bounded, free continuation a Step gets when
    Execute's reply simply stops — code for every Step up to some point, then
    nothing, with nothing coded after it either — rather than being told
    anything is wrong with the code it did write (`logs/analysis-preflight-rakes.md`
    section 5): 23 such replies across 13 sessions, 57% within the last two
    Steps of the Plan, call durations up to 266 seconds, none of them a model
    choosing to skip a Step it could write. It is one, not `attempts_per_step`'s
    three: 12 of those 23 recurred once after a plain re-ask under the old
    accounting, so a single free continuation already catches about half the
    corpus without granting a model that runs dry on every call of a Step the
    same headroom a real Preflight failure gets. Once it is spent, the next
    trailing gap at that Step counts against `attempts_per_step` exactly as
    before, so a Step that never writes its tail still terminates.
    """

    attempts_per_step: int = 3
    review_cycles: int = 2
    revisions: int = 3
    mechanical_revisions: int = 2
    looks_per_turn: int = 6
    rereads: int = 3
    trailing_gap_retries: int = 1


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

    Eight unrelated things replan: the Plan leaves part of the description
    unplanned, a sum it states does not come out, a Step names an operation
    off the closed list, the user amends at the Plan Gate, Execute finds a
    Step out of date, Execute cannot build one, the acceptance checks fail,
    and Review rejects the result. Mechanical causes (UNCOVERED, MISCOUNTED,
    OP_REFUSED) spend their own ceiling; execution causes (OBSOLETE,
    UNBUILDABLE, ASSERTED, REJECTED, AMENDED) spend revisions_ceiling. They used
    to share one *counter* too, so whichever branch spent the last unit was
    reported as the cause of all of them, and a run stopped by two amendments
    told the user Execute kept finding the Plan out of date.

    OP_REFUSED sits with UNCOVERED and MISCOUNTED rather than with
    UNBUILDABLE: the closed-vocabulary check that raises it runs over the
    Plan reply itself, before Execute is ever asked for code, the same way
    UNCOVERED and MISCOUNTED are read straight off the Plan and never touch a
    Document (ADR-0026, FCC-233).
    """

    UNCOVERED = "uncovered"
    MISCOUNTED = "miscounted"
    OP_REFUSED = "op_refused"
    AMENDED = "amended"
    OBSOLETE = "obsolete"
    UNBUILDABLE = "unbuildable"
    ASSERTED = "asserted"
    REJECTED = "rejected"

    MECHANICAL_CAUSES = {UNCOVERED, MISCOUNTED, OP_REFUSED}

    _SAID = (
        (UNCOVERED, "the Plan left part of the description unplanned %s"),
        (MISCOUNTED, "the Plan's own arithmetic did not come out %s"),
        (OP_REFUSED, "a Step named an operation off the closed list %s"),
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

    def mechanical_spent(self) -> int:
        return sum(self._spent[cause] for cause in self.MECHANICAL_CAUSES)

    def execution_spent(self) -> int:
        return sum(self._spent[cause] for cause in self._spent if cause not in self.MECHANICAL_CAUSES)

    def named(self, cause) -> str:
        """The cause in words, without the count `breakdown` puts on it."""
        return (dict(self._SAID)[cause] % "").strip()

    def exhausted(self, budgets, steps, cause=None) -> bool:
        if cause in self.MECHANICAL_CAUSES:
            return self.mechanical_spent() >= budgets.mechanical_revisions
        if cause is not None and cause not in self.MECHANICAL_CAUSES:
            return self.execution_spent() >= revisions_ceiling(budgets, steps)
        return (self.mechanical_spent() >= budgets.mechanical_revisions or
                self.execution_spent() >= revisions_ceiling(budgets, steps))

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

        Names the ceiling that was hit: mechanical_revisions when a mechanical
        cause blocked, revisions_ceiling when an execution cause blocked.
        """
        if not self._causes_spent():
            return "The Plan may not be rewritten in this run. " + refused

        if cause in self.MECHANICAL_CAUSES:
            ceiling_hit = budgets.mechanical_revisions
            ceiling_name = "the mechanical ceiling"
        else:
            ceiling_hit = revisions_ceiling(budgets, steps)
            ceiling_name = "the whole budget"

        reason = ("The Plan has been rewritten %d times, which is %s — %s."
                  % (ceiling_hit, ceiling_name, self.breakdown()))
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

    `steps` is the Plan the Gate is actually approving, if it differs from
    the one it was shown: the panel's Plan Gate is edited directly now, not
    amended through the model (FCC-177), and Build hands back whatever it
    holds at the moment it is pressed. Empty means unedited -- run continues
    with the Steps `plan()` produced.
    """

    decision: str = APPROVE
    feedback: str = ""
    standing: bool = False
    steps: tuple = ()


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
    wrong in the Document, and the next run is entitled to know -- `ok=True`
    beside a non-empty `unmet` is exactly that: taken as it is, not passed. A
    saved Part carries nothing else saying so (logs/analysis-mm2023-defects.md:
    a failing pocket and lug, unrecorded anywhere), which is why both of
    `_run`'s ACCEPT returns set `unmet` from the same acceptance list every
    other ending already reads.

    `the_part_path` is the Part reading this run planned from: the one it
    wrote, or the one it was handed and skipped Design to read instead
    (FCC-210). Empty where neither happened. A run that inherits it and gets
    no new attachment reads the same Part rather than describing it again.
    """

    ok: bool = False
    blocked: bool = False
    verdict: str = None
    reason: str = ""
    steps_applied: int = 0
    tried: List[dict] = field(default_factory=list)
    unmet: List[str] = field(default_factory=list)
    the_part_path: str = ""

    @property
    def awaiting_user(self) -> bool:
        """Blocked means stopped and waiting, not failed and finished."""
        return self.blocked


PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "prompts")

_LANGUAGE_PATH = os.path.join(PROMPTS, "language.md")

_LANGUAGE_HEADINGS = (
    "Naming from a closed vocabulary",
    "The operation is one of 36",
    "A Step is one sentence",
    "The artefacts are English",
)

_PHASE_VOCABULARY = {
    DESIGN_PHASE: ("Naming from a closed vocabulary", "The artefacts are English"),
    PLAN_PHASE: ("The operation is one of 36", "A Step is one sentence",
                 "The artefacts are English"),
    EXECUTE_PHASE: ("The operation is one of 36", "The artefacts are English"),
    ASSERTION_PHASE: ("The artefacts are English",),
    REVIEW_SUSPECT_PHASE: ("The artefacts are English",),
    REVIEW_PHASE: ("The artefacts are English",),
}

_TRAPS_PLACEHOLDER = "{{traps}}"

_CUT_OFF_ATTEMPTS = 3


_LEDGER_HEADING = "This run has already failed these ways:"


def _language_sections():
    """
    `config/prompts/language.md` split at its own `## ` headings, each
    section keyed by heading text — the unit `_PHASE_VOCABULARY` composes
    from, so a phase gets the sections it uses and nothing it does not.
    """
    with open(_LANGUAGE_PATH, encoding="utf-8") as handle:
        text = handle.read()
    marks = sorted((text.index("## " + heading), heading)
                   for heading in _LANGUAGE_HEADINGS)
    sections = {}
    for i, (start, heading) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        sections[heading] = text[start:end].strip()
    return sections


def prompt(phase, ledger=""):
    """
    A phase's system prompt, read from `config/prompts/<phase>.md` and
    composed with the sections of the shared vocabulary, `config/prompts/
    language.md` (ADR-0026, FCC-234), that phase actually uses —
    `_PHASE_VOCABULARY` names which: Design invents `kind`/datum/envelope
    names and writes English prose, so it gets naming and the English rule;
    Plan and Execute write and check an `op`, so they get the 36-verb
    section, and Plan also gets the sentence rules since it is the phase
    that writes the Steps those rules and the English-only check bind;
    Assertion and both Review phases write English and nothing else language.md
    defines, so English is all any of the three needs. The vocabulary lives once, in
    one file, one heading per closed list — a phase composing only some of
    those headings is still reading its one source of truth, not a copy of
    it. Appended after the phase's own body rather than before it, so a
    phrase such as design.md's "the closed vocabulary below" still points at
    something below it in the composed prompt.

    Read per turn rather than at import: the addon installs as a plain source
    tree, so editing a prompt file takes effect on the next run. A missing file
    raises — a silently empty system prompt is the worse failure.

    The traps are the one thing not in either file. They are knowledge about a
    specific FreeCAD build, not copy to tune, so they stay in code and land at
    the placeholder the phase file chooses.

    `ledger` is this *run's* traps rather than this build's: `Ledger.render()`
    (`core/ledger.py`), appended after the file rather than placed at a
    placeholder inside it, under its own heading — a reader has to be able to
    tell "known about this FreeCAD build, always here" from "happened in this
    run, gone the moment the next one starts" without reading either list
    first. Empty for every phase but Execute today, and for Execute itself
    until this run has a failure to carry.
    """
    with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
        body = handle.read().strip()
    sections = _language_sections()
    vocabulary = "\n\n".join(sections[heading] for heading in _PHASE_VOCABULARY[phase])
    body = "%s\n\n%s" % (body, vocabulary)
    body = body.replace(_TRAPS_PLACEHOLDER, "\n".join("- " + trap for trap in TRAPS))
    if ledger:
        body = "%s\n\n%s\n%s" % (body, _LEDGER_HEADING, ledger)
    return body


def _ask(llm, events, phase, system, user):
    """
    One phase's turn: stream it, surface the reasoning as it forms, parse the reply.

    Reasoning is emitted chunk by chunk rather than aggregated at the end, so the
    user can tell early whether the assistant has understood them.

    A reply carrying no object is asked once more, as a second turn of the same
    conversation rather than a second copy of the first (`_asked_again`): the
    original `user` — Design's evidence among it, tens of thousands of tokens —
    goes out once, whatever the phase answers on. Every phase here answers in
    JSON and some models will not, or will not consistently: measured on the
    gateway, kimi-k3 replied to Execute with "I need to check the Hole
    feature's properties before writing the counterbore steps" — which is a
    *look*, and a look is a key of the object Execute replies with, so the
    model had the answer and wrote it as a sentence
    (`logs/sessions/ea390959745a453488138fddcc879635.jsonl`). One re-ask costs a
    turn; the alternative cost the run, ten approved Steps into it.

    Exactly one, and the second refusal is the phase failing as it did before.
    A model that will not answer in the shape twice is not going to on the
    third, and a loop of re-asks would spend the run proving it.

    Reasoning and then no answer is a third case, and it is asked again the
    same way rather than left to fail outright: there is no reply text to hand
    back as the assistant's turn, so the reasoning stream stands in for it
    (`_asked_again`). Of the last 20 sessions, 11 touched Design and 8 of those
    died exactly here, 70-640 s in — one more turn, built from thought rather
    than an answer, is worth trying before the phase is told it did not
    answer.

    On an OpenAI-compatible route reasoning with no terminal chunk at all is
    the provider closing the connection and is named as that instead
    (`core/llm.py :: StreamCutOff`), measured with raw HTTP and no client in
    the call at all. Running out of room is the other cause of an empty reply
    and arrives as a refusal, not as this.
    """
    body, thought, reasoning = _streamed(llm, events, phase, system, user)
    if not isinstance(_object_of(body), dict):
        body, thought, reasoning = _streamed(llm, events, phase, system, user,
                                             prior=_asked_again(body, reasoning))

    if thought and not body.strip():
        raise PhaseError(
            "%s streamed its reasoning and then stopped without answering. Send the "
            "Intent again, or pick a model that thinks less before it commits." % phase
        )
    return _as_json(body, phase)


def _streamed(llm, events, phase, system, user, prior=()):
    """
    One turn, as the text it wrote, whether it reasoned before writing it, and
    the reasoning itself.

    Two facts about a call are not a `Chunk` and cross the seam here, which is
    the one place every phase's stream is consumed and the one that already
    holds `events`: what the call spent, and how long the user waited for it.
    Both are recorded for every call, whatever the client is — a client that
    reports no price still costs the wait, and the wait is the number the
    panel has to show while nothing else is happening.

    The pair is what makes a silent call legible: `call_started` with no `cost`
    after it, in a session log, is a call that never came back. That is why
    the start is on the wire at all rather than inferred from the event before
    it, which would count the loop's own work between two calls as part of the
    second one.

    `ceiling` is the wall-clock the call is killed at, and only the
    subscription route has one (`core/llm.py :: ClaudeCodeClient`); a keyed
    call runs to whatever the provider does, so the field is absent rather
    than invented.

    A stream the provider closed mid-reply is sent again rather than carried
    on with (`core/llm.py :: StreamCutOff`). It is a dropped connection, not
    an answer, and it is random: of seven long streams measured against the
    opencode gateway four were cut and three finished, so the second attempt
    usually lands. Three attempts, because a gateway that cuts all three is
    down rather than unlucky, and each one costs the whole phase again.

    Every attempt is a `call_started` and a `cost` of its own, the abandoned
    ones included: a phase that took four minutes over three connections is
    four minutes the panel has to account for.

    `prior` is `_asked_again`'s two messages — the model's own last reply as
    an assistant turn, then the nudge — appended after the first user turn
    rather than sent as a fresh call, so a retry pays for `system` and `user`
    once, on the wire, whatever the client does with its own context.
    """
    for attempt in range(_CUT_OFF_ATTEMPTS):
        try:
            return _one_call(llm, events, phase, system, user, prior)
        except StreamCutOff:
            if attempt == _CUT_OFF_ATTEMPTS - 1:
                raise


def _one_call(llm, events, phase, system, user, prior=()):
    """
    The turn itself, priced whether it answered or the connection went.

    Every phase's call passes through here, which is what makes this the seam
    a per-phase client is chosen at: `for_phase` is asked for the client this
    phase is answered by, and a client that has none answers all five itself.
    Design reading a drawing and Execute writing the code for a Step it has
    already been given are not the same demand, and one dial over both is
    paid at the rate of the more expensive on every call of the cheaper.

    The reply reaches the stream as it forms, the way the reasoning before it
    does. Until it did, a phase went silent from its last `thought` to its
    `cost` — 22.4 minutes of a recorded run's 23.6 minutes of provider time,
    and 25.6 of 53.7 in another — and a panel with nothing to show reads as a
    run that has hung.
    """
    llm = llm.for_phase(phase) if hasattr(llm, "for_phase") else llm
    ceiling = getattr(llm, "timeout", None)
    model = getattr(llm, "model", None)
    started = {"phase": phase, "model": model}
    if ceiling is not None:
        started["ceiling"] = ceiling
    events.emit(CALL_STARTED, **started)

    start = time.time()
    text, reasoning, thought = [], [], False
    messages = [{"role": "system", "content": system},
               {"role": "user", "content": user}] + list(prior)
    try:
        for chunk in llm.stream(messages):
            if chunk.kind == REASONING:
                thought = True
                reasoning.append(chunk.text)
                events.emit(THOUGHT, phase=phase, text=chunk.text)
            else:
                text.append(chunk.text)
                events.emit(WRITING, phase=phase, text=chunk.text)
    finally:
        spent = getattr(llm, "last_cost", None)
        paid = {"phase": phase, "seconds": time.time() - start,
                "model": spent["model"] if spent else model}
        if spent:
            paid["usd"] = spent["usd"]
        events.emit(COST, **paid)
    return "".join(text), thought, "".join(reasoning)


_ASKED_AGAIN = (
    "Answer again, and put the JSON object this phase takes in the reply. Whatever you "
    "said above is either one of that object's own keys or it is not the answer: a phase "
    "that needs to look something up first asks for the look *in* the JSON, and a phase "
    "that wants to say why does it inside the object, not instead of it. Prose beside the "
    "object is read; prose without one is not."
)


def _asked_again(body, reasoning):
    """
    The retry as a second turn of the same conversation: the model's own last
    reply, handed back as its own assistant message, then a short nudge asking
    for the object — not the original `user` sent again with a directive
    stapled on. The evidence a phase like Design carries is in that original
    `user`, once, and a retry that resent it was a second prefill of the same
    tens of thousands of tokens for the sake of one sentence at the end.

    `reasoning` stands in for `body` when there is no reply to hand back — a
    turn spent entirely on thought and nothing after it. There is no promise
    the tail of a chain of thought reads as an answer; there is a promise it
    reads as *something*, which a blank assistant turn does not, and which is
    what a retry needs to be a turn of this conversation rather than a demand
    into empty air.
    """
    said = (body or reasoning or "").strip() or "(nothing was said)"
    return [{"role": "assistant", "content": said},
            {"role": "user", "content": _ASKED_AGAIN}]


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


def _the_evidence(attachments, views=None, keep_printed=False, pictures=True):
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

    `views` narrows a turn that is not the first pass to the overview plus the
    named views, rather than the whole sheet: a re-read Design is sent back to
    the sheet for one thing already knows which view that thing is printed in
    (`_every_callout_accounted_for`), and resending every other view's crops
    with it pays for pictures the question never mentions. `None`, the first
    pass's default, keeps everything — a run that has not looked at the sheet
    yet has nothing to narrow to.

    `keep_printed` puts the sheet's own printed-callout list back on a narrowed
    turn even though `views` dropped it: a page-sized turn in `_first_pass`
    still has to see a callout printed in one view that explains a feature
    drawn in another, and that list is a few thousand characters against the
    tens of thousands a whole extra view's crops would cost. A re-read
    (`_every_callout_accounted_for` and the like) leaves this False — the
    question it sends already names the callout it wants.

    `pictures=False` is `_reconciled`'s own turn: every named list `shown()`
    would otherwise draw pictures for, as text and nothing else, so the turn
    that settles what only the whole sheet can settle reads the same words a
    picture would have shown without paying for the picture again.
    """
    shown = []
    for path in attachments:
        sheet = sheet_reader.read(path)
        if sheet is None:
            if pictures:
                picture = _as_image(path)
                if picture:
                    shown.append(picture)
            continue
        if not pictures:
            shown.extend({"type": "text", "text": text} for text in _words_only(sheet))
            continue
        for piece in _pieces_of(sheet, views):
            if "say" in piece:
                shown.append({"type": "text", "text": piece["say"]})
            else:
                picture = _as_image(piece["show"])
                if picture:
                    shown.append(picture)
        if keep_printed and views is not None and sheet.printed:
            shown.append({"type": "text", "text": sheet_reader._the_printed_list(sheet)})
    return shown


def _pieces_of(sheet, views):
    """*sheet*'s own `shown()`, narrowed to `views` — the one place both
    `_the_evidence` and the page-budget estimate in `_group_tokens` read it,
    so the two never drift apart about what a turn over `views` actually
    sends."""
    return _narrowed(sheet_reader.shown(sheet), _wanted_paths(sheet, views))


def _words_only(sheet):
    """
    Every named list `shown()` draws a picture beside, as words alone.

    `_reconciled` is the turn that settles what only the whole sheet can
    settle, and it does that off what is printed, leadered, measured between
    two points and drawn — not off a picture, because the page-sized turns
    that came before it have already looked at every picture there is.
    """
    parts = []
    if sheet.printed:
        parts.append(sheet_reader._the_printed_list(sheet))
    if sheet.leaders:
        parts.append(sheet_reader._the_leader_list(sheet))
    if sheet.between:
        parts.append(sheet_reader._the_between_list(sheet))
    if sheet.drawn:
        parts.append(sheet_reader._the_drawn_list(sheet))
    return parts


def _wanted_paths(sheet, views):
    """
    The picture paths a narrowed turn keeps: the sheet overview, always, and
    the whole/crop pictures of the named `views` — or `None`, meaning every
    picture, when `views` itself is `None`.

    Read off `sheet`'s own fields rather than off `shown()`'s prose, so this
    does not depend on how a view is announced, only on which picture belongs
    to it — `sheet_reader.shown(sheet)`'s signature is the only promise this
    makes use of.
    """
    if views is None:
        return None
    wanted = set(views)
    paths = {sheet.overview}
    paths.update(sheet.overviews or ())
    paths.update(whole.path for whole in sheet.wholes if whole.name in wanted)
    paths.update(crop.path for crop in sheet.crops
                 if crop.name.split(", part ")[0] in wanted)
    return paths


def _narrowed(told, paths):
    """
    *told* kept down to `paths` — or all of it, when `paths` is `None`.

    A `say` that introduces a picture is kept exactly when that picture is; a
    `say` with no picture after it is one of the sheet-wide lists (the printed
    callouts, the leaders, the notes) and is dropped on a narrowed turn along
    with the pictures — what a narrowed turn asks about has already named the
    callout it wants, in the question, so the sheet's own account of every
    callout is not needed again to answer it.
    """
    if paths is None:
        return told
    kept = []
    for index, piece in enumerate(told):
        if "show" in piece:
            if piece["show"] in paths:
                kept.append(piece)
            continue
        following = told[index + 1] if index + 1 < len(told) else None
        if following is not None and "show" in following and following["show"] in paths:
            kept.append(piece)
    return kept


def design(body, llm, events, attachments=(), views=None, phase=DESIGN_PHASE,
          keep_printed=False, pictures=True):
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

    The Intent is in the body of every turn, beside whatever else that turn is
    asking about. It is the scope of the run rather than one source among them,
    so a turn without it reads the sheet for nothing in particular; it never
    narrows what is described, because what the drawing prints is on the part
    whether the run was asked about it or not.

    `views`, `keep_printed` and `pictures` are `_the_evidence`'s own, passed
    through rather than applied here: this is where the evidence and the body
    meet, not where either is decided.

    `phase` labels the call on the event stream and the session log — plain
    `"design"` for every turn except the page-sized ones `_first_pass` fans
    out, which are told apart as `"design: <label>"` so N calls in flight are
    N legible rows and not one stream with N reasonings braided into it.

    The evidence goes out first and the body — the one part of the request
    that changes turn to turn — last, so the evidence is a stable prefix
    rather than the tail of a message that starts differently every time. On
    the one route measured to honour it (`core/llm.py :: cache_control_supported`)
    its last block carries `cache_control`, so a run's second and later Design
    turns read the prefix from cache instead of paying to send it again; on
    every other route the block carries an extra key the provider was never
    asked about and nothing here relies on it doing anything with it.

    Hands back what to merge and what Design wants asked. A reply that is not a
    model grows the part by nothing, which is the same case as a sheet that does
    not answer: the run carries on with what it already had rather than losing
    it, the way an acceptance list nobody wrote is not fatal either.
    """
    evidence = _the_evidence(attachments, views, keep_printed, pictures)
    if evidence and cache_control_supported(getattr(llm, "model", None)):
        evidence = evidence[:-1] + [dict(evidence[-1], cache_control={"type": "ephemeral"})]
    reply = _ask(llm, events, phase, prompt(DESIGN_PHASE),
                 evidence + [{"type": "text", "text": json.dumps(body, indent=2, default=str)}])
    the_part = reply.get("design")
    return (the_part if isinstance(the_part, dict) else {},
            str(reply.get("question") or "").strip())


_CHARS_PER_TOKEN = 4

_PIXELS_PER_VISUAL_TOKEN = 750

_A_GROUP_BUDGET = 34000

_A_GROUP_CONCURRENCY = 4


def _group_tokens(sheet, views, prompt_tokens):
    """
    A page-sized turn's own input tokens, estimated the way
    `tests/test_design_payload.py` prices a whole one: text at four characters
    to the token, a picture at its pixel count over 750 — the same divisor a
    provider is measured charging one at (`tests/test_design_payload.py`).

    Read off `_pieces_of` rather than off `_the_evidence`'s finished blocks: a
    picture is still a path here, not a base64 string decoded a second time
    just to be measured.
    """
    chars = sum(len(piece["say"]) for piece in _pieces_of(sheet, views) if "say" in piece)
    if sheet.printed:
        chars += len(sheet_reader._the_printed_list(sheet))
    from PIL import Image
    visual = 0.0
    for piece in _pieces_of(sheet, views):
        if "show" in piece and os.path.isfile(piece["show"]):
            with Image.open(piece["show"]) as picture:
                visual += picture.width * picture.height / _PIXELS_PER_VISUAL_TOKEN
    return prompt_tokens + chars // _CHARS_PER_TOKEN + round(visual)


def _split_to_budget(sheet, views, prompt_tokens):
    """
    *views* as one group, or as two smaller ones split at the middle and
    asked the same question, recursively, until every group prices at or
    under `_A_GROUP_BUDGET`.

    `_A_GROUP_BUDGET` is 34,000: measured against the two Model Mania sheets a
    page prices at 23,829-28,352 tokens (`tests/test_design_payload.py`), so
    the budget clears both with room before a group would grow back toward
    what the whole sheet used to cost in one call.
    """
    if len(views) <= 1 or _group_tokens(sheet, views, prompt_tokens) <= _A_GROUP_BUDGET:
        return [views]
    middle = len(views) // 2
    return (_split_to_budget(sheet, views[:middle], prompt_tokens)
           + _split_to_budget(sheet, views[middle:], prompt_tokens))


def _the_groups(attachments):
    """
    Every attached, readable sheet as its own page-sized Design calls: one
    group of view names per page, split further only where `_split_to_budget`
    says a page outgrew the budget on its own.

    A page rather than an arbitrary N of views is the boundary, because a page
    is one a reader recognises and a run's log can point at ("design: page
    2") without translating a group id back into what it covers.

    Empty for a run with nothing `sheet_reader` can read — no attachments, a
    text-only Intent, a format neither PyMuPDF nor Pillow knows — which is
    `_first_pass`'s own sign to fall back to today's single whole-attachment
    call rather than page a sheet that was never cut into pages.
    """
    prompt_tokens = len(prompt(DESIGN_PHASE)) // _CHARS_PER_TOKEN
    groups = []
    for path in attachments:
        sheet = sheet_reader.read(path)
        if sheet is None:
            continue
        pages = sorted({crop.page for crop in sheet.crops} | {whole.page for whole in sheet.wholes})
        for page in pages:
            views = sorted({crop.name.split(", part ")[0] for crop in sheet.crops
                            if crop.page == page}
                           | {whole.name for whole in sheet.wholes if whole.page == page})
            if not views:
                continue
            parts = _split_to_budget(sheet, views, prompt_tokens)
            for index, part in enumerate(parts, start=1):
                label = ("page %d" % page if len(parts) == 1 else
                        "page %d, part %d of %d" % (page, index, len(parts)))
                groups.append((label, part))
    return groups


def _asked_about_a_group(label, views, intent, llm, events, attachments):
    """One page-sized group's Design turn — the printed-callout list rides
    along even though `views` narrows the pictures, because a callout printed
    once can explain a feature in a view this group never sees."""
    return design({"intent": intent}, llm, events, attachments, views=views,
                  phase="%s: %s" % (DESIGN_PHASE, label), keep_printed=True)


def _first_pass(intent, llm, events, attachments, cancelled):
    """
    The Design phase's opening move: one call per page-sized group of views,
    run in parallel, each merged into the same part as it lands
    (ADR-0019 — grow-only and order-independent is what makes concurrent
    merges legitimate rather than a race). Falls back to today's single
    whole-attachment call when `_the_groups` finds nothing to page.

    `copy.copy(llm)` gives every concurrent call its own `last_cost`: both
    `LiteLLMClient` and `ClaudeCodeClient` clear that attribute at the top of
    `stream()` and set it at the end, as instance state, and two calls
    sharing one `llm` object under real concurrency would clear and set each
    other's price mid-flight. The copy is shallow and cheap — model, key and
    the spawn/completion callable are read-only, so nothing about the
    provider route changes, only which object holds this one call's price.

    `cancelled()` is read once, before the batch is submitted, exactly where
    every other call site in this phase reads it: a provider call has no
    cancel hook to give a thread mid-flight, so a run stopped while the batch
    is in flight still lets every submitted call finish, the same as a run
    stopped mid-call does today.

    A group's own failure — no JSON, a refusal, a stream that would not stay
    up — costs that group and not the phase: it lands on the part as a
    `conflicts` entry naming the group, with `kind=A_CHECK_HAZARD`, the same
    shape `_the_geometry_disagrees` gives an outline the solver never reached,
    and it is said once more on the `ERROR` stream so a session log shows
    which page went unread and why.

    At most one question crosses the fan-out, the first a group asked in page
    order: the phase still asks once here (ADR-0022), and a page-sized call
    asking on its own for its own reasons is not license for the run to wait
    on several at once.
    """
    groups = _the_groups(attachments)
    if not groups:
        the_part, question = design({"intent": intent}, llm, events, attachments)
        return the_part, question, False

    if cancelled():
        raise Stopped()

    the_part = {}
    question = ""
    with ThreadPoolExecutor(max_workers=min(len(groups), _A_GROUP_CONCURRENCY)) as pool:
        futures = [(label, pool.submit(_asked_about_a_group, label, views, intent,
                                       copy.copy(llm), events, attachments))
                  for label, views in groups]
        for label, future in futures:
            try:
                grown, asked = future.result()
            except (PhaseError, ProviderRefused, StreamCutOff) as failed:
                the_part = merge(the_part, {"conflicts": [
                    {"about": "the %s of this drawing" % label,
                     "stands": "Design could not be asked about it: %s" % failed,
                     "kind": A_CHECK_HAZARD,
                     "machine_written": True}]})
                events.emit(ERROR, message="Design could not read the %s: %s"
                                          % (label, failed))
                continue
            the_part = merge(the_part, grown)
            if asked and not question:
                question = asked
    return the_part, question, True


def _reconciled(the_part, intent, llm, events, attachments):
    """
    One turn over what the page-sized calls produced, seeing no pictures —
    every named list `shown()` would draw one beside, as `_words_only` gives
    it, instead. This is the half of cross-view reading a page cannot do for
    itself: a page-sized call sees its own view's line work and the sheet's
    printed callouts, and this is the turn that sees every page's words at
    once and settles what only the whole sheet can.
    """
    return design({"intent": intent, "the_part_as_you_described_it": the_part},
                  llm, events, attachments, pictures=False)


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


def plan(intent, snapshot, llm, events, feedback=None, the_part=None, unanswered=(),
         revising=(), looking=(), looked=(), tried=""):
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
    """
    request = {"intent": intent, "document": snapshot}
    if revising:
        request["the_plan_you_wrote"] = list(revising)
    if the_part:
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

    `said` is what a reply that left a Step without code and without a reason
    carried instead, rendered by `_said`. It is set whenever a reply could
    leave a Step uncovered — a reply that numbered some Steps but not all, and
    the empty reply at the end of a turn that spent its looks — because from
    here that is the only account of what the model answered: a Step with no
    code and no `said` is a run that spent a provider call and kept nothing
    of it.
    """

    steps: dict = field(default_factory=dict)
    obsolete: str = None
    unbuildable: str = None
    said: str = None

    def for_step(self, number) -> ExecuteOutcome:
        return self.steps.get(number) or ExecuteOutcome()


def execute(steps, tools, llm, events, start=0, attempts=(), budgets=None, as_described="",
            looks=None, entries=(), ledger=None):
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
    A Step that landed carries none of its history forward. `ledger` is the
    opposite of that on purpose: it is this whole run's, survives every land
    and every replan (`core/ledger.py`), and lands in the system prompt rather
    than the request — the same placement `TRAPS` already has, under its own
    heading so the two stay distinguishable.

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
        reference = _repair_reference(attempts)
        if reference:
            request["repair_reference"] = reference

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

        reply = _ask(llm, events, EXECUTE_PHASE,
                     prompt(EXECUTE_PHASE, ledger.render() if ledger else ""),
                     json.dumps(request, indent=2, default=str))

        written = _written(reply.get("steps"), start)
        if written:
            return Written(steps=written,
                           unbuildable=str(reply.get("unbuildable") or reply.get("obsolete"))
                           if (reply.get("unbuildable") or reply.get("obsolete")) else None,
                           said=_said(reply, start))
        if reply.get("obsolete"):
            return Written(obsolete=str(reply["obsolete"]))
        if reply.get("unbuildable"):
            return Written(unbuildable=str(reply["unbuildable"]))
        if not reply.get("look") or not remaining:
            break
        looked.append(_look(tools, reply["look"], events, looks))

    return Written(said=_said(reply, start))


_RAN_OUT_OF_ROOM = "ran out of room before finishing this reply"
_SKIPPED_A_STEP = "skipped a Step it could have written"


def _classified_failures():
    """
    (phrase, directive, label) for every rejection a different attempt could
    actually fix, keyed on a substring Preflight, lint or apply itself writes
    — or, for `_RAN_OUT_OF_ROOM` and `_SKIPPED_A_STEP`, that `_work_through`'s
    own missing-code message writes, so a trailing gap and an interior gap
    stay two Ledger lines rather than deduping into the one this table had no
    entry for before them.

    `directive` is the paragraph `_the_same_way_twice` hands Execute the
    second time this exact phrase recurs; `label` is the one line `Ledger`
    (`core/ledger.py`) keeps of the same classification once the code and the
    traceback themselves are gone.

    Two rejections a real run can hit are deliberately absent. "There is no
    open Document to apply this Step to" is not a Step to write differently —
    there is no Document open, which no code change fixes. And the wrapper
    "The Step passed Preflight but failed against the real Document, so it
    was rolled back" carries whatever underlying exception the transaction
    raised appended after it — if that exception is one of the phrases below,
    it already matches on its own text; the wrapper's fixed words say nothing
    about *what* to change, so giving it its own entry would be advice with
    nothing behind it.
    """
    return (
        ("neither removed nor added any material",
         "the cut ran the wrong way or missed the solid. It is not the size of the cut "
         "and it is not how the profile is built: the same shape, extruded the other "
         "way, is the whole of the fix. Change the *direction* this time — set "
         "`Reversed`, set `Type='ThroughAll'` or `SideType='Symmetric'`, or put the "
         "sketch on the opposite face — and check the sign against the Body's "
         "BoundBox before you commit to it. Rewriting the same extrusion more "
         "legibly will fail "
         "identically.",
         "cut moved no material because the extrusion ran the wrong direction"),
        ("left a Body in more than one piece",
         "the profiles in one sketch do not meet, so they padded into separate lumps. "
         "Renaming the features does not join them. Either make the profiles share "
         "their endpoints so the outline closes as one loop, or build each piece as "
         "its own feature on the solid that is already there.",
         "sketch profiles left the Body split into separate solids"),
        ("recompute left these objects in error",
         "the Document does not rebuild. The object named in the traceback is the one "
         "to fix, and it is fixed by changing what it references or what it is given "
         "— not by building the same thing again under another name.",
         "the Document failed to recompute"),
        ("is not part of the enumeration",
         "the spelling is not one this build accepts. Ask `describe_type` for the "
         "property's enumeration and use a value from it verbatim; guessing a second "
         "spelling is the same move that failed.",
         "used a property value spelling this build does not accept"),
        ("end face of tapered extrusion is empty",
         "the draft angle closes the profile before the extrusion gets to the end of "
         "it. A smaller angle, a shorter cut, or a larger profile is the change; the "
         "same three numbers arranged differently is not.",
         "the draft angle closed the profile before the extrusion's end"),
        ("The code does not parse:",
         "the code is not valid Python. Fix the syntax the traceback names before "
         "changing anything else — rewriting the logic around a syntax error the "
         "parser never reached is not a different attempt.",
         "the code did not parse as Python"),
        ("is not a Sketcher constraint type",
         "the constraint name is not one Sketcher has. Ask `describe_type` for the "
         "exact spellings this build accepts rather than guessing another one; "
         "concentricity in particular is `Constraint('Coincident', a, 3, b, 3)` "
         "between the two centres, not a `Concentric` constraint, because there is "
         "no such constraint.",
         "named a Sketcher constraint type that does not exist"),
        ("which is not a signature this constructor has",
         "the constraint was given the wrong number of arguments for its name. Ask "
         "`describe_type` for how many arguments it actually takes rather than "
         "guessing another count.",
         "gave a Sketcher constraint the wrong number of arguments"),
        ("is a literal element name",
         "an edge or face was named directly, and it points somewhere else the "
         "moment a parameter upstream in the history changes. Compute the reference "
         "at execution time off the geometry instead of writing the element name "
         "again, or pick an operation that needs no element name at all.",
         "referenced a geometry element by a literal name"),
        ("produces a feature tree that is fiction",
         "a PartDesign feature had `.Shape` assigned, or a property bolted on with "
         "`addProperty`, instead of computing its Shape from its Profile. Use the "
         "feature that performs the operation — Pad, Pocket, Revolution, Groove, "
         "Hole, Fillet, Chamfer — on a real sketch; the same assignment under "
         "another name fails identically.",
         "assigned Shape directly on a PartDesign feature instead of building it"),
        ("is deprecated and FreeCAD prints two lines of warning",
         "the property named is deprecated. Write the replacement property the "
         "message names instead of writing the same deprecated one again.",
         "wrote a deprecated property instead of its replacement"),
        ("breaks the correction path",
         "the Step's own error handling caught and hid the exception that should "
         "have failed it. Let it raise instead of catching it, so the traceback "
         "that says what to fix actually reaches you.",
         "swallowed its own exception instead of letting the Step fail"),
        ("nothing says how many elements it expected to find",
         "a filtered selection was used without asserting its count against the "
         "number the Step's prose gives. State that count and let the Step fail "
         "where the selection does not match it — `if len(edges) != 12: raise "
         "RuntimeError(...)` — before the reference is used.",
         "used a filtered selection without asserting its expected count"),
        ("is not available during Preflight",
         "the code read the GUI or the live selection, and the Preflight worker has "
         "neither. Make the Step a pure function of the Document instead.",
         "read the GUI or the live selection instead of the Document"),
        ("makes an object active, which only the GUI can do",
         "the code tried to make an object active, which only the GUI can do and "
         "Preflight has no GUI for. Use `Body.addObject(feature)` instead of "
         "activating anything.",
         "tried to activate an object instead of adding it to the Body"),
        ("changed nothing: no object was created, deleted or modified",
         "the code ran without error but the Document is unchanged. Check that it "
         "actually calls what it defines and assigns the result onto the Document's "
         "objects — or, if this Step genuinely has nothing left to do, say so with "
         "`obsolete` instead of writing the same inert code again.",
         "ran without error but changed nothing in the Document"),
        ("which is a rebuild of a model that is already there rather than a change "
         "to it, and that was not approved",
         "the Step deleted several of the model's own features instead of editing "
         "them. Edit what exists — a sketch's geometry, a feature's Length, Type or "
         "Profile — and add a feature to the end of the tree only where the "
         "existing one genuinely cannot express what is needed.",
         "deleted several existing features instead of editing them"),
        ("which you created rather than I did, and that was not approved",
         "the Step deleted objects the user built, not this run. Achieve it without "
         "deleting them, or leave the Step out and say why it cannot be done — "
         "deleting the same objects again asks the same refused question again.",
         "deleted objects the user created instead of the run's own"),
        (_RAN_OUT_OF_ROOM,
         "the same reply is running out of room at roughly the same point twice. Write "
         "shorter code per Step this time — no restated Step prose, no comments, "
         "straight to the calls — rather than the same length worded more tersely.",
         "ran out of room and left the Plan's tail with no code"),
        (_SKIPPED_A_STEP,
         "a Step was skipped even though a Step after it got code in the same reply, "
         "twice over. Write every Step you are asked about, in the order given, rather "
         "than leaving one for a regeneration to fill in.",
         "skipped a Step it could have written while writing later ones"),
    )


_FAILED_THIS_WAY = _classified_failures()


_REPAIR_REFERENCE = (
    ("has no such property",
     "The Step set a property the object type doesn't carry — a guessed name, not a "
     "real one. Ask `describe_type` for what the object actually has and set one of "
     "those; a second guessed spelling fails the identical way.",
     "any check on this Step's own promised properties: the value never landed, so a "
     "check that already passed against the object as it stood before this Step ran "
     "has not yet measured what this Step was meant to change."),
    ("BRep_API: command not done",
     "PartDesign::Fillet fails this way on a radius the material can't carry — check "
     "the radius against the Body's wall thickness at the edge being rounded (a full "
     "round needs less than half of it) and set one the wall can carry, rather than "
     "rewriting the same call.",
     "any check that measured a face count or a bounding dimension off the "
     "un-filleted Body: a radius that now fits changes both."),
)


def _repair_reference(attempts):
    """
    The repair layer's own addition to a retry request (FCC-203): a
    reference entry for a *known* symptom, keyed on a phrase read off the
    latest attempt's own traceback the way `_FAILED_THIS_WAY` and
    `_the_same_way_twice` already are — but consulted on the *first*
    failure rather than the second, since a repair record names a fix for
    the symptom just seen, not a warning that a Step keeps failing the same
    way twice. Where the traceback names no symptom this table recognises,
    nothing is added and the request is exactly what it is without this
    function: the traceback, and nothing else.

    Two layers, on purpose, not one: `core/freecad_tools.py :: TRAPS` is
    prevention, read into every prompt before a Step is written, full and
    unconditional; this is repair, read only after a Step has already
    failed, and stays narrow — one entry for the symptom in hand, not the
    whole table. Merging them would force a choice between the two, since
    prevention has to stay complete and repair has to stay narrow.

    Riding beside `your_failed_attempts_at_this_step` rather than replacing
    it, so a retry always still sees the traceback it names. Nothing here spends a `look` or asks for anything:
    the table is read before the request is built, not through a Tool call
    — a repair layer that cost Execute one of its own bounded turns would
    spend the very budget it exists to protect (ADR-0009).
    """
    if not attempts:
        return None
    traceback = attempts[-1].traceback or ""
    for phrase, fix, recheck in _REPAIR_REFERENCE:
        if phrase in traceback:
            return {"the_smallest_responsible_fix": fix, "recount_after_it": recheck}
    return None


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
    for phrase, directive, _label in _FAILED_THIS_WAY:
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


def _said(reply, start):
    """
    A reply that could leave a Step without code, rendered short enough to
    travel: what it was, not what it wrote.

    Capped at 300 characters. This same string reaches `PREFLIGHT_FAILED`, then
    `progress["tried"]`, then the handback to Plan, and an Execute reply
    carries the code for a whole Plan — recording it whole would put that code
    back into the next request and into the transcript, which is the cost this
    exists to avoid, not to move.

    Three things, in order: the reply's top-level keys, so a reply that had
    nothing usable says so rather than rendering empty; for `steps`, which
    Step numbers it carried and which of those had code, since a Step missing
    from that second list and not the first is the whole of what a reply that
    numbered other Steps but not this one did; and, for every other key, its
    value where that value is a short string — `unbuildable`, `obsolete`, or
    whatever else a model answered with instead of code — truncated with it.
    """
    keys = sorted(reply)
    parts = ["keys: %s" % ", ".join(keys) if keys else "keys: none"]
    steps = reply.get("steps")
    if isinstance(steps, list):
        carried, coded = [], []
        for position, entry in enumerate(steps, start=start + 1):
            if not isinstance(entry, dict):
                continue
            number = entry.get("step")
            number = number if isinstance(number, int) else position
            carried.append(number)
            if entry.get("code"):
                coded.append(number)
        parts.append("steps carried: %s, with code: %s" % (carried, coded))
    for key, value in reply.items():
        if key == "steps" or not isinstance(value, str):
            continue
        parts.append("%s: %s" % (key, value))
    return "; ".join(parts)[:300]


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

    `describe_type` is cached to disk between runs, keyed by FreeCAD version.
    A recorded run spent 26 minutes and $4.24 of $19.46 on six describe_type
    calls — facts about the build that never change
    (logs/sessions/247c2aae...).
    """

    ABOUT_THE_BUILD = ("describe_type", "supported_types", "materials")

    CARRIED = 8

    def __init__(self, cache_path=None, freecad_version=None):
        self._taken = []
        self._step = 0
        self._cache_path = cache_path
        self._freecad_version = freecad_version
        self._disk_cache = self._load_disk_cache() if cache_path else {}

    def _load_disk_cache(self):
        """
        Read describe_type answers from disk, or {} if absent or unreadable.

        A corrupted file is treated as absent: the run continues and the Tool
        is called.
        """
        if not self._cache_path or not os.path.isfile(self._cache_path):
            return {}
        try:
            with open(self._cache_path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_disk_cache(self, name, arguments, result):
        """
        Write one describe_type answer to disk, atomically.

        Concurrent runs both write; a unique temporary name plus os.replace
        makes last-write-wins safe.
        """
        if not self._cache_path or name != "describe_type":
            return

        key = self._disk_cache_key(arguments)
        self._disk_cache[key] = result

        try:
            directory = os.path.dirname(self._cache_path)
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._disk_cache, handle, indent=2, sort_keys=True)
                os.replace(tmp_path, self._cache_path)
            except OSError:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
        except OSError:
            pass

    def _disk_cache_key(self, arguments):
        """Cache key for describe_type: (version, type_id)."""
        args_str = json.dumps(arguments, sort_keys=True, default=str)
        return "%s:%s" % (self._freecad_version, args_str)

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
        self._save_disk_cache(name, arguments, result)

    def answer(self, name, arguments):
        """
        What this run already knows about this exact look, or None.

        Only where it is still true: an answer read off a Document two Steps
        ago is not an answer about this one.

        describe_type reads from disk first, then from this run's memory.
        """
        if name == "describe_type" and self._cache_path:
            disk_key = self._disk_cache_key(arguments)
            if disk_key in self._disk_cache:
                return self._disk_cache[disk_key]

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
    looks = Looks(cache_path=TYPE_CACHE_PATH, freecad_version=_freecad_version())
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

    `confirmed` is what the check script itself measured and found true, read
    back out of its own `held` list rather than inferred as everything the
    model did not name `uncovered` — so it is not only the success branch's:
    a run whose checks failed on some entries and held on others carries both
    a non-empty `confirmed` and a non-empty `unmet`.

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


SELF_INTERSECTION_CHECK = """
import os
import tempfile
from OCC.Core.BOPAlgo import BOPAlgo_ArgumentAnalyzer
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape

held = []
failed = []
for obj in doc.Objects:
    shape = getattr(obj, 'Shape', None)
    if shape is None or shape.isNull() or not shape.Solids:
        continue
    handle, path = tempfile.mkstemp(suffix='.brep')
    os.close(handle)
    shape.exportBrep(path)
    occ_shape = TopoDS_Shape()
    breptools.Read(occ_shape, path, BRep_Builder())
    os.remove(path)
    analyzer = BOPAlgo_ArgumentAnalyzer()
    analyzer.SetShape1(occ_shape)
    analyzer.SetSelfInterMode(True)
    analyzer.Perform()
    if analyzer.HasFaulty():
        failed.append(obj.Name + " self-intersects (BOPAlgo_SelfIntersect)")
    else:
        held.append(obj.Name)
assert not failed, "these did not hold: " + "; ".join(failed)
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

    `confirmed` is the check script's own `held` list where the worker
    measured one, on the failing branch as much as the passing one — not
    everything the model did not call `uncovered`, which is a claim about
    what the model looked at rather than a measurement of what it found true.
    An envelope failure and a check the phase never wrote both leave
    `confirmed` empty by construction, having measured nothing to confirm.

    The envelope is measured first and without asking anybody: three sizes
    against three sizes is arithmetic, and a run that built the wrong part
    should not have to wait for a provider to say so. Self-intersection is
    measured next, also without asking anybody: `BOPAlgo_SelfIntersect` is too
    expensive to run after every Step (FCC-253 Q4), so it runs once here,
    against every solid the finished Document holds, the same way the
    envelope does. The entries a retry touched go with the request, because a
    Step that failed a check and passed on the retry is a Step whose feature
    was changed to get past it — shortened, moved, turned around — and
    passing the second time says the workaround was legal, never that it is
    still the feature the drawing asked for; those are the entries to measure
    rather than to take on trust.

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

    for label, measured in (("the envelope check", the_envelope_measured(acceptance)),
                            ("the self-intersection check", SELF_INTERSECTION_CHECK)):
        if not measured:
            continue
        events.emit(TOOL_CALL, name="check_assertions", arguments={"code": measured})
        result = tools.call("check_assertions", {"code": measured})
        events.emit(TOOL_RESULT, name="check_assertions", result=result)
        if not result.get("ok"):
            traceback = result.get("traceback") or (label + " failed.")
            unmet = _unmet_in(traceback) or [label]
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
            held = result.get("held")
            confirmed = list(held) if held is not None else \
                [entry for entry in acceptance if entry not in uncovered]
            checked = Checked(confirmed=confirmed, uncovered=uncovered)
            events.emit(ASSERTION, ok=True, code=code, confirmed=confirmed,
                        uncovered=uncovered, traceback="")
            return checked

        traceback = result.get("traceback") or "The checks failed without saying why."
        if _the_model_failed(traceback):
            held = result.get("held")
            confirmed = list(held) if held is not None else []
            worker_failed = result.get("failed")
            unmet = list(worker_failed) if worker_failed is not None else \
                (_unmet_in(traceback) or list(acceptance))
            events.emit(ASSERTION, ok=False, code=code, confirmed=confirmed,
                        uncovered=uncovered, traceback=traceback, unmet=unmet)
            return Checked(failed=True, confirmed=confirmed, traceback=traceback,
                           uncovered=uncovered, unmet=unmet)

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

    The fallback for a failure the worker's own `failed` list did not cover —
    the envelope check, which never builds one, and a check script that
    raised without following the assertion.md shape at all. Where a script
    did build `failed`, the caller reads that directly, off the same
    namespace `held` comes off; this is what is left for the traceback string
    that stands in for it, parsed rather than returned as data because that
    string is what a broken or older script leaves behind.

    The checks end in a single assert whose message lists every entry that did
    not hold, separated by semicolons — the Assertion prompt specifies that
    shape, so this reads it rather than guessing at one. A Document that does
    not recompute has no entry list at all: nothing was measured, so nothing
    can be named, and the caller falls back to the whole list.
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


def _review_content(request, views, attachments):
    """
    A Review request's content blocks — the views, named for the prose, then
    whatever the user attached to the Intent.

    Both Review calls send this shape; only what they put in `request` before
    calling this differs, and neither is told how the result was produced
    (ADR-0003).
    """
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
    return content


def review_suspect(intent, views, described, llm, events, attachments=()):
    """
    Look at the finished Document before Assertion measures it, and name what
    a render alone can only suspect.

    Runs first, on the same views and the same Description Review #2 judges
    later, so what it notices still has Assertion ahead of it to confirm or
    refute — never a verdict of its own (FCC-204: a suspicion that could pass
    or fail the run on its own is the flaw ADR-0015 already ruled out).

    What it returns is zero or more Acceptance entries, each written the way
    every other entry already is — `address: value` — and flagged `suspected`
    here rather than trusted from the reply, the way a view-read entry is
    flagged `derived` by the loop and not by the phase that read it. Assertion
    reads them exactly as it reads the rest of the list (FCC-258).
    """
    request = {"intent": intent, "the_document_as_it_now_stands": described}
    content = _review_content(request, views, attachments)

    reply = _ask(llm, events, REVIEW_SUSPECT_PHASE, prompt(REVIEW_SUSPECT_PHASE), content)

    suspected = [str(entry).strip() for entry in (reply.get("suspected") or ())
                if str(entry).strip()]
    return [entry + " (suspected)" for entry in suspected]


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
    what `_VIEW_NAMES` names them by for Review's own prose — the same views
    `review_suspect` was shown, captured once for both (a render does not
    change between them; Assertion does not draw). What the checks confirmed
    and what they could not cover go with them, so the verdict is about what
    the checks did not cover rather than a second opinion on what they did
    (ADR-0015). This is the run's one gating verdict: `review_suspect` never
    supplies one, so `measure.py`'s review_fail_rate stays a measure of this
    call alone.
    """
    request = {"intent": intent, "the_document_as_it_now_stands": described}
    if checked is not None:
        request["facts_the_checks_confirmed"] = list(checked.confirmed)
        request["what_the_checks_could_not_cover"] = list(checked.uncovered)
    content = _review_content(request, views, attachments)

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
        outstanding=(), the_part_path=None) -> LoopResult:
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

    `the_part_path` is a prior run's Part reading, written by `part_file.saved`.
    Given one and no `attachments`, `_run` reads it back instead of asking
    Design to describe the part again — the second half of one conversation
    plans from what the first half already read off the sheet, rather than
    asking for the sheet a second time. An attachment arriving alongside it
    means a new drawing, which replaces rather than resumes: Design runs on
    it as it would on a first prompt. The path a run reads or writes comes
    back out on the result the same way `unmet` does, for whoever drives the
    next run.
    """
    events = events if events is not None else EventStream()
    events.emit(INTENT, text=intent)
    budgets = budgets or Budgets()
    progress = {"applied": 0, "tried": [], "unmet": list(outstanding), "satisfied": [],
               "the_part_path": the_part_path or ""}

    def outcome(result):
        """
        Stamp what is still wrong with the Document, and which Part it was
        built from, onto whatever the run returned.

        Every exit carries both, including the ones that raised: a run stopped
        halfway leaves the Document exactly as wrong as the last check found
        it, and reading from the same Part it started from, and that is the
        state the next run inherits. Done in one place because there are nine
        ways out of `_run` and the eight that are not the happy one are the
        ones that matter here.
        """
        result.unmet = list(progress["unmet"])
        result.the_part_path = progress["the_part_path"]
        return result

    try:
        return outcome(_run(intent, llm, tools, events, budgets,
                            cancelled or (lambda: False), gate, progress, attachments,
                            ask, decide, list(outstanding), the_part_path))
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
    except part_file.MissingPyYAML as exc:
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


def _written_in(the_part, intent, question, answer, llm, events, attachments):
    """
    A person's answer, back through Design rather than on to Plan.

    The drawing answering writes `read` and the person answering writes
    `assumed` (FCC-87 preserved), and both land in the same model by the same
    merge — which is what makes a value already on it impossible to replace.
    An answer carried alongside the description instead would be a second source
    for Plan to prefer, and preferring the newer is exactly how the depth off
    SECTION B-B was lost.

    Nobody answering is a turn too. The question is Design's own, so what it
    asks about is a value the part cannot be described without, and left
    unsettled it is a gap where a criterion belongs — nothing plans it, nothing
    measures it, and the Gate has nothing to show the user. Design settles it
    instead, and the instruction rides in the question because that is where the
    phase reads what to do with one (ADR-0022).
    """
    body = {"intent": intent, "the_part_as_you_described_it": the_part,
            "the_question": question if answer else _settle_it(question)}
    if answer:
        body["the_answer_you_were_given"] = answer
    grew, asks = design(body, llm, events, attachments)
    return merge(the_part, grew), asks


def _settle_it(question):
    return (question + " Nobody answered it, so settle it yourself: give every value you "
            "choose the flag `assumed` and a `because` saying what you settled it off. It "
            "is shown to the user above the Steps, and a choice they can see is a choice "
            "they can correct.")


def _describe_the_part(intent, llm, events, budgets, ask, cancelled, attachments):
    """
    The part as Design has it, before anything is planned.

    Design owns the question (ADR-0019), and this is the one that survives
    (ADR-0022): where its own model says a value is not merely empty but
    unknowable from the sheet, it asks, once, before any Step has been drafted.
    The run waits, because it is the only question left and so it may afford
    to. Unanswered it is not fatal — the model settles the value like every
    other, and the choice rides to the Gate.

    Design runs whether or not there is a drawing. With nothing to look at it
    describes the part from the words of the Intent alone and asks for what
    those words do not carry, so a text-only run reaches the Gate with a
    description, an Acceptance list and a reading on disk rather than with
    nothing at all.

    It ends in two steps: gather every piece of evidence there is, then distil
    it (ADR-0024). What leaves the phase is the document — what the part is —
    and the evidence that settled it stays in the log beside the file, with
    what nobody agrees on standing there one entry to an address, so every
    phase after this one is handed the part rather than the reading of it.
    `tests/test_loop.py` measures both against the recorded run of the 2023
    pipe clamp.
    """
    the_part, question, grouped = _first_pass(intent, llm, events, attachments, cancelled)
    the_part = merge({}, the_part)

    if question:
        if cancelled():
            raise Stopped()
        events.emit(QUESTION, text=question)
        the_part, _ = _written_in(the_part, intent, question,
                                  _answered(question, ask, events), llm, events,
                                  attachments)

    if grouped:
        if cancelled():
            raise Stopped()
        grown, _ = _reconciled(the_part, intent, llm, events, attachments)
        the_part = merge(the_part, grown)

    the_part = _every_callout_accounted_for(the_part, intent, attachments, llm, events,
                                            budgets, cancelled)
    the_part = _the_outside_is_carried(the_part, intent, attachments, llm, events,
                                       budgets, cancelled)

    if the_part:
        events.emit(DESIGN, model=the_part)
    the_part = _the_sweep_is_the_sheets(the_part, attachments)
    the_part = _the_directions_are_the_drawings(the_part, intent, attachments, llm,
                                                events, budgets, cancelled)
    the_part = _every_number_is_the_sheets(the_part, intent, attachments, llm, events,
                                           budgets, cancelled)
    disagreements = _the_geometry_disagrees(attachments, events)
    the_part = merge(the_part, dict(the_part, conflicts=disagreements)
                     if disagreements else the_part)
    the_part = _every_direction_settled(the_part, _round_evidence(attachments))
    the_part = _the_tracing_governs(the_part)
    return _written_down(distilled(the_part), each_disagreement_once(the_part),
                         intent, attachments, events)


def _every_number_is_the_sheets(the_part, intent, attachments, llm, events, budgets,
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

    Signs `kind: A_CONFLICT` and `machine_written: True` on what it appends
    (FCC-249) — previously neither was set, so `_which_kind_it_is`'s default
    covered the kind and nothing at all covered authorship. FCC-248 found
    this and could not fix it, the file being another lane's.
    """
    found = _off_the_sheet(attachments, the_part)
    if not found:
        return the_part

    if budgets.rereads:
        if cancelled():
            raise Stopped()
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
                          "the_question": _where_is_this_number(found)},
                         llm, events, attachments)
        if grew:
            the_part = merge(the_part, grew)
        found = _off_the_sheet(attachments, the_part)

    return merge(the_part, {"conflicts": [
        {"about": "the value %g on this part" % number,
         "stands": "no callout prints it, no dimension line measures it and "
                   "nothing traced is that size",
         "was_also_said": "the drawing",
         "kind": A_CONFLICT,
         "machine_written": True}
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

    An outline the solver could not be asked about arrives on the part as a
    check hazard, for the same reason a re-read that found nothing says so: a
    sheet nothing was wrong with and a sheet nobody could check both arrive at
    the Gate with nothing to settle on them, and only one of the two is a
    verdict. It stays on the `ERROR` stream as well, which is where a run is
    read back from and not where a reading is settled. A child that never came
    up, a child that died and could not be replaced, and a budget that ran out
    are all that same sentence: those outlines are unchecked and the run goes
    on. Nothing here may end a run — that is what makes it a reading check and
    not a Step.

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
        found.extend({"about": "the line work traced in %s" % view,
                      "stands": "the solver could not be asked about it, so nothing "
                                "here says whether the view draws what the sheet "
                                "prints",
                      "kind": A_CHECK_HAZARD,
                      "machine_written": True}
                     for view in sorted(set(unchecked)))
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


def _the_directions_are_the_drawings(the_part, intent, attachments, llm, events,
                                     budgets, cancelled):
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

    What is left when it stops is not always what a look could still fix. A
    raster attachment has no line in it to trace — `round_here` is empty, a
    re-ask of the same picture answers the same way it did the first time, and
    no correction here ever measured anything. That is not the same as the
    finding going away, and carrying the reading on as `read` says it is:
    `_left_as_assumed` is the honest end of this phase, the one `_corrected_by`
    has no evidence to reach — and it runs only once a look was actually taken
    (`looked`), so a budget of zero re-reads leaves the model exactly as
    untouched as it has always left it, rather than distrusting a reading
    nobody has yet had the chance to put back to the drawing.
    """
    round_here = _round_evidence(attachments)
    found = read_off_the_wrong_view(the_part, round_here)
    looked = 0
    while found and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
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

    if not looked:
        return the_part
    still = read_off_the_wrong_view(the_part, round_here)
    return _left_as_assumed(the_part, still) if still else the_part


def _left_as_assumed(the_part, found):
    """
    A direction still contradicted after every re-read and every tracing, kept
    at the value it was read at with `flag: assumed`.

    Nothing here corrects a value — reaching this point means nobody had a
    measurement to correct it with, which is exactly what `_the_directions_are_
    the_drawings` could not settle. What changes is the confidence the part
    claims for it: a `read` a check has caught contradicting itself is not a
    reading any more, and carrying it as one hides a guess inside a fact the
    Plan and the Gate would otherwise trust without question. The `because` is
    the conflict itself — the words `stands` already holds — so what shows
    above the Steps is why it is a guess, not only that it is one.

    The whole entry travels, not just its `value`: `round_in` is what
    `grounded` and `crosswise` read a feature's direction by, and a write that
    dropped it would settle this finding by blinding the next check to it,
    which is not the same as settling it.
    """
    for finding in found:
        address = where_the_direction_is(the_part, str(finding.get("about") or ""))
        held = entry_at(the_part, address)
        if not isinstance(held, dict) or "value" not in held or held.get("flag") == "assumed":
            continue
        the_part = corrected(the_part, address, dict(held, flag="assumed"),
                             finding["stands"])
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


def _written_down(document, gathered, intent, attachments, events):
    """
    The document saved as a file, read back out of it, and the evidence beside it.

    Read back rather than passed on, because a file nothing downstream uses is
    a file nobody keeps true. From here on the run is planned, checked and
    built out of what is on disk, so a user who opens it and corrects an axis
    has corrected the run — and two readings of one sheet are two files to
    diff rather than two transcripts to compare (FCC-108).

    What is on disk is what came out of the distil step, and the whole of what
    Design gathered is the log beside it, each disagreement in it said once.
    The reading is the requirement, and a requirement nobody can hold in their
    head is one nobody checks; the proof of each of its numbers is what a
    person goes looking for afterwards and no phase needs, so it is a file with
    the same name and nothing reads it.

    The Intent is written beside the sheet it was read off, because a reading
    is the requirement one run is held to, and what was asked is what makes it
    a requirement rather than a description of a drawing.

    A part described from words alone leaves one too. There is no sheet to name
    it after and none to say it was read off, and both of those are things the
    file says rather than reasons not to write it: what the run is planned,
    checked and built from is the reading, drawing or no drawing.
    """
    source = next((path for path in attachments if sheet_reader.read(path)),
                  attachments[0] if attachments else "")
    path = part_file.saved(document, source, asked_for=intent,
                           read_from=_how_it_was_read(sheet_reader.read(source)))
    if not path:
        return document
    part_file.logged(gathered, path)
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


def _the_outside_is_carried(the_part, intent, attachments, llm, events, budgets,
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

    Not narrowed to a view the way `_every_callout_accounted_for`'s re-read is
    (`_the_evidence`'s `views`). That narrowing is sound because
    `sheet_reader.dimensions` ties a missed callout to the view it is printed
    in directly; `why` here is quoted out of the model's own prose
    (`nobody_carries_the_outside`), before `_the_sweep_is_the_sheets` has even
    put the sheet's own account of itself on the part, and nothing on the path
    to it names a view with the same certainty. Guessing a view and being wrong
    would hide the very picture the silhouette is drawn in, which is worse
    than the pictures this keeps sending — so this keeps sending all of them.
    """
    why = nobody_carries_the_outside(the_part)
    looked = 0
    while why and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
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

    grew, _ = design({"intent": intent,
                      "the_part_as_you_described_it": the_part,
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


def _every_callout_accounted_for(the_part, intent, attachments, llm, events, budgets,
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
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
                          "the_question": _look_again_at(missed)},
                         llm, events, attachments, views=_views_of(attachments, missed))
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


def _views_of(attachments, missed):
    """
    The views `missed`'s callouts are printed in, or `None` where that is not
    knowable — a callout `sheet_reader.dimensions` cannot place on a named
    view, or one this sweep does not find at all, most often because it is
    printed on a second attached sheet the loop over `_sheets` has not reached
    yet when it does.

    A miss here costs the part — the very failure `_every_callout_accounted_for`
    exists to catch — so `None` keeps the whole sheet in play rather than
    narrow to a guess: a false alarm on the narrowing costs one extra view's
    worth of pictures, and a wrong narrowing hides the one the model needs.
    """
    wanted = set(missed)
    views = set()
    for sheet in _sheets(attachments):
        for entry in sheet_reader.dimensions(sheet):
            if entry.text in wanted:
                wanted.discard(entry.text)
                if not entry.seen_in:
                    return None
                views.add(entry.seen_in)
    return None if wanted or not views else views


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
                               revising=(), ledger=None):
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
                       looked=asking.looked_up,
                       tried=ledger.render() if ledger else "")
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
            from_the_drawing, _ = design({"intent": intent,
                                          "the_part_as_you_described_it": grown,
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
         decide=revise_by_default, outstanding=(), the_part_path=None):
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
    `the_part_path` is the one exception: a prior run's own reading, handed
    back with no new attachment, is read off disk instead — Design is skipped
    entirely rather than asked to look at nothing (FCC-210).

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

    `settings_manager.get_auto_approve_plan` is the one exception to "asked
    about, standing approval or not": on, it stands over every Gate from the
    first Plan on, including a Step with an assumption nobody has read, so an
    unattended run never stalls waiting on a human who is not there. `gate`
    itself is never called while it is on. What it accepted without asking
    stays honest on the stream regardless: the `GATE` event carries
    `auto: true` and, when it skipped an unread assumption, the Step text
    that assumption was in, under `assumed` — the same evidence a human
    approval leaves, for whoever reads the Session back. Off by default, so a
    run only goes unattended once the user has turned it on.

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

    Review looks at the finished Document twice, with Assertion run between
    the two: once, `review_suspect`, to notice what a render alone cannot
    confirm and hand it to Assertion as more of the acceptance list to
    measure, and once more, gating, once Assertion has answered. Assertion's
    own failure is a rejection there, before the gating Review ever runs. No
    acceptance list means no phase at all, not a phase that checks nothing.
    `review_suspect` never rejects a run by itself — it is diagnostic, not
    authoritative (FCC-258) — so nothing it notices skips the acceptance
    checks or is judged before they run. What the checks found is carried on
    `progress["unmet"]` whatever happens next, and a pass clears it, including
    the entries inherited from the run before. A user who takes a failed
    result goes straight out: the gating Review has already passed the same
    Document, so nothing is left unrun to save by skipping it. A rejection by
    the gating Review is bounded by `review_cycles` and spends the shared
    revision budget as well.
    """
    if cancelled():
        raise Stopped()
    if outstanding:
        events.emit(OUTSTANDING, entries=list(outstanding))
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(path), "path": path}
                                     for path in attachments])

    if the_part_path and not attachments:
        part = {"the_part": part_file.read(the_part_path)}
    else:
        part = {"the_part": _describe_the_part(intent, llm, events, budgets, ask, cancelled,
                                               attachments)}
        written = events.last(PART)
        progress["the_part_path"] = written.data["path"] if written else ""

    asking = _Asking()
    ledger = Ledger(_FAILED_THIS_WAY)

    def replan(feedback=None, revising=()):
        planned, part["the_part"] = _plan_once_it_knows_enough(
            intent, tools.call("snapshot"), llm, events, budgets, part["the_part"],
            cancelled, asking, tools, feedback, attachments, revising, ledger)
        return planned

    def decided(why, applied, unmet=()):
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

        `auto_approve` reaches this question the same way it already reaches
        the Plan Gate, past the exact point the Plan Gate stops waiting: an
        unattended run does not block on a bare `Event().wait()` nobody is
        left to set. `revise_by_default` — the answer a headless caller with
        no `decide` of its own already gets — is reused rather than a second
        auto policy invented beside it, so `decide` itself is never called in
        auto mode. `unmet` is the checks that did not hold, carried on the
        answer the way the Plan Gate carries what an auto-approval overruled,
        so the stream says what was decided without asking rather than
        leaving the next reader of it to infer a human was ever there.
        """
        if not applied:
            return REVISE
        if auto_approve:
            events.emit(QUESTION, text=_WHAT_NOW % why, auto=True)
            events.emit(ANSWER, by="auto", text=_DECIDED[REVISE], auto=True,
                        unmet=list(unmet))
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
    last_pass = {"steps": None, "applied": 0}

    from utils import settings_manager
    auto_approve = settings_manager.instance().get_auto_approve_plan()

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
        if revisions.exhausted(budgets, steps, cause):
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

        if planned.unbuildable:
            planned = rewrite(_Revisions.OP_REFUSED,
                              "A Step named an operation off the closed list.",
                              _op_off_the_list(planned.unbuildable), progress["tried"],
                              reason="; ".join(item["why"] for item in planned.unbuildable))
            continue

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
                    asked=(bool(unread) or not standing["approval"]) and not auto_approve,
                    **overruled)
        if auto_approve:
            approval = Approval(APPROVE, standing=True)
            standing["approval"] = True
            standing["read"] |= _assumed(steps)
            events.emit(GATE, decision=APPROVE, standing=True, auto=True, steps=list(steps),
                        **({"assumed": sorted(unread)} if unread else {}))
        elif standing["approval"] and not unread:
            approval = Approval(APPROVE, standing=True)
            events.emit(GATE, decision=APPROVE, standing=True, steps=list(steps))
        else:
            approval = gate(list(steps)) or Approval(APPROVE)
            standing["approval"] = bool(getattr(approval, "standing", False)
                                        and approval.decision == APPROVE)
            if approval.decision == APPROVE:
                standing["read"] |= _assumed(steps)
                if approval.steps:
                    steps = list(approval.steps)
            events.emit(GATE, decision=approval.decision,
                        standing=standing["approval"], steps=list(steps))

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
        carried = _carried_over(last_pass["steps"], steps, last_pass["applied"])
        outcome = _work_through(steps, intent, tools, llm, events, budgets, cancelled,
                                progress, render(part["the_part"]), looks,
                                planned.acceptance, planned.cites, ledger,
                                start=carried, ops=planned.ops)
        last_pass["steps"], last_pass["applied"] = steps, outcome["applied_here"]
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

        views = _review_views(tools, events)
        suspected = review_suspect(intent, views, described, llm, events, attachments)
        events.emit(REVIEW_SUSPECTED, suspected=list(suspected))

        checking = _also_check(_also_check(planned.acceptance, outstanding), suspected)
        not_reached = _not_reached(outcome.get("unbuilt") or [], checking)
        acceptance = [entry for entry in checking if entry not in not_reached]
        checked = (assertion(acceptance, described, tools, llm, events, budgets,
                             cancelled, after_a_retry=outcome.get("retried"))
                   if acceptance else None)
        progress["unmet"] = (list(checked.unmet) if checked is not None else []) \
            + list(not_reached)
        if checked is not None and checked.failed:
            feedback = _why_the_checks_failed(checked.traceback)
            answer = decided(feedback, applied, unmet=checked.unmet)
            if answer == ACCEPT:
                return LoopResult(ok=True, verdict="pass", steps_applied=applied,
                                  unmet=list(progress["unmet"]),
                                  reason="You took the result as it is, although the "
                                         "acceptance checks did not pass.")
            if answer == START_OVER:
                feedback += _MAY_START_AGAIN

            planned = rewrite(_Revisions.ASSERTED,
                              "The acceptance checks have failed again.", feedback,
                              outcome["tried"] + [{"assertion": checked.traceback}])
            continue

        verdict, reason = review(intent, views, described, llm, events, attachments,
                                 checked=checked)
        events.emit(REVIEW_VERDICT, verdict=verdict, reason=reason)

        if verdict != "pass":
            feedback = reason
            answer = decided(reason, applied, unmet=list(progress["unmet"]))
            if answer == ACCEPT:
                return LoopResult(ok=True, verdict="pass", reason=reason,
                                  steps_applied=applied, unmet=list(progress["unmet"]))
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
            continue

        return LoopResult(ok=True, verdict=verdict, reason=reason,
                          steps_applied=applied)


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


def _carried_over(previous, steps, applied):
    """
    How many Steps at the head of a revised Plan are already standing in the
    Document, and so must not be written or applied again.

    `_run` keeps the Steps of the pass it just worked through and how many of
    them landed. A revision is a new Plan (`_run`'s own docstring), but where
    it opens with the same prose as Steps that already applied, those Steps
    describe nothing Execute has not already built — asking for their code
    again is asking for the same Document twice. Matched to a recorded run
    (`logs/sessions/0de34f88d1274dcebb02ac67e0e52fb0.jsonl`): an 11-Step Plan
    revised three times produced 28 `step_started` events over what should
    have been 11, because every revision restarted Execute at Step 1 no
    matter how much of the Plan it left untouched.

    The count is the longest run of identical prose from the head of both
    Plans, capped at how many Steps of `previous` actually landed: a Step
    that was never reached is not carried just because a revision kept its
    words, and a Step a revision changed is never carried past where it
    changed.
    """
    if previous is None:
        return 0
    carried = 0
    for was, now in zip(previous[:applied], steps):
        if was != now:
            break
        carried += 1
    return carried


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


def _trailing_gap(written, number, total):
    """
    Whether Step `number`'s missing code leaves nothing coded after it either,
    up to `total` — a trailing gap, the reply running out of room before it
    got there — as opposed to a Step after `number` landing code in the same
    reply, an interior gap where the model wrote past a Step it skipped.

    Only meaningful where Step `number` itself has no code; the last Step of
    the range has nothing after it to check and is always a trailing gap.
    """
    return not any(written.for_step(later).code for later in range(number + 1, total + 1))


def _work_through(steps, intent, tools, llm, events, budgets, cancelled, progress,
                  as_described="", looks=None, entries=(), cites=(), ledger=None,
                  start=0, ops=()):
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

    One kind of failure is exempted from that count, up to
    `budgets.trailing_gap_retries`: a reply that wrote code up to some Step and
    nothing after it, with nothing coded past the gap either, is a reply that
    ran out of room rather than one that judged the Step and refused it. That
    continuation is free until the allowance is spent; the next one after that
    counts against `attempts_per_step` exactly as any other failure does, so a
    Step whose every reply runs dry still hands back rather than asking forever.

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

    `ledger` is the one thing here that is not reset when `attempts` is: every
    failed attempt, at any Step, is classified into it (`core/ledger.py`)
    before the retry that follows sees it, so a rake this pass already hit at
    Step 4 is in the prompt for Step 5, and for whatever Plan writes if this
    Plan is handed back.

    `start` is where a revised Plan's own head is already standing in the
    Document (`_carried_over`, called by `_run` before this): the Steps below
    it are neither written nor applied again, only accounted for — `APPLIED`
    on the stream and their citations onto `progress["satisfied"]`, exactly as
    a Step that had just landed. The count this pass actually reached, carried
    Steps included, comes back on `applied_here` so the next revision, if
    there is one, has it to carry from in turn.

    `ops` is `planned.ops`, index for index with `steps`: where an entry is
    truthy it rides onto the `apply_step` call as `op`, which is what lets
    `core/lint.py` check the TypeId that Step's code actually builds
    (ADR-0026, FCC-233). A legacy, unmarked Plan's `ops` is all `None`, so
    `apply_step` is called exactly as it always was — no `op` key at all.
"""
    if cancelled():
        raise Stopped()
    described = {}
    attempts = []
    charged = 0
    free_trailing_gaps = 0
    index = 0
    retried = set()
    unbuilt = []
    looks = looks if looks is not None else Looks(cache_path=TYPE_CACHE_PATH, freecad_version=_freecad_version())
    written = execute(steps, tools, llm, events, start, budgets=budgets,
                      as_described=as_described, looks=looks, entries=entries,
                      ledger=ledger)

    while index < start:
        progress["satisfied"].extend(cites[index] if index < len(cites) else ())
        events.emit(APPLIED, step=index + 1, label="", created=[])
        index += 1

    while index < len(steps):
        if cancelled():
            raise Stopped()
        step = steps[index]

        if written.obsolete:
            return {"obsolete": written.obsolete, "handback": None,
                    "description": described, "applied_here": index,
                    "tried": [_tried(step, a) for a in attempts]}

        if not attempts:
            events.emit(STEP_STARTED, index=index + 1, total=len(steps), description=step)

        outcome = written.for_step(index + 1)
        if not outcome.code and written.unbuildable:
            if not _changes_the_shape(step):
                cited = list(cites[index]) if index < len(cites) else []
                unbuilt.append({"step": step, "why": written.unbuildable,
                                "entries": cited})
                events.emit(UNBUILT, step=index + 1, total=len(steps), description=step,
                            why=written.unbuildable, entries=cited)
                index += 1
                attempts = []
                charged = 0
                free_trailing_gaps = 0
                continue
            return {
                "handback": "Step %d of %d could not be built as written: %s\n%s"
                            % (index + 1, len(steps), step, written.unbuildable),
                "tried": [], "obsolete": None, "description": described,
                "applied_here": index,
            }

        ran_dry = False
        if outcome.code:
            arguments = {"code": outcome.code, "label": outcome.label}
            if index < len(ops) and ops[index]:
                arguments["op"] = ops[index]
            events.emit(TOOL_CALL, name="apply_step", arguments=dict(arguments))
            result = tools.call("apply_step", arguments)
            events.emit(TOOL_RESULT, name="apply_step", result=result)
        else:
            ran_dry = _trailing_gap(written, index + 1, len(steps))
            if ran_dry:
                no_code = ("You replied with no code for Step %d, and nothing after it "
                          "either — you %s. Write Step %d and every Step after it, "
                          "shorter this time: no restated Step prose, no comments, "
                          "straight to the calls." % (index + 1, _RAN_OUT_OF_ROOM, index + 1))
            else:
                no_code = ("You replied with no code for Step %d, but wrote code for a "
                          "Step after it — you %s. Write Step %d and every Step after it."
                          % (index + 1, _SKIPPED_A_STEP, index + 1))
            if written.said:
                no_code = "%s\n%s" % (no_code, written.said)
            result = {"status": "failed", "traceback": no_code}

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
            charged = 0
            free_trailing_gaps = 0
            continue

        traceback = result.get("traceback", "The Step failed without saying why.")
        attempts.append(Attempt(code=outcome.code or "", traceback=traceback))
        progress["tried"] = [_tried(step, a) for a in attempts]
        events.emit(PREFLIGHT_FAILED, step=index + 1, attempt=len(attempts),
                    traceback=traceback)
        if ledger is not None:
            ledger.record(step, traceback)

        if ran_dry and free_trailing_gaps < budgets.trailing_gap_retries:
            free_trailing_gaps += 1
        else:
            charged += 1

        if charged >= budgets.attempts_per_step:
            return {
                "handback": "Step %d of %d could not be built after %d attempts: %s\n"
                            "The last traceback was:\n%s%s"
                            % (index + 1, len(steps), len(attempts), step,
                               attempts[-1].traceback, _always_the_same_way(attempts)),
                "tried": [_tried(step, a) for a in attempts],
                "obsolete": None, "description": described,
                "applied_here": index,
            }

        if cancelled():
            raise Stopped()
        written = execute(steps, tools, llm, events, index, attempts, budgets, as_described,
                          looks, entries, ledger)

    return {"obsolete": None, "handback": None, "description": described,
            "applied_here": index,
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
    for phrase, _directive, _label in _FAILED_THIS_WAY:
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
