"""The Execute phase: write code for steps and apply them."""
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field

from core.design.gaps import entries
from core.events import APPLIED, PREFLIGHT_FAILED, TOOL_CALL, TOOL_RESULT
from core.loop.document import copied
from core.tools import ToolError

import tempfile

from core.agent.agent import _FORCED_BY_BUDGET, run_phase
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor
from core.loop.budgets import Budgets
from core.loop.describe import _RAN_OUT_OF_ROOM
from core.lint import OP_TYPEIDS
from core.loop.prompt import EXECUTE_PHASE, prompt
from core.prompts import read_prompt
from core.loop.looks import Looks
from core.logs_root import LOGS




NOT_A_LOOK = ("apply_step", "review_views", "check_assertions")


TYPE_CACHE_PATH = os.path.join(LOGS, "type_cache.json")


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


OP_VERBS = tuple(OP_TYPEIDS)


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
    """
    The code for one Step, the name its undo entry gets, and what it
    delivered -- or, if `on_declaration` rejected it for a reason that
    was not drift, `apply_failed` carries what it said instead. `retries`
    is every traceback from the in-place rewrites `on_declaration` ran
    before `apply_failed` reached its budget; `retried` marks that same
    in-place rewriting on a Step that went on to land.
    """

    code: str = None
    label: str = "Daedalus Step"
    description: dict = None
    created: list = field(default_factory=list)
    apply_failed: str = None
    retries: list = field(default_factory=list)
    retried: bool = False


@dataclass
class Written:
    """
    What one Execute phase produced: each Step's declared code, keyed by
    number, and why a Step it was asked about has none. Keyed by Step
    number, not reply position, so a reply leaving a Step out leaves a
    hole there instead of shifting later Steps' code -- `unbuildable` is
    why a Step was left out of a reply that coded the rest, `said` is what
    a reply carried instead of code, and `forced` is whether the call's own
    turn budget ran out before it declared, making `unbuildable` (if set)
    the forced last word `budget_last_call.md` demands rather than a
    verdict the model reached on its own.

    `rehearsals` counts, per Step number, how many declared attempts
    `apply_step` rejected before the phase moved on or ran out of turns.
    """

    steps: dict = field(default_factory=dict)
    obsolete: str = None
    unbuildable: str = None
    said: str = None
    forced: bool = False
    rehearsals: dict = field(default_factory=dict)

    def for_step(self, number) -> ExecuteOutcome:
        return self.steps.get(number) or ExecuteOutcome()


