"""The Execute phase: write code for steps and apply them."""
import json
import os
import tempfile
from dataclasses import dataclass, field

from core.design.design import entries
from core.events import TOOL_CALL, TOOL_RESULT
from core.tools import ToolError

import tempfile

from core.agent.agent import run_phase
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor
from core.loop.budgets import Budgets
from core.loop.describe import _RAN_OUT_OF_ROOM
from core.loop.prompt import EXECUTE_PHASE, _ask, prompt
from core.prompts import read_prompt
from core.loop.looks import Looks




NOT_A_LOOK = ("apply_step", "review_views", "check_assertions")


TYPE_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
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


_VIEW_NAMES = ("isometric", "the opposite isometric", "above", "the front", "the section")


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


_WHAT_NOW = ("The result did not pass, and what to do about it is yours to say — take it "
             "as it is, have it revised, or start over.\n\n%s")


_MAY_START_AGAIN = (
    "\n\nThe user has decided you may start again: you may delete what was built and "
    "plan the part from nothing."
)


_SAID_NOTHING = "nothing in the views answers it."


_LOOK_IT_UP = read_prompt("look_it_up.md")


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
            looks=None, entries=(), ledger=None, supervisor=None, kernel=None):
    """
    Write the code for every Step from `start` to the end of the Plan, in one turn.

    22 of roughly 29 provider calls for six applied Steps, and all fifteen
    looks were API discovery rather than geometry — not one returned a
    coordinate the code then used. Every coordinate was derived at runtime
    inside the Step's own code. That is what makes one call possible: a Step
    does not need the Document its predecessor will leave behind, because it
    reads it when it runs (ADR-0021).

    A regeneration is the same call with `start` at the Step that failed. Step
    N and every Step after it are written again, because the code for N+1 was
    written assuming objects Step N was going to create — regenerating N alone
    breaks at the *next* Step's Preflight, silently and one Step late.

    `entries` is the description addressed. In the run that named this the
    lookup was missing and the values were invented from the geometry to hand:
    `arm_z = sum((g.StartPoint.y + g.EndPoint.y) * 0.5 for g in arm_lines) /
    len(arm_lines)` for a hole the description places on the centre plane.

    A reply that wrote code is not a verdict on the Plan: voiding the whole
    Plan is how a recorded run threw away Steps nobody had attempted and
    replanned three times over with nothing applied.
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

    looked = looks.still_true() if looks is not None else []
    if looked:
        request["what_you_looked_at"] = looked

    supervisor = supervisor or SessionTreeSupervisor()
    task = json.dumps(request, indent=2, default=str)
    
    with tempfile.TemporaryDirectory() as working_dir:
        declared, session, checkpoints = run_phase(
            pc.EXECUTE, working_dir,
            {"plan": steps, "design": as_described},
            llm, task,
            kernel=kernel, supervisor=supervisor, stream=events,
            system=prompt(EXECUTE_PHASE, ledger=ledger.render() if ledger else "")
        )
    
    reply = declared if isinstance(declared, dict) else {}

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
    return Written(said=_said(reply, start))


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

    Two layers, on purpose, not one: `core/apply.py :: TRAPS` is
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
    `progress.tried`, then the handback to Plan, and an Execute reply
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


_MODEL_IS_INVALID = "recompute left these objects in error"


_DID_NOT_HOLD = "these did not hold: "


_GOING_NOWHERE = read_prompt("going_nowhere.md")


_A_PROPERTY_OF_THE_DOCUMENT = ("material", "appearance", "colour", "color",
                               "density", "transparen", "visibility", "label")


_SHAPES_THE_PART = ("pad", "pocket", "hole", "bore", "fillet", "chamfer", "sketch",
                    "extrud", "revolv", "loft", "sweep", "mirror", "groove",
                    "thicken", "draft", "boolean", "array", "pattern", "shell",
                    "slot", "recess", "taper", "thread", "rib", "fuse", "subtract",
                    "cut", "trim", "split")