def execute(steps, tools, llm, events, start=0, attempts=(), budgets=None, as_described="",
            looks=None, entries=(), ledger=None, supervisor=None, kernel=None, ops=(),
            exploration=None, design=None, plan_values=None):
    """
    Run every Step from `start` to the end of the Plan as one agent phase,
    against one kernel held open for the whole call. `Written` comes from
    `run_phase`'s own `declarations`, in build order, not the reply's `steps`
    summary -- the code already lives in the kernel. A reply that writes code
    is not a verdict on the Plan, a regeneration replays from `start` against a
    fresh Snapshot, `exploration` passes through to `run_phase` keyed there by
    `_work_through`'s outstanding Step, `design` is carried read-only into
    every Step's own namespace in Preflight and at Apply, not only into
    `as_described`'s prose for the model to read, and `plan_values` is the
    numbers Plan froze at declare time, read by name from the same namespace.
    """
    budgets = budgets or Budgets()
    request = {"steps": [{"step": number, "do": prose}
                         for number, prose in enumerate(steps[start:], start=start + 1)],
               "document": tools.call("snapshot")}
    if as_described:
        request["what_the_part_is"] = as_described
    if entries:
        request["what_each_entry_says"] = _addressed(entries)
    if plan_values:
        request["plan_values"] = plan_values
    if attempts:
        request["your_failed_attempts_at_this_step"] = [
            {"code": a.code, "traceback": a.traceback} for a in attempts
        ]
        request["what_to_do_now"] = (
            "Step %d has not landed; the Document above is exactly what is there right "
            "now -- read it rather than assume what a failed attempt did or did not "
            "leave behind. Write Step %d again, and every Step after it — the code you "
            "wrote for those assumed objects Step %d never created."
            % (start + 1, start + 1, start + 1)
        )
        again = _the_same_way_twice(attempts)
        if again:
            request["you_have_failed_this_the_same_way_twice"] = again

    looked = looks.still_true() if looks is not None else []
    if looked:
        request["what_you_looked_at"] = looked

    supervisor = supervisor or SessionTreeSupervisor()
    task = json.dumps(request, indent=2, default=str)
    refused = {}
    tried = {}
    free_refusal_spent = set()
    exhausted = set()

    def on_declaration(declaration):
        """
        Replay one declared Step into the live Document via `apply_step` the
        moment it declares. A `SignatureMismatch` answers `drift: True`; any
        other refusal answers `retry: True` and rewinds the kernel, with the
        first such refusal per Step free of charge against
        `budgets.attempts_per_step` whenever `attempts_per_step > 1` --
        bounding the Step at `attempts_per_step + 1` attempts, a Step already
        at that bound refused before `apply_step` runs at all rather than
        left unbounded. Recording the Step to `Ledger` is not this
        function's decision: whether the Step lands or is abandoned is
        `_work_through`'s to find out, from `declaration["retries"]` alone,
        so that is where the one `Ledger` entry per abandoned Step is
        written.
        """
        fix = declaration.get("fix")
        number = declaration.get("step") if fix is None else ("fix", fix)
        if number in exhausted:
            declaration["retries"] = list(tried[number])
            return {"delivered": False, "traceback": tried[number][-1]}
        index = number - 1 if isinstance(number, int) else None
        op = ops[index] if (ops and index is not None and 0 <= index < len(ops)) else None
        arguments = {"code": declaration["code"], "label": declaration.get("label") or "Daedalus Step",
                     "kernel_result": {"created": declaration.get("created") or [],
                                       "deleted": declaration.get("deleted") or [],
                                       "modified": declaration.get("modified") or [],
                                       "objects": declaration.get("objects") or [],
                                       "names": declaration.get("names") or {},
                                       "signature": declaration.get("signature")}}
        if op:
            arguments["op"] = op
        if design:
            arguments["design"] = design
        if plan_values:
            arguments["plan_values"] = plan_values
        events.emit(TOOL_CALL, name="apply_step", arguments=dict(arguments))
        result = tools.call("apply_step", arguments)
        events.emit(TOOL_RESULT, name="apply_step", result=result)
        if result.get("status") == "applied":
            if fix is None:
                events.emit(APPLIED, step=number,
                            label=declaration.get("label") or "Daedalus Step",
                            created=result.get("created", []))
            declaration["description"] = result.get("description")
            declaration["created"] = result.get("created", [])
            if tried.get(number):
                declaration["retried"] = True
            return {"delivered": True}
        drift = bool(result.get("drift"))
        traceback = result.get("traceback")
        tried.setdefault(number, []).append(traceback or "")
        events.emit(PREFLIGHT_FAILED, step=fix if fix is not None else number, attempt=len(tried[number]),
                    traceback=traceback or "", gate=result.get("gate"))
        directive = _the_same_way_twice(
            [Attempt(code="", traceback=t) for t in tried[number]])
        resync = tools.call("document_copy")["path"]
        if number not in free_refusal_spent and budgets.attempts_per_step > 1:
            free_refusal_spent.add(number)
            return {"delivered": False, "traceback": traceback, "retry": True,
                    "drift": drift, "directive": directive, "resync": resync}
        refused[number] = refused.get(number, 0) + 1
        if refused[number] < budgets.attempts_per_step:
            return {"delivered": False, "traceback": traceback, "retry": True,
                    "drift": drift, "directive": directive, "resync": resync}
        exhausted.add(number)
        declaration["retries"] = list(tried[number])
        return {"delivered": False, "traceback": traceback, "drift": drift, "resync": resync}

    working_dir = tempfile.mkdtemp()
    try:
        with copied(tools) as document:
            declared, session, declarations = run_phase(
                pc.EXECUTE, working_dir,
                {"plan": steps},
                llm, task, document=document, design=design,
                kernel=kernel, supervisor=supervisor, stream=events,
                system=prompt(EXECUTE_PHASE),
                ledger=ledger.render() if ledger else "",
                on_declaration=on_declaration, exploration=exploration,
                repair_ceiling=budgets.attempts_per_step,
                already_delivered=range(1, start + 1),
                plan_values=plan_values,
            )

        reply = declared if isinstance(declared, dict) else {}
        forced = bool(reply.pop(_FORCED_BY_BUDGET, False))
        rehearsals = {number: len(attempts) for number, attempts in tried.items()
                      if isinstance(number, int)}

        written = _written(declarations, start)
        if written:
            return Written(steps=written,
                           unbuildable=str(reply.get("unbuildable") or reply.get("obsolete"))
                           if (reply.get("unbuildable") or reply.get("obsolete")) else None,
                           said=_said(reply, declarations, start), forced=forced,
                           rehearsals=rehearsals)
        if reply.get("obsolete"):
            return Written(obsolete=str(reply["obsolete"]), forced=forced, rehearsals=rehearsals)
        if reply.get("unbuildable"):
            return Written(unbuildable=str(reply["unbuildable"]), forced=forced,
                           rehearsals=rehearsals)
        return Written(said=_said(reply, declarations, start), forced=forced,
                       rehearsals=rehearsals)
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)


_SKIPPED_A_STEP = "skipped a Step it could have written"

_THICKNESS_FAILED = "Failed to make thick solid"


def _classified_failures():
    """
    (phrase, directive, label) for every rejection a different attempt
    could fix, keyed on a substring Preflight, lint or apply writes.

    Constraint: no-open-Document and the rolled-back wrapper are absent on
    purpose -- both already match on their own exception text.
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
        ("shape is invalid",
         "a feature was created with doc.addObject but never wired into a Body -- its "
         "own Shape computes and looks correct, but reading the Body's Shape (or a "
         "feature that depends on it) raises this because the Body's own Shape comes "
         "from Body.Tip and stays unset. Call body.addObject(feature) and set "
         "body.Tip = feature, the same way the Body's other features were added; the "
         "same unwired addObject under another name fails identically.",
         "created a PartDesign feature without wiring it into a Body"),
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
        (_THICKNESS_FAILED,
         "the shell could not be built, and asking for it again fails the same way. Build the "
         "same wall by another construction. For an open shell, sketch the inner profile "
         "offset by the wall thickness and pocket it to the depth minus the floor. For a loft "
         "or sweep, build an inner body the same way from offset profiles and remove it with a "
         "Boolean cut.",
         "Thickness could not make a solid, so the wall was never built"),
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
        ("signature did not match what the kernel verified",
         "the GUI Document landed something other than what the kernel verified -- the "
         "same code replayed against the same Document should not drift a second time. "
         "Read the Document above rather than assume what the drifted attempt left "
         "behind, and write the Step again against what is actually there.",
         "the GUI replay drifted from what the kernel verified"),
    )


_FAILED_THIS_WAY = _classified_failures()


def _the_same_way_twice(attempts):
    """
    The directive for a Step that failed twice for the same reason, or None.
    Only the *last two* attempts, and only the same kind of failure — two
    different failures in a row is progress, not a reason to change tack.
    `_FAILED_THIS_WAY` is keyed on a phrase Preflight itself writes, so an
    unrecognised failure stops being classified, not misclassified.
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


def _written(declarations, start):
    """
    Every Step the kernel ran and declared, by Step number, whether or
    not it reached the Document. A delivered Step carries the
    `description`/`created` `on_declaration` wrote there; one rejected
    for a reason that was not drift carries `delivered: False` and its
    traceback as `apply_failed`.
    """
    written = {}
    for position, entry in enumerate(declarations or (), start=start + 1):
        number = entry.get("step")
        written[number if isinstance(number, int) else position] = ExecuteOutcome(
            code=str(entry["code"]), label=str(entry.get("label") or "Daedalus Step"),
            description=entry.get("description"), created=list(entry.get("created") or ()),
            apply_failed=entry.get("traceback") if entry.get("delivered") is False else None,
            retries=list(entry.get("retries") or ()), retried=bool(entry.get("retried")))
    return written


def _said(reply, declarations, start):
    """
    A reply that could leave a Step undeclared, rendered short enough to travel: what it was, not what it wrote.

    Capped at 300 characters — an Execute reply carries a whole Plan's code, so recording it whole puts that code back into the next request. Renders the reply's keys, which `steps` it named against `declarations`, and every other short-string value.
    """
    keys = sorted(reply)
    parts = ["keys: %s" % ", ".join(keys) if keys else "keys: none"]
    steps = reply.get("steps")
    if isinstance(steps, list):
        declared_numbers = {
            entry.get("step") if isinstance(entry.get("step"), int) else position
            for position, entry in enumerate(declarations or (), start=start + 1)
        }
        carried, coded = [], []
        for position, entry in enumerate(steps, start=start + 1):
            if not isinstance(entry, dict):
                continue
            number = entry.get("step")
            number = number if isinstance(number, int) else position
            carried.append(number)
            if number in declared_numbers:
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
    `apply_step` is excluded, since the loop decides when a Step runs and
    a phase applying its own code is a second, unbudgeted route to the
    Document; `review_views` moves the camera, a loop inside a phase.
    """
    return [described for described in tools.describe()
            if described["name"] not in NOT_A_LOOK]


def _emit_plan(events, kind, planned, reason=None, unanswered=(), decided=(),
               **counted):
    """
    A Plan on the stream, in one place -- the panel reads both kinds
    alike. `unanswered` is the only place a Plan's own choices are
    correctable, since the Gate cannot show what it was never handed, and
    `decided` is the same for values settled before Plan saw them.
    `counted` names which revision this is, of how many, and why.
    """
    extra = {"reason": reason} if reason is not None else {}
    events.emit(kind, steps=list(planned.steps), acceptance=list(planned.acceptance),
                unanswered=list(unanswered), decided=list(decided), **extra, **counted)


def _review_views(tools, events):
    """
    The four viewport views for Review, or nothing where there is no GUI.
    Driven by the loop, once per run -- a picture is not something a
    phase gets to ask for. A missing Tool falls through to the same
    nothing as a headless build; a failing one is narrated and let go,
    since a finished run is not lost over its photograph.
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
    """Run one read-only Tool for Execute and hand back what it said."""
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
    Material cards are a build fact taken outside a model turn -- spending
    one on them once cost a run its last Step, declared unbuildable for a
    Tool it was told was unavailable. Unfiltered and not narrated, since a
    page of cards is the request's own question and nothing was asked for
    it; every describe_type already on disk from an earlier run is carried
    the same way, so a type asked about once never costs a second turn.
    """
    looks = Looks(cache_path=TYPE_CACHE_PATH, freecad_version=_freecad_version())
    if "materials" in tools:
        try:
            looks.remember("materials", {}, tools.call("materials", {}))
        except ToolError:
            pass  # except-pass-allowed: materials is optional, its absence here is not the caller's problem
    for arguments, result in looks.disk_types():
        looks.remember("describe_type", arguments, result)
    return looks


_MODEL_IS_INVALID = "recompute left these objects in error"


_DID_NOT_HOLD = "these did not hold: "


_GOING_NOWHERE = read_prompt("going_nowhere.md")


_TURN_BUDGET_SPENT = read_prompt("turn_budget_spent.md")


_TURN_BUDGET_SPENT_REHEARSED = read_prompt("turn_budget_spent_rehearsed.md")


_A_PROPERTY_OF_THE_DOCUMENT = ("material", "appearance", "colour", "color",
                               "density", "transparen", "visibility", "label")


_SHAPES_THE_PART = ("pad", "pocket", "hole", "bore", "fillet", "chamfer", "sketch",
                    "extrud", "revolv", "loft", "sweep", "mirror", "groove",
                    "thicken", "draft", "boolean", "array", "pattern", "shell",
                    "slot", "recess", "taper", "thread", "rib", "fuse", "subtract",
                    "cut", "trim", "split")
