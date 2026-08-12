"""
Level 1 — the Plan -> Execute -> Review loop. Written first, no FreeCAD present.

The hardest logic in the project, verified the cheapest way: a scripted LLM and
a fake Tool registry. The event stream is an output and is asserted on; it is
never substituted.
"""
import base64
import json
import os
import tempfile

import pytest

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, ERROR, GATE, PART, PLAN,
    PLAN_REVISED, DESIGN, PREFLIGHT_FAILED, QUESTION, REVIEW_VERDICT,
    STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, EventStream,
)
from core import loop, part_file, sheet_reader
from core.freecad_tools import TRAPS
from core.llm import TEXT, Chunk, ProviderRefused, ScriptedLLMClient
from core.design import UNDER, entries, merge, render
from core.loop import (
    PROMPTS, Budgets, prompt, revisions_ceiling, run,
)
from core.tools import ToolError, ToolRegistry

SNAPSHOT = {"document": "Unnamed", "objects": [], "object_count": 0,
            "active_body": None, "selection": []}


def _a_real_png(suffix=".png"):
    """
    Review is meant to look at the render, so the tests hand it a file that
    actually decodes rather than a path to nothing.
    """
    handle, path = tempfile.mkstemp(prefix="loop_render_", suffix=suffix)
    with os.fdopen(handle, "wb") as png:
        png.write(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8Dw"
            "HwAFAAH/q842iQAAAABJRU5ErkJggg=="))
    return path


def _a_real_pdf():
    """The smallest thing on disk that is unambiguously a PDF by name and header."""
    handle, path = tempfile.mkstemp(prefix="loop_drawing_", suffix=".pdf")
    with os.fdopen(handle, "wb") as pdf:
        pdf.write(b"%PDF-1.4\n%%EOF\n")
    return path


RENDER_PNG = _a_real_png()

# What a real Apply hands back: the feature tree with its parameters, and the
# geometry read off the Shape (FCC-81).
DESCRIPTION = {
    "features": [{"name": "Box", "label": "Box", "type": "Part::Box",
                  "parameters": {"Length": "10.0 mm"}}],
    "geometry": {"solids": 1, "surfaces": [{"kind": "Plane", "count": 6}]},
}


def read(value):
    return {"value": value, "flag": "read"}


# The smallest part there is, for the tests that only care that a Design turn
# happened at all.
A_CUBE = {"envelope": {"length": read("10 mm")}}

# A description carrying no value at all: nothing for a Step to cite and nothing
# for the checks to measure, so a run over it has neither a coverage duty nor an
# Assertion phase. What the prompt tests want, which is a Design turn and
# nothing else (FCC-94).
A_DATUM = {"datums": [{"name": "the left end face", "is": "the flat face at the left"}]}

A_PART = {
    "datums": [{"name": "the left end face", "is": "the flat face at the left"}],
    "envelope": {"length": read("80 mm"), "width": read("40 mm"),
                 "thickness": read("10 mm")},
    "features": [{"id": "hole-8-left", "kind": "Ø8 hole", "topology": "through",
                  "axis": read("perpendicular to the top face"),
                  "position": {"the left end face": read("15 mm")},
                  "seen_in": ["top view"]}],
    "not_defined": [{"what": "the material", "looked_in": ["the title block"]}],
}


# ── scripted phase replies ────────────────────────────────────────────────────


def plan_reply(*steps, thinking=None, satisfies=None):
    """
    One Plan turn. `satisfies` is what the *first* Step cites.

    Coverage is checked over the Plan and not over a Step, so one Step citing
    every entry is a Plan that leaves none of them to nobody — which is what
    most of these tests want, since what they are about is somewhere else.
    """
    written = list(steps)
    if satisfies and written:
        written[0] = (dict(written[0], satisfies=list(satisfies))
                      if isinstance(written[0], dict)
                      else {"do": written[0], "satisfies": list(satisfies)})
    body = json.dumps({"steps": written})
    return {"reasoning": thinking, "text": body} if thinking else body


def _grown(models):
    grown = {}
    for model in models:
        grown = merge(grown, model)
    return grown


def everything(*models):
    """
    Every entry a Plan over this description has to account for.

    More than one model where the description grows mid-run: a Plan is checked
    against the model as it stands when it is planned, so the test merges in the
    order the run does.
    """
    return [entry["about"] for entry in entries(_grown(models)) if entry["flag"] == "read"]


def acceptance_of(*models):
    """What a run over this description is checked against — derived, not written."""
    return [entry["says"] for entry in entries(_grown(models))]


def design_reply(model=None, question="", thinking=None):
    """
    One Design turn: what it adds to the part, and what it wants a person asked.

    Every turn of the phase has this one shape — the first pass at the sheet, a
    question put back to it, a person's answer being written in — because every
    one of them ends in a merge into the same model (FCC-93).
    """
    body = json.dumps({"design": model or {}, "question": question})
    return {"reasoning": thinking, "text": body} if thinking else body


def code_reply(*codes, label="Step", thinking=None, first=1):
    """
    One Execute turn: the code for every Step it was asked to write (FCC-95).

    `first` is the number of the Step the first code belongs to, which is 1 for
    the whole Plan and N for a regeneration of Step N and the tail after it.
    """
    body = json.dumps({"steps": [{"step": first + offset, "code": code, "label": label}
                                 for offset, code in enumerate(codes)]})
    return {"reasoning": thinking, "text": body} if thinking else body


def assertion_reply(code, uncovered=None):
    """The Assertion phase's turn: the checks it wrote, and what it could not check."""
    reply = {"code": code}
    if uncovered is not None:
        reply["uncovered"] = list(uncovered)
    return json.dumps(reply)


def obsolete_reply(reason):
    return json.dumps({"obsolete": reason})


def question_reply(question):
    """Plan answering that it cannot plan yet, and why (FCC-25)."""
    return json.dumps({"question": question})


def look_reply(tool, **arguments):
    return json.dumps({"look": {"tool": tool, "arguments": arguments}})


def verdict_reply(verdict, reason="", thinking=None):
    body = json.dumps({"verdict": verdict, "reason": reason})
    return {"reasoning": thinking, "text": body} if thinking else body


# ── the fake Tool registry ────────────────────────────────────────────────────


class FakeTools:
    """
    The registry the loop is given. `applies` decides what apply_step returns,
    one entry per call; anything left over is a success.
    """

    APPLIED = {"status": "applied", "created": ["Box"], "description": DESCRIPTION}

    def __init__(self, applies=(), views=None, checks=()):
        self.applies = list(applies)
        # What is in the Document by now. A Snapshot frozen at empty could not
        # show that a regeneration is written against the Steps that did apply.
        self.created = []
        # What the end-of-run capture answers. None means a registry that has
        # no `review_views` at all, which is the same case as headless.
        self.views = views
        # One entry per `check_assertions` call; anything left over passes.
        self.checks = list(checks)
        self.calls = []
        self.registry = self._build()

    def _build(self):
        registry = ToolRegistry()

        @registry.tool("snapshot", "State right now.")
        def snapshot():
            self.calls.append(("snapshot", {}))
            return dict(SNAPSHOT, objects=[{"name": name} for name in self.created],
                        object_count=len(self.created))

        @registry.tool("overall_size", "How big the part came out.")
        def overall_size():
            self.calls.append(("overall_size", {}))
            return {"size_mm": [40, 30, 10]}

        @registry.tool("supported_types", "Types this build can create.", {
            "type": "object", "properties": {"prefix": {"type": "string"}},
        })
        def supported_types(prefix=None):
            self.calls.append(("supported_types", {"prefix": prefix}))
            return {"types": ["Sketcher::SketchObject"], "count": 1}

        @registry.tool("describe_type", "What a type has, before one exists.", {
            "type": "object", "properties": {"type_id": {"type": "string"}},
            "required": ["type_id"],
        })
        def describe_type(type_id):
            self.calls.append(("describe_type", {"type_id": type_id}))
            return {"type": type_id, "properties": [
                {"name": "Type", "type": "App::PropertyEnumeration",
                 "enumeration": ["Length", "ThroughAll", "UpToFirst"]}]}

        @registry.tool("describe_object", "What an object in the Document has.", {
            "type": "object", "properties": {"name": {"type": "string"}},
            "required": ["name"],
        })
        def describe_object(name):
            self.calls.append(("describe_object", {"name": name}))
            if name not in self.created:
                raise ToolError("No object named %r." % name)
            return {"name": name, "type": "Part::Box",
                    "properties": [{"name": "Length", "type": "App::PropertyLength",
                                    "value": "10.0 mm"}]}

        @registry.tool("apply_step", "Apply one Step.", {
            "type": "object",
            "properties": {"code": {"type": "string"}, "label": {"type": "string"}},
            "required": ["code"],
        })
        def apply_step(code, label="Step"):
            self.calls.append(("apply_step", {"code": code, "label": label}))
            # A None entry, or running off the end, means "this one worked".
            queued = self.applies.pop(0) if self.applies else None
            result = queued if queued is not None else dict(self.APPLIED)
            if result.get("status") == "applied":
                self.created.extend(result.get("created", ()))
            return result

        @registry.tool("check_assertions", "Run read-only checks against a copy.", {
            "type": "object", "properties": {"code": {"type": "string"}},
            "required": ["code"],
        })
        def check_assertions(code):
            self.calls.append(("check_assertions", {"code": code}))
            queued = self.checks.pop(0) if self.checks else None
            return queued if queued is not None else {"ok": True}

        if self.views is not None:
            @registry.tool("review_views", "The views Review judges.",
                           {"type": "object", "properties": {}})
            def review_views():
                self.calls.append(("review_views", {}))
                return {"views": list(self.views)}

        return registry

    def applied_codes(self):
        return [args["code"] for name, args in self.calls if name == "apply_step"]


def failed(traceback):
    return {"status": "failed", "traceback": traceback}


def drive(turns, applies=(), budgets=None, intent="Make a 10mm box", ask=None,
          views=None, checks=(), attachments=(), decide=None, outstanding=()):
    """Run the loop over scripted turns and hand back everything to assert on."""
    llm = ScriptedLLMClient(turns)
    tools = FakeTools(applies, views=views, checks=checks)
    events = EventStream()
    kwargs = {"ask": ask} if ask is not None else {}
    if decide is not None:
        kwargs["decide"] = decide
    result = run(intent, llm=llm, tools=tools.registry, events=events,
                 budgets=budgets or Budgets(), attachments=list(attachments),
                 outstanding=list(outstanding), **kwargs)
    return result, events, llm, tools


# ── the prompts each phase sends ──────────────────────────────────────────────


def _system_messages(llm):
    """The system message of every turn, in order, as the provider saw it."""
    return [call[0]["content"] for call in llm.calls]


def _turns_of(llm, phase):
    """Every request body that phase was sent, in order — one entry per provider call."""
    system = prompt(phase)
    return [json.loads(call[-1]["content"]) for call in llm.calls
            if call[0]["content"] == system]


@pytest.mark.parametrize("turn, phase",
                         [(0, "design"), (1, "plan"), (2, "execute"), (3, "assertion"),
                          (4, "review")])
def test_each_phase_sends_the_prompt_from_its_config_file(turn, phase):
    """
    The file reached the provider — not that the loader returned a string. The
    prompts are config, so this reads the config. FCC-3.

    Driven with an attachment, because Design is a turn only where there is
    something to read (FCC-86); every later phase shifts by one with it.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
        expected = handle.read().strip().replace(
            "{{traps}}", "\n".join("- " + trap for trap in TRAPS))

    assert _system_messages(llm)[turn] == expected


def test_the_traps_land_in_the_prompts_that_ask_for_them():
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    plan_system, execute_system, review_system = _system_messages(llm)

    assert "{{traps}}" not in plan_system
    for text in (plan_system, execute_system):
        assert all(trap in text for trap in TRAPS)
    # Review judges the outcome, not the API, and its file asks for no traps.
    assert TRAPS[0] not in review_system


def test_the_sketch_construction_rule_reaches_plan_and_execute():
    """
    The method rule is prompt copy, not a trap — it is how to build a Sketch,
    not knowledge about this FreeCAD build. It has to reach both phases that
    author geometry, and Review, which only judges the outcome, does not
    need it. FCC-20.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    plan_system, execute_system, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    for text in (plan_system, execute_system):
        assert "computed coordinates" in text or "coordinates you compute" in text
        assert "must genuinely be driven" in text
    assert "same centre" in plan_system
    assert "same centre vector" in execute_system
    assert "never invent a constraint type" in execute_system
    assert "must genuinely be driven" not in review_system


def test_design_is_told_the_outer_contour_is_a_feature():
    """
    Prompt copy, not a trap: it is how a part is described, not knowledge about
    a FreeCAD build. The recorded run
    (`logs/sessions/57af6c09950047bf9f0582cd398d2a66.jsonl`, mm25-p1-1.pdf) put
    "capsule-shaped exterior perimeter" inside a fillet's edge selection, where
    nobody builds from it, and the envelope's three sizes were padded into a
    box that passed every check.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "The outside of the part is a feature, not three numbers" in design_system
    assert "obround" in design_system and "outer-profile" in design_system
    assert "A shape word inside another feature's prose is not the shape being recorded" \
        in design_system


def test_assertion_is_told_what_a_fillet_does_to_the_surface_it_replaced():
    """
    The same run's checks read `MajorRadius == 20` on bore mouths that were
    correctly filleted R2 — a torus of major radius 22 — reported four entries
    as unmet and spent a Plan revision on geometry that was already right.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    assertion_system = " ".join(_system_messages(llm)[3].split())

    assert "A fillet's own surface is not the surface it was cut into" in assertion_system
    assert "minor radius" in assertion_system
    assert "nominal ± the fillet radius" in assertion_system
    assert "Three distances are not a shape" in assertion_system


def test_execute_is_no_longer_asked_to_assert_inside_a_step():
    """
    FCC-54 asked Execute to assert an invariant of its own derivation, because
    at the time there was nowhere else to check a result. There is now: Plan
    writes the Acceptance list and the Assertion phase measures the finished
    run (ADR-0017). What the rule actually produced was ten to twenty
    preconditions per Step — `len(bodies) == 1`, `base.TypeId == 'PartDesign::Pad'`
    — and a precondition that does not hold is an `AssertionError` out of the
    transaction, so it spends one of the Step's three attempts. FCC-83.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    _, execute_system, _ = (" ".join(text.split()) for text in _system_messages(llm))

    for cancelled in ("invariant of its own", "assert it in the Step's code",
                      "thinner as the radius grows"):
        assert cancelled not in execute_system, \
            "execute.md still asks for an assert inside a Step: " + cancelled

    # The rule this one is not: a Step still may not swallow its own traceback.
    assert "Let a Step fail" in execute_system


def test_the_reading_rules_moved_to_the_design_phase_whole():
    """
    FCC-59's rules were live in plan.md and did not hold, so FCC-86 moved who
    reads and when rather than rewriting them a fourth time. They moved again
    with the file when Reading became Design, and they move as they are: what
    is asserted is that every one of them survived, that Plan no longer carries
    any of it, and that Review is still told none of it. FCC-59, FCC-86,
    FCC-93.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    design_system, plan_system, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    for rule in ("A description is not a description of the *picture*",
                 "Design records; it does not invent",
                 "cannot both be true",
                 "an operation the drawing does not show",
                 "Read every plane the drawing uses",
                 "perpendicular"):
        assert rule in design_system, "the Design phase lost: " + rule
        assert rule not in plan_system, "plan.md still carries the Design rule: " + rule
        assert rule not in review_system


def test_a_value_carries_a_named_datum_and_a_read_or_assumed_flag():
    """
    Dimensions were read and positions were not: a magnitude arrived without
    what it was measured from, and a position with no named datum degrades into
    "somewhere sensible", which defaults to centred. Prose is what allows it —
    a sentence carries a number without carrying its datum. FCC-87 demanded the
    three fields in copy; they are now the shape of the model itself, keyed by
    the datum the value is measured from. FCC-87, FCC-93.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    design_system, _, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "Every value carries its datum and its flag" in design_system
    assert 'A value is `{"value": ..., "flag": ...}`' in design_system
    assert "named the way the drawing names it" in design_system
    assert "A magnitude without its datum is the defect this phase exists for" \
        in design_system
    assert "Every value carries its datum and its flag" not in review_system


def test_a_curve_is_not_read_until_its_centre_is_placed():
    """
    On the Model Mania 2025 Phase 1 sheet, `(R30)` places the central pocket's
    side walls and its leader starts at the end-bore axis, which is where the
    arc centre is. The first pass wrote the radius, filed the centre under "cannot
    be read", and Plan then put the centres at ±10 from the middle: the same
    R30, the same 10 mm web beside it, and the wall curving out instead of in.
    A radius with no centre is two shapes, and no number on the sheet chooses
    between them. FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "A curve is not read until its centre is placed" in design_system
    assert "every arc, fillet and round gets **two** values" in design_system
    assert "from the centre of the arc to the arc" in design_system
    # A reference dimension is repeated, not invented.
    assert "it is `read`, never `assumed`" in design_system


def test_the_views_decide_what_no_dimension_arrow_says():
    """
    The run declared the pocket directions "not defined" while SECTION A-A
    shows them outright — hatched where there is material. Which way an arc
    bows, which side is material, whether a cut goes in or a boss stands out:
    none of it is dimensioned on any drawing, and all of it is on the sheet.
    "Not dimensioned" is not "not defined". FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "A value with no dimension arrow on it is not an undefined value" \
        in design_system
    assert "hatched where there is material" in design_system
    assert "Where two readings satisfy the same numbers, say which one the view shows" \
        in design_system
    # And the escape hatch costs something to use.
    assert "name the views you looked in" in design_system


def test_every_callout_printed_on_the_sheet_has_to_be_accounted_for():
    """
    "It often says it cannot see something, and the PDF has everything on it."
    A description written from what the model happened to notice has no way to tell
    that it missed a callout; one written from a sweep of the sheet does, by
    counting. FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "First, sweep the sheet" in design_system
    assert "list **every callout printed on it**" in design_system
    assert "account for every entry on it" in design_system
    assert "illegible" in design_system


def test_a_fraction_of_a_named_overall_is_a_value_on_equal_terms_with_millimetres():
    """
    A picture with no dimensions on it has no millimetres, only proportions.
    Millimetres-only sends it to `question`, which is the behaviour that burned
    the run in FCC-85: the model asked about the medium rather than a value and
    the answer came back nobody's. One assumed overall is a wrong scale, which
    one number at the Plan Gate corrects; a dozen assumed dimensions is a wrong
    shape, which nothing corrects. FCC-87.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    design_system = " ".join(_system_messages(llm)[0].split())

    assert "a fraction of a named overall" in design_system
    assert "a fraction is a full record, not a fallback" in design_system
    assert "Never stop to ask for dimensions" in design_system
    assert "Exactly one assumption in the whole model" in design_system


def test_the_datum_and_the_flag_reach_the_checks_without_anyone_rewriting_them():
    """
    FCC-87 asked Plan, in copy, to carry the datum and the flag into every
    Acceptance entry, and the hole `34.5` and `50.5` came through all the same:
    a rule in a prompt is a rule a phase can miss. There is no rewrite left to
    miss it. Plan is told it does not write the list, and Assertion is told what
    the entries it is handed are. FCC-87, FCC-94.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    _, plan_system, _, assertion_system, _ = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "You do not write the acceptance list" in plan_system
    assert "there is no list for you to write" in plan_system
    assert "The list is not somebody's paraphrase of the drawing" in assertion_system
    assert "carrying the datum it is measured from and how it was arrived at" \
        in assertion_system


def test_the_part_travels_as_a_structure_and_reaches_the_gate_laid_out():
    """
    The structure is the point: it is what the merge can refuse to overwrite,
    and what the Plan Gate can show as datums, an envelope and features rather
    than as paragraphs. It rides the stream as the model and reaches Plan as the
    same thing the user was shown. FCC-87, FCC-93.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Add a box", satisfies=everything(A_PART)),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        assertion_reply("assert True"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(DESIGN).data["model"] == A_PART, "the structure was flattened away"

    shown = json.loads(llm.calls[1][-1]["content"])["what_the_part_is"]
    assert shown == render(A_PART), "Plan and the Gate were shown different things"
    for heading in ("Datums", "Envelope", "Features", "Not defined"):
        assert heading in shown
    assert "the left end face: 15 mm (read)" in shown
    assert "{" not in shown, "Plan was handed a JSON dump"


def test_plan_is_handed_a_finished_description_and_not_the_picture():
    """
    Far too much of the split is invisible in the background otherwise: Plan is
    told plainly that this is a description somebody else produced, that what it
    calls not defined stays not defined, and that nothing arrives beside it to
    be weighed against it. FCC-86, FCC-93.
    """
    _, _, llm, _ = drive([
        design_reply(A_DATUM),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    plan_system = " ".join(_system_messages(llm)[1].split())

    assert "what_the_part_is" in plan_system
    assert "You do not get the picture itself" in plan_system
    assert "What it lists as not defined stays not defined" in plan_system
    assert "nothing arrives beside it to be weighed against it" in plan_system
    assert "you_asked_and_were_answered" not in plan_system, \
        "plan.md still documents the second source this ticket removed"


def test_a_step_is_an_action_a_user_would_perform_not_a_call():
    """
    A Step written at the level of code is a call, and the same call is easy to
    write twice at two granularities — which is how one run drew the hexagon in
    Step 1 and again in Steps 3-8. The rule that closes it is the level: the
    action a user would perform, one piece of work per Step, no sub-steps and
    no restating another Step's work. FCC-74.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    plan_system, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "an action a user would perform" in plan_system
    assert "Execute translates that action into code" in plan_system
    assert "One piece of work is one Step" in plan_system
    assert "no sub-steps of its own" in plan_system
    assert "restates work another Step already carries" in plan_system

    # Decision (a) on FCC-74: the operation is still named, in engineering
    # words rather than the FreeCAD spelling of its type.
    assert "not in the FreeCAD spelling of its type" in plan_system
    # Which element a reference resolves to stays Execute's (ADR-0005).
    assert "never an element" in plan_system
    assert "an action a user would perform" not in review_system


def test_the_granularity_rules_this_reverses_are_gone_from_plan():
    """
    Asserted as a reversal, because copy is only cancelled when it is absent:
    a newer rule standing beside "three Steps, not one" leaves Plan free to
    obey either. FCC-58's four sentences go the same way its own reversal of
    the rules before it went. FCC-74.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    plan_system = " ".join(_system_messages(llm)[0].split())

    for reversed_rule in ("shortest ordered list", "and no smaller",
                          "should achieve, not the code", "one Step, not three",
                          "one FreeCAD operation with all its numbers",
                          "three Steps, not one",
                          "a distinct piece of geometry within a sketch is a Step of its own",
                          "roughly twenty Steps", "too coarse to be judged or corrected"):
        assert reversed_rule not in plan_system, \
            "plan.md still carries the rule this ticket reverses: " + reversed_rule


def test_plan_names_declared_numbers_and_not_how_a_reference_is_found():
    """
    FCC-66 asked Plan for links in the same prompt that forbids it writing how
    a reference is found (ADR-0005), and every Step that attached a sketch then
    failed Preflight for obeying. FCC-71 took the link back; FCC-75 took the
    whole field, and what is asserted here is the half of FCC-71 that outlives
    it — the link is not asked for anywhere.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    plan_system, _, _ = (" ".join(text.split()) for text in _system_messages(llm))

    assert "A link is written as the name you gave" not in plan_system, \
        "plan.md still asks Plan for the link FCC-71 takes back"
    assert '"Profile": "RootProfile"' not in plan_system, \
        "the parameters example still shows a link as a parameter"


def test_the_positive_form_of_the_lint_rules_reaches_execute():
    """
    Lint can only say no. Without the how, the model enumerates workarounds —
    it already answered the one-solid rule with max(solids, key=volume). The
    prompt carries what to build instead; Review is told neither. FCC-60.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    _, execute_system, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "computes its own Shape from its Profile" in execute_system
    assert "addProperty" in execute_system
    assert "Let a Step fail" in execute_system
    assert "bare `except`" in execute_system
    assert "computes its own Shape" not in review_system


def test_the_curve_and_thread_types_are_traps_not_prompt_copy():
    """
    Which types this build offers for a thread and for a free curve is
    knowledge about a specific FreeCAD build, so it lands through TRAPS and
    not in the prompt files. Plan cannot name a feature type it has not been
    told exists. FCC-58.
    """
    threads = [trap for trap in TRAPS if "AdditiveHelix" in trap]
    curves = [trap for trap in TRAPS if "BSplineCurve" in trap]

    assert len(threads) == 1, "the thread types are not a single trap"
    assert "Threaded" in threads[0]
    assert len(curves) == 1, "the curve types are not a single trap"
    assert "involute" in curves[0]
    for phase in ("plan", "execute"):
        with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
            assert "AdditiveHelix" not in handle.read(), \
                "a build-specific type was written into %s.md" % phase


def test_the_tessellated_bound_box_is_a_trap_not_a_number_in_the_prompt():
    """
    That `Shape.BoundBox` answers off the triangulation, and lands inside a
    curved face by the mesh's sagitta, is knowledge about this build — so it
    lands through TRAPS. The prompt says only what to read instead.

    A recorded run placed five sketches at `bb.XMin` of a pad with R25 rounded
    ends and built every bore, hole and pocket 0.18 mm off, in a Document that
    reopens with `XMin` at 0.0.
    """
    boxes = [trap for trap in TRAPS if "optimalBoundingBox" in trap]

    assert len(boxes) == 1, "the BoundBox reading is not a single trap"
    assert "triangulation" in boxes[0]
    assert "Surface.Center" in boxes[0]

    with open(os.path.join(PROMPTS, "execute.md"), encoding="utf-8") as handle:
        execute = " ".join(handle.read().split())
    assert "not off `Shape.BoundBox`" in execute, \
        "Execute no longer says where a coordinate comes from"


def test_the_constraint_spellings_are_a_trap_not_prompt_copy():
    """
    Which constraint types exist is knowledge about a specific FreeCAD build,
    so it lives in TRAPS and lands through the placeholder. FCC-20.
    """
    spellings = [trap for trap in TRAPS if "no 'Concentric'" in trap]

    assert len(spellings) == 1, "the constraint spellings are not a single trap"
    assert "Coincident', a, 3, b, 3" in spellings[0]
    for phase in ("plan", "execute"):
        with open(os.path.join(PROMPTS, phase + ".md"), encoding="utf-8") as handle:
            assert "Concentric" not in handle.read(), \
                "a build-specific spelling was written into %s.md" % phase


def test_the_in_place_vector_methods_are_a_trap_and_reach_the_checks():
    """
    That `App.Vector.multiply` scales the vector it is called on rather than
    returning a scaled copy is knowledge about this build, so it lands through
    TRAPS — and it has to reach Assertion, which is where it was paid for.

    A recorded run's checks projected with `zdir.multiply(dot(raw, zdir))`,
    which zeroed `zdir` and left `xdir` 51.5 times too long, and reported 28 of
    34 entries unmet on a part that met them.
    """
    vectors = [trap for trap in TRAPS if "in place" in trap and "App.Vector" in trap]

    assert len(vectors) == 1, "the in-place Vector methods are not a single trap"
    assert "multiply" in vectors[0] and "sub" in vectors[0]
    assert "{{traps}}" not in prompt("assertion")
    assert vectors[0] in prompt("assertion"), \
        "the checks are not told which Vector methods mutate"


def test_a_missing_prompt_file_raises_rather_than_sending_an_empty_prompt():
    with pytest.raises(IOError):
        prompt("no_such_phase")


# ── Plan ──────────────────────────────────────────────────────────────────────


def test_plan_produces_an_ordered_list_of_steps_from_the_intent_and_a_snapshot():
    result, events, llm, tools = drive([
        plan_reply("Create a 10mm box", "Fillet the top edge"),
        code_reply("doc.addObject('Part::Box', 'Box')", "pass"),
        verdict_reply("pass", "Looks like a 10mm box."),
    ])

    assert result.ok
    planned = events.first(PLAN).data["steps"]
    assert planned == ["Create a 10mm box", "Fillet the top edge"]


def test_plan_is_given_the_intent_and_the_snapshot():
    _, _, llm, tools = drive([
        plan_reply("Only step"),
        code_reply("pass"),
        verdict_reply("pass"),
    ])

    asked = json.dumps(llm.calls[0])
    assert "Make a 10mm box" in asked
    assert "object_count" in asked, "Plan did not see a Snapshot"
    assert ("snapshot", {}) in tools.calls


def test_the_steps_run_in_the_order_the_plan_gave_them():
    _, events, _, tools = drive([
        plan_reply("First", "Second", "Third"),
        code_reply("first()", "second()", "third()"),
        verdict_reply("pass"),
    ])

    assert tools.applied_codes() == ["first()", "second()", "third()"]
    assert [e.data["description"] for e in events.of(STEP_STARTED)] == ["First", "Second", "Third"]


# ── Execute looks before it writes ────────────────────────────────────────────


def test_execute_can_ask_the_document_before_committing_to_code():
    """
    The phase that hallucinates API was the one phase that could not ask:
    `supported_types` had no caller anywhere outside the tests (ADR-0009).
    """
    result, events, llm, tools = drive([
        plan_reply("Add a sketch"),
        look_reply("supported_types", prefix="Sketcher::"),
        code_reply("doc.addObject('Sketcher::SketchObject', 'Sketch')"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert ("supported_types", {"prefix": "Sketcher::"}) in tools.calls

    asked_with = json.loads(llm.calls[2][-1]["content"])
    assert asked_with["what_you_looked_at"][0]["result"]["types"] == ["Sketcher::SketchObject"]


def test_a_look_is_visible_on_the_event_stream():
    _, events, _, _ = drive([
        plan_reply("Add a sketch"),
        look_reply("supported_types", prefix="Sketcher::"),
        code_reply("doc.addObject('Sketcher::SketchObject', 'Sketch')"),
        verdict_reply("pass"),
    ])

    assert "supported_types" in [e.data["name"] for e in events.of(TOOL_CALL)]
    assert "supported_types" in [e.data["name"] for e in events.of(TOOL_RESULT)]


def test_looking_is_budgeted_and_the_last_turn_offers_nothing_to_look_with():
    """Looks are for answering a question, not for stalling instead of coding."""
    result, _, llm, tools = drive([
        plan_reply("Add a sketch"),
        look_reply("supported_types"),
        look_reply("supported_types"),
        code_reply("doc.addObject('Sketcher::SketchObject', 'Sketch')"),
        verdict_reply("pass"),
    ], budgets=Budgets(looks_per_turn=2))

    assert result.ok
    # Two looks, one Tool call: the allowance is spent by asking, and the
    # second answer was already here to give (FCC-99).
    assert len([c for c in tools.calls if c[0] == "supported_types"]) == 1

    final = json.loads(llm.calls[3][-1]["content"])
    assert final["tools_you_may_look_with"] == [], "the last turn still invited a look"
    assert "code" in final["this_is_your_last_turn"]
    # Scoped, not just mentioned: an empty Tool list and an open invitation to
    # declare the Plan obsolete is how one value nobody could look up threw away
    # every Step that was buildable without it.
    assert "obsolete" in final["this_is_your_last_turn"]
    assert "Document" in final["this_is_your_last_turn"]
    assert "still exists" in final["this_is_your_last_turn"]
    assert "this_is_your_last_turn" not in json.loads(llm.calls[1][-1]["content"])


def test_a_last_turn_with_neither_code_nor_obsolete_fails_the_step_not_the_run():
    """
    A model that spends its looks and then asks for another has failed an
    attempt, not broken the loop. FCC-8.

    `revisions=0` keeps this on the terminal path: since FCC-61 a Step that
    cannot be built hands back to Plan, and this test is about the attempt, not
    about the handback.
    """
    result, events, _, _ = drive(
        [plan_reply("Add a sketch")] + [look_reply("supported_types")] * 9,
        budgets=Budgets(looks_per_turn=2, attempts_per_step=3, revisions=0),
    )

    assert not result.ok
    assert events.first(ERROR) is None
    blocked = events.first(BLOCKED)
    assert blocked is not None
    assert "Step 1 of 1" in blocked.data["reason"]
    assert len(blocked.data["tried"]) == 3
    assert all("no code for Step 1" in t["traceback"] for t in blocked.data["tried"])


def test_execute_cannot_apply_its_own_code_through_a_look():
    """Execute proposes; the loop decides when a Step runs."""
    result, _, _, tools = drive([
        plan_reply("Add a box"),
        look_reply("apply_step", code="doc.addObject('Part::Box', 'Sneaky')"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["doc.addObject('Part::Box', 'Box')"]


def test_execute_can_ask_what_a_type_has_before_any_object_of_it_exists():
    """
    `describe_object` needs an object that is already there and
    `supported_types` reports names only, so the properties of a type nothing
    has been built from yet were unreachable — which is how `'Through all'`
    reached `PartDesign::Pocket.Type` and failed Preflight. FCC-95.
    """
    result, _, llm, tools = drive([
        plan_reply("Pocket the plate through"),
        look_reply("describe_type", type_id="PartDesign::Pocket"),
        code_reply("pocket.Type = 'ThroughAll'"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert ("describe_type", {"type_id": "PartDesign::Pocket"}) in tools.calls

    offered = _turns_of(llm, "execute")[0]["tools_you_may_look_with"]
    assert "describe_type" in [described["name"] for described in offered]

    answered = _turns_of(llm, "execute")[1]["what_you_looked_at"][0]
    assert "ThroughAll" in answered["result"]["properties"][0]["enumeration"]


def test_a_look_at_a_tool_that_does_not_exist_is_answered_not_fatal():
    result, _, llm, _ = drive([
        plan_reply("Add a box"),
        look_reply("read_the_manual"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])

    assert result.ok
    answered = json.loads(llm.calls[2][-1]["content"])
    assert "error" in answered["what_you_looked_at"][0]["result"]


# ── Execute ───────────────────────────────────────────────────────────────────


def test_the_whole_plan_is_written_in_one_call():
    """
    Execute cost 22 of roughly 29 provider calls in the recorded run, for six
    applied Steps. It is asked once, with every Step of the Plan in front of it,
    and answers with the code for all of them. FCC-95.
    """
    result, _, llm, tools = drive([
        plan_reply("First", "Second", "Third"),
        code_reply("first()", "second()", "third()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["first()", "second()", "third()"]

    turns = _turns_of(llm, "execute")
    assert len(turns) == 1, "Execute was asked more than once for a Plan that built"
    assert [s["step"] for s in turns[0]["steps"]] == [1, 2, 3]
    assert [s["do"] for s in turns[0]["steps"]] == ["First", "Second", "Third"]


def test_a_plan_that_needs_no_look_costs_no_look():
    """A Plan whose Steps are all buildable is one Execute call and nothing else."""
    _, events, _, tools = drive([
        plan_reply("First", "Second"),
        code_reply("first()", "second()"),
        verdict_reply("pass"),
    ])

    looked = [e.data["name"] for e in events.of(TOOL_CALL)]
    assert set(looked) == {"apply_step"}
    assert not [name for name, _ in tools.calls if name in ("supported_types", "describe_type")]


def test_each_step_still_gets_its_own_started_event():
    """
    The code arrives in one call; it still lands one Step at a time. The panel
    is unchanged and so is the undo history. FCC-95.
    """
    _, events, _, _ = drive([
        plan_reply("First", "Second"),
        code_reply("first()", "second()"),
        verdict_reply("pass"),
    ])

    started = events.of(STEP_STARTED)
    assert [(e.data["index"], e.data["total"]) for e in started] == [(1, 2), (2, 2)]
    assert [e.data["step"] for e in events.of(APPLIED)] == [1, 2]


def test_a_preflight_failure_rewrites_the_failing_step_and_the_whole_tail():
    """
    The code for Step N+1 was written assuming objects Step N was going to
    create. Regenerating Step N alone breaks silently — at the *next* Step's
    Preflight, not at this one. FCC-95.
    """
    result, _, llm, tools = drive(
        [
            plan_reply("First", "Second", "Third"),
            code_reply("first()", "second()", "third()"),
            code_reply("second_again()", "third_again()", first=2),
            verdict_reply("pass"),
        ],
        applies=[None, failed("AttributeError: no Length on this")],
    )

    assert result.ok
    assert tools.applied_codes() == ["first()", "second()", "second_again()", "third_again()"]

    regeneration = _turns_of(llm, "execute")[1]
    assert [s["step"] for s in regeneration["steps"]] == [2, 3], \
        "the tail after the failing Step was not written again"
    assert regeneration["your_failed_attempts_at_this_step"] == [
        {"code": "second()", "traceback": "AttributeError: no Length on this"}]
    assert "Step 2" in regeneration["what_to_do_now"]


def test_the_regeneration_is_written_against_the_document_as_it_stands():
    """
    Not against the Plan's assumption of it: the Steps before the failure are
    applied and the failing one is not, and the Snapshot is re-read so the code
    is written over what is actually there. FCC-95.
    """
    _, _, llm, tools = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()", "second()"),
            code_reply("second_again()", first=2),
            verdict_reply("pass"),
        ],
        applies=[None, failed("boom")],
    )

    regeneration = _turns_of(llm, "execute")[1]
    assert regeneration["document"]["objects"] == [{"name": "Box"}]
    assert regeneration["document"]["object_count"] == 1

    # Re-read rather than remembered: the Snapshot was asked for again.
    last_failure = max(index for index, (name, args) in enumerate(tools.calls)
                       if name == "apply_step" and args["code"] == "second()")
    assert "snapshot" in [name for name, _ in tools.calls[last_failure:]]


def test_a_failure_at_every_step_costs_no_more_than_it_costs_today():
    """
    The worst case degrades to the old shape and no further: one call up front,
    then one per failure. Six Steps that each fail once is seven Execute calls,
    where the per-Step loop spent twelve. FCC-95.
    """
    _, _, llm, _ = drive(
        [
            plan_reply("First", "Second", "Third"),
            code_reply("a1()", "b1()", "c1()"),
            code_reply("a2()", "b2()", "c2()"),
            code_reply("b3()", "c3()", first=2),
            code_reply("c4()", first=3),
            verdict_reply("pass"),
        ],
        applies=[failed("a"), None, failed("b"), None, failed("c"), None],
    )

    assert len(_turns_of(llm, "execute")) == 4


def test_a_step_that_applied_carries_none_of_its_failures_into_the_next_writing():
    """
    `your_failed_attempts_at_this_step` is the failures of the Step being
    written again, and a Step that applied has none. What used to keep Step 2
    from seeing Step 1's traceback was that they were different calls; what
    keeps it now is that the attempts reset when a Step lands. FCC-95.
    """
    _, _, llm, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first_bad()", "second()"),
            code_reply("first_good()", "second()"),
            code_reply("second_good()", first=2),
            verdict_reply("pass"),
        ],
        applies=[failed("AttributeError: no Length on this"), None, failed("a different boom")],
    )

    retry_of_first, retry_of_second = _turns_of(llm, "execute")[1:]

    assert "AttributeError: no Length on this" in json.dumps(retry_of_first)
    assert "AttributeError: no Length on this" not in json.dumps(retry_of_second), \
        "Step 2 was shown Step 1's failure"
    assert retry_of_second["your_failed_attempts_at_this_step"] == [
        {"code": "second()", "traceback": "a different boom"}]


def test_a_step_the_reply_left_out_fails_that_step_and_not_the_run():
    """A Step with no code is an attempt spent, the same as one that failed."""
    result, events, _, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first()"),
            code_reply("second()", first=2),
            verdict_reply("pass"),
        ],
    )

    assert result.ok
    failure = events.first(PREFLIGHT_FAILED)
    assert failure.data["step"] == 2
    assert "no code for Step 2" in failure.data["traceback"]


def test_the_reason_a_recompute_failed_reaches_the_next_attempts_request():
    """
    The reason has to be in the request, not merely on the event stream — a
    causeless message is what made every attempt a re-spelling. FCC-11.
    """
    _, _, llm, _ = drive(
        [
            plan_reply("Add a pad"),
            code_reply("pad_one()"),
            code_reply("pad_two()"),
            verdict_reply("pass"),
        ],
        applies=[failed("Pad (PartDesign::Pad): Linked shape object is empty"), None],
    )

    retry = json.loads(llm.calls[2][-1]["content"])

    assert retry["your_failed_attempts_at_this_step"] == [
        {"code": "pad_one()",
         "traceback": "Pad (PartDesign::Pad): Linked shape object is empty"},
    ]


def test_a_failed_attempt_is_surfaced_before_the_retry():
    _, events, _, _ = drive(
        [
            plan_reply("Only"),
            code_reply("bad()"),
            code_reply("good()"),
            verdict_reply("pass"),
        ],
        applies=[failed("ValueError: nope"), None],
    )

    kinds = [e.kind for e in events]
    assert kinds.index(PREFLIGHT_FAILED) < kinds.index(APPLIED)
    assert events.first(PREFLIGHT_FAILED).data["traceback"] == "ValueError: nope"
    assert events.first(PREFLIGHT_FAILED).data["attempt"] == 1


def test_execute_can_declare_a_step_obsolete_and_plan_revises():
    result, events, _, tools = drive([
        plan_reply("Delete the old pad", "Add the new one"),
        obsolete_reply("There is no pad in this Document; nothing to delete."),
        plan_reply("Add the new one"),
        code_reply("add_new()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None, "the revision was not surfaced"
    assert revised.data["steps"] == ["Add the new one"]
    assert "no pad in this Document" in revised.data["reason"]
    assert tools.applied_codes() == ["add_new()"]


def test_a_revised_plan_says_which_revision_it_is_and_what_caused_it():
    """
    Three Revised Plans hitting the same wall read as three fresh starts, and
    the reason alone never said otherwise — which is what a run that looks
    stalled for no reason actually is.
    """
    _, events, _, _ = drive([
        plan_reply("Delete the old pad", "Add the new one"),
        obsolete_reply("There is no pad in this Document."),
        plan_reply("Add the new one"),
        code_reply("add_new()"),
        verdict_reply("pass"),
    ], budgets=Budgets(revisions=3))

    revised = events.first(PLAN_REVISED).data
    assert (revised["revision"], revised["of"]) == (1, 3)
    assert revised["because"] == "Execute found the Plan out of date"


def test_the_plan_gate_leaves_both_ends_of_its_wait_on_the_stream():
    """
    The Gate is the longest silence in a run. Without it on the stream, a
    session read back cannot tell a user thinking for half an hour from a
    provider taking half an hour.
    """
    _, events, _, _ = drive([
        plan_reply("Add a box"),
        code_reply("add_box()"),
        verdict_reply("pass"),
    ])

    opened, decided = events.of(GATE)
    assert opened.data == {"steps": 1, "asked": True}
    assert decided.data == {"decision": "approve", "standing": False}
    kinds = [e.kind for e in events]
    assert kinds.index(PLAN) < kinds.index(GATE) < kinds.index(STEP_STARTED)


def test_obsolete_beside_code_does_not_throw_that_code_away():
    """
    A complaint about a Step is not a verdict on the Plan.

    A recorded run replanned three times and applied nothing: Execute read the
    whole Plan, found one late Step it did not like, and declared the lot
    obsolete before writing a line — so Steps nobody had attempted died with
    it. Where a reply carries code, what it could not do is what it left out,
    and only that goes back to Plan.
    """
    result, events, _, tools = drive([
        plan_reply("Pad the outline", "Fillet the marked edges"),
        json.dumps({"steps": [{"step": 1, "code": "pad()", "label": "Pad"}],
                    "obsolete": "Step 2 does not say which edges are marked."}),
        plan_reply("Fillet the four corner edges"),
        code_reply("fillet()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["pad()", "fillet()"]
    revised = events.first(PLAN_REVISED)
    assert "which edges are marked" in revised.data["reason"]
    assert revised.data["because"] == "Execute could not build a Step"


def test_a_step_left_out_with_a_reason_hands_back_without_spending_attempts():
    """
    Leaving a Step out is what Execute is told to do with one it cannot build,
    so it must not cost three identical regenerations to say so. The reason is
    the only thing that lets Plan write the Step differently: without it Plan
    hears "no code for Step 2" three times over and learns nothing.
    """
    result, events, llm, tools = drive([
        plan_reply("Pad the outline", "Pocket the hex recess"),
        json.dumps({"steps": [{"step": 1, "code": "pad()", "label": "Pad"}],
                    "unbuildable": "Step 2 cuts a hex inside a bore of the same "
                                   "diameter, so it moves no material."}),
        plan_reply("Pocket a wider hex recess"),
        code_reply("hex()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["pad()", "hex()"]
    assert len(_turns_of(llm, "execute")) == 2, "the left-out Step was written again"
    assert events.first(PREFLIGHT_FAILED) is None
    revised = events.first(PLAN_REVISED)
    assert "moves no material" in revised.data["reason"]
    assert revised.data["because"] == "Execute could not build a Step"


def test_an_obsolete_step_is_never_applied():
    _, _, _, tools = drive([
        plan_reply("Obsolete one"),
        obsolete_reply("Already done."),
        plan_reply("Something else"),
        code_reply("something()"),
        verdict_reply("pass"),
    ])

    assert tools.applied_codes() == ["something()"]


# ── Review ────────────────────────────────────────────────────────────────────


def test_review_receives_the_intent_the_render_and_the_document_state():
    _, _, llm, _ = drive([
        plan_reply("Only"),
        code_reply("box()"),
        verdict_reply("pass"),
    ])

    asked = json.dumps(llm.calls[-1])
    assert "Make a 10mm box" in asked
    assert "Part::Box" in asked, "Review did not see the final Document state"


def test_review_and_assertion_are_both_given_the_whole_description():
    """
    Both phases judge a finished part, so both get the same description of it
    — the features with their parameters and the geometry off the Shape, not
    a list of type names. FCC-81.
    """
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    to_assertion = json.loads(llm.calls[3][-1]["content"])
    # The prose about the views is appended after the JSON, past a blank line.
    to_review = json.loads(llm.calls[4][-1]["content"][0]["text"].split("\n\n")[0])

    assert to_assertion["the_document_as_it_now_stands"] == DESCRIPTION
    assert to_review["the_document_as_it_now_stands"] == DESCRIPTION


def test_review_is_shown_the_render_as_a_picture_not_as_a_path():
    """
    A path is meaningless to a model — it cannot open files. Sending one would
    mean Review judged on the object list while the panel showed the user an
    image the model never saw.
    """
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")],
                         views=[RENDER_PNG])

    content = llm.calls[-1][-1]["content"]
    images = [block for block in content if block.get("type") == "image_url"]

    assert images, "Review was sent no image"
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert RENDER_PNG not in json.dumps(content), "the path was sent instead of the bytes"


def test_review_is_told_when_there_is_no_render_to_judge_by():
    """A sketch-only Step produces no render; Review must not be left guessing."""
    tools = FakeTools(applies=[{"status": "applied", "created": ["Sketch"],
                                "description": {"features": [], "geometry": {}}}])
    llm = ScriptedLLMClient([plan_reply("Only"), code_reply("sketch()"), verdict_reply("pass")])

    run("Sketch a square", llm=llm, tools=tools.registry)

    content = llm.calls[-1][-1]["content"]
    assert not [b for b in content if b.get("type") == "image_url"]
    assert "No render was produced" in content[0]["text"]


# ── attachments ───────────────────────────────────────────────────────────────


def test_an_attachment_reaches_the_design_phase_as_a_picture():
    """
    The panel used to collect the attached paths and throw them away, so the
    user showed the assistant a photo it never saw. FCC-4. The phase that
    receives it is the Design phase since FCC-86; Plan never sees a picture.
    """
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this bracket", llm=llm, tools=FakeTools().registry, attachments=[RENDER_PNG])

    content = llm.calls[0][-1]["content"]
    images = [block for block in content if block.get("type") == "image_url"]

    assert "Build this bracket" in content[0]["text"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert RENDER_PNG not in json.dumps(content), "the path was sent instead of the bytes"
    assert isinstance(llm.calls[1][-1]["content"], str), "Plan was sent the picture"


MODEL_MANIA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "modelmania", "mm25-p1-1.pdf")


# ── the reading, written down (FCC-108) ───────────────────────────────────────


def test_the_part_design_read_is_written_to_a_file_the_run_names():
    """
    The reading used to exist only as prose in the Plan request and one line in
    the transcript, so it could be neither diffed nor corrected.
    """
    from core import part_file

    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    written = events.last(PART).data["path"]
    assert os.path.isfile(written)
    assert part_file.read(written) == events.first(DESIGN).data["model"]


def test_the_file_says_which_sheet_the_part_was_read_from():
    from core import part_file

    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        verdict_reply("pass"),
    ], attachments=[MODEL_MANIA])

    read_from = part_file.provenance(events.last(PART).data["path"])
    assert read_from["sheet"] == os.path.basename(MODEL_MANIA)
    assert read_from["views"], "the views it was read from are not recorded"


# ── the envelope is measured, not asked about (FCC-114) ───────────────────────


A_CLAMP_ENVELOPE = ["envelope → length between the ends: 108 mm (read)",
                    "envelope → depth front to rear: 30 mm (read)",
                    "envelope → height of the right lug: 30 mm (read)"]


def test_the_three_overall_sizes_are_checked_before_anybody_writes_a_check():
    from core.loop import the_envelope_measured

    code = the_envelope_measured(A_CLAMP_ENVELOPE)

    assert "optimalBoundingBox" in code
    assert "[30.0, 30.0, 108.0]" in code
    assert "these did not hold: the envelope" in code


def test_an_envelope_of_anything_but_three_plain_sizes_is_not_measured_here():
    from core.loop import the_envelope_measured

    assert the_envelope_measured(A_CLAMP_ENVELOPE[:2]) == ""
    assert the_envelope_measured(
        ["envelope → length: as long as the arm reaches (assumed)"]) == ""


def test_the_envelope_check_runs_against_the_document_and_can_fail_it():
    """A part of the wrong overall size fails before a provider is asked."""
    A_PART_WITH_AN_ENVELOPE = {
        "envelope": {"length between the ends": {"value": "108 mm", "flag": "read"},
                     "depth front to rear": {"value": "30 mm", "flag": "read"},
                     "height of the right lug": {"value": "30 mm", "flag": "read"}},
    }
    _, events, llm, tools = drive([
        design_reply(A_PART_WITH_AN_ENVELOPE),
        plan_reply("Pad the outline", satisfies=everything(A_PART_WITH_AN_ENVELOPE)),
        code_reply("pad()"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG],
        checks=[{"ok": False, "traceback": "AssertionError: these did not hold: "
                                           "the envelope. ... measures 108.00 x 30.00 "
                                           "x 50.00 mm"}],
        decide=lambda why: "accept")

    ran = [args["code"] for name, args in tools.calls if name == "check_assertions"]
    assert "optimalBoundingBox" in ran[0], "the envelope was not measured first"
    assert events.first(ASSERTION).data["unmet"], "nothing was reported unmet"
    assert not any(call[0]["content"] == prompt("assertion") for call in llm.calls), \
        "a provider was asked to write checks for a part already known to be wrong"


def test_a_step_that_only_passed_on_the_retry_is_named_to_the_checks():
    """
    The ring split of the recorded run was shortened until the Body stayed in
    one piece and the shortened one is what the Document kept: passing the
    second time says the workaround was legal, not that it is still the feature
    the drawing asked for.
    """
    A_SPLIT = {"features": [{"id": "ring-split", "kind": "2 mm split",
                             "width": {"value": "2 mm", "flag": "read"}}]}

    _, _, llm, _ = drive([
        design_reply(A_SPLIT),
        plan_reply("Pocket the split", satisfies=everything(A_SPLIT)),
        code_reply("split_through()"),
        code_reply("split_shortened()"),
        assertion_reply("assert True, 'the split'"),
        verdict_reply("pass"),
    ], applies=[failed("this Step left a Body in more than one piece"), None],
        attachments=[RENDER_PNG])

    touched = _turns_of(llm, "assertion")[0]["entries_a_retry_touched"]
    assert "features → ring-split → width" in touched


def test_a_step_that_landed_first_time_names_nothing_to_the_checks():
    _, _, llm, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert "entries_a_retry_touched" not in _turns_of(llm, "assertion")[0]


# ── Execute is given the values, not asked to derive them (FCC-115) ───────────


def test_execute_is_handed_every_entry_against_the_value_it_resolves_to():
    """
    A Step cites an address; the value it needs is then a lookup. Without it
    the run that named this wrote `arm_z = sum(...) / len(arm_lines)` for a
    hole the description places on the longitudinal centre plane.
    """
    _, _, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Sketch the profile", satisfies=everything(A_PART)),
        code_reply("sketch()"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    addressed = _turns_of(llm, "execute")[0]["what_each_entry_says"]
    assert addressed, "Execute was given the prose and no addresses"
    assert all(": " not in address for address in addressed)
    assert any(value for value in addressed.values())


# ── one Step, one plane (FCC-111) ─────────────────────────────────────────────


THE_CLAMP_OUTLINE = {
    "envelope": {"length": {"value": "108 mm", "flag": "read"}},
    "features": [
        {"id": "outer-profile", "kind": "compound outer profile",
         "plane": {"value": "the plane of the length and the height", "flag": "read"}},
        {"id": "right-mounting-lug", "kind": "R12.5 rounded end lug",
         "plane": {"value": "the plane of the length and the depth", "flag": "read"}},
    ],
}


def _both_planes_in_one_step():
    return plan_reply("Pad the compound outer profile 30 mm",
                      satisfies=everything(THE_CLAMP_OUTLINE))


def _one_plane_each():
    return json.dumps({"steps": [
        {"do": "Pad the outer profile in the plane of the length and the height",
         "satisfies": ["envelope → length", "features → outer-profile → plane"]},
        {"do": "Pad the lug in the plane of the length and the depth",
         "satisfies": ["features → right-mounting-lug → plane"]},
    ]})


def test_a_step_citing_two_planes_is_sent_back_before_it_is_ever_built():
    """
    Step 1 of the run that named this cited the outer profile in the elevation
    and the R12.5 lug in the plan, and drew both in the elevation: the flat end
    face at 108 became a round nose and the lug came out 25 mm tall where its
    own Step says 30.
    """
    _, _, llm, tools = drive([
        design_reply(THE_CLAMP_OUTLINE),
        _both_planes_in_one_step(),
        _one_plane_each(),
        code_reply("profile()", "lug()"),
        assertion_reply("assert True, 'the outline'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    told = [turn.get("what_went_wrong_last_time", "")
            for turn in _turns_of(llm, "plan")]
    assert any("2 different values of `plane`" in said for said in told), told
    assert any("the plane of the length and the depth" in said for said in told)
    assert tools.applied_codes()[:2] == ["profile()", "lug()"]


def test_a_plan_that_keeps_mixing_them_is_not_sent_back_for_ever():
    drive([design_reply(THE_CLAMP_OUTLINE)] + [_both_planes_in_one_step()] * 4
          + [code_reply("profile()"), assertion_reply("assert True, 'the outline'"),
             verdict_reply("pass")],
          attachments=[RENDER_PNG], budgets=Budgets(revisions=2))


def test_a_step_whose_citations_agree_is_planned_once():
    one_plane = {"envelope": {"length": {"value": "108 mm", "flag": "read"}},
                 "features": [THE_CLAMP_OUTLINE["features"][0]]}

    _, _, llm, tools = drive([
        design_reply(one_plane),
        plan_reply("Pad the outer profile", satisfies=everything(one_plane)),
        code_reply("profile()"),
        assertion_reply("assert True, 'the outline'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert len(_turns_of(llm, "plan")) == 1
    assert tools.applied_codes() == ["profile()"]


# ── the sweep is the sheet's, not the model's account of it (FCC-112) ─────────


def test_what_is_printed_on_the_sheet_is_written_by_the_reader_not_the_model():
    """
    230 exact entries came back from one run as 22 sentences, and `off_by` —
    the arithmetic that catches 108 read as 100 — went with them.
    """
    from core import part_file

    _, events, _, _ = drive([
        design_reply(dict(A_CUBE, printed_on_the_sheet=["Page 1: some numbers, "
                                                        "two locations"])),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        verdict_reply("pass"),
    ], attachments=[MODEL_MANIA])

    swept = part_file.read(events.last(PART).data["path"])["printed_on_the_sheet"]
    assert {entry["callout"] for entry in swept} != {"Page 1: some numbers, two locations"}
    assert any(entry.get("off_by") is not None for entry in swept), \
        "what the sheet measures of its own numbers did not survive"


# ── a direction read off the wrong view stops the run (FCC-110) ───────────────


THE_CLAMP_READ_OFF_ONE_VIEW = {
    "envelope": {"length": {"value": "108 mm", "flag": "read"}},
    "features": [
        {"id": "clamp-bore", "kind": "Ø42 bore through the clamp ring",
         "topology": "through",
         "axis": {"value": "the depth", "flag": "read",
                  "round_in": "the front elevation"}},
        {"id": "right-mounting-hole", "kind": "Ø6.6 through hole",
         "topology": "through",
         "axis": {"value": "the depth", "flag": "read",
                  "round_in": "the upper plan"}},
    ],
}


def test_two_holes_read_off_the_wrong_view_never_reach_the_plan():
    """
    The 2023 clamp. Both were built along the ring axis, and the screw that
    exists to pinch the split came out parallel to it. Every finding needed to
    stop it was in the model already.
    """
    result, events, llm, tools = drive([
        design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
        plan_reply("Pad the outer profile"),
        code_reply("pad()"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert not result.ok
    assert not tools.applied_codes(), "it built a part it could not read"
    assert "read off the wrong view" in events.first(BLOCKED).data["reason"]


def test_the_run_that_stops_says_which_file_to_correct():
    """The reading is a file now: the remedy is an edit, not another opinion."""
    _, events, _, _ = drive([
        design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
        plan_reply("Pad the outer profile"),
    ], attachments=[RENDER_PNG])

    assert events.last(PART).data["path"] in events.first(BLOCKED).data["reason"]


THE_CLAMP_READ_PROPERLY = {
    "envelope": {"length": {"value": "108 mm", "flag": "read"}},
    "features": [dict(THE_CLAMP_READ_OFF_ONE_VIEW["features"][1],
                      axis={"value": "the height", "flag": "read",
                            "round_in": "the upper plan"})],
}


def test_a_reading_whose_directions_agree_carries_on():
    result, _, _, tools = drive([
        design_reply(THE_CLAMP_READ_PROPERLY),
        plan_reply("Pocket the hole", satisfies=everything(THE_CLAMP_READ_PROPERLY)),
        code_reply("pocket()"),
        assertion_reply("assert True, 'the hole'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok
    assert tools.applied_codes() == ["pocket()"]


def test_a_run_with_nothing_to_read_writes_no_reading():
    """A run without a drawing describes no part, and a file saying so is a
    file to mistake for a reading later."""
    _, events, _, _ = drive([plan_reply("Only"), code_reply("box()"),
                             verdict_reply("pass")])

    assert not events.of(PART)


def _pictures_shown(llm, at=0):
    return [block for block in llm.calls[at][-1]["content"]
            if block.get("type") == "image_url"]


def _said_to_design(llm, at=0):
    return "\n".join(block.get("text", "") for block in llm.calls[at][-1]["content"]
                      if block.get("type") == "text")


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_sheet_is_shown_as_its_views_and_not_as_one_page():
    """
    The whole point of FCC-102. Sent as one page this sheet arrives at the
    provider's 143 DPI cap with 14 px of ink in a dimension digit; sent as the
    layout plus one picture per view it arrives at 279 DPI. A single picture
    reaching Design means the sheet went out at the resolution that misreads.
    """
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    shown = _pictures_shown(llm)
    assert len(shown) > 1, "the sheet went out whole, which is what FCC-102 is about"
    assert not [b for b in llm.calls[0][-1]["content"] if b.get("type") == "file"], \
        "the PDF also went out as an opaque file block, at the provider's own DPI"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_design_is_told_what_the_sheet_prints_rather_than_asked_to_find_it():
    """
    The text layer is the draughtsman's own strings and cannot be misread, so
    the sweep is handed over instead of being compiled off a picture. `150` and
    `100` are on this sheet; the run that named the defect read one of them as
    something else.
    """
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    said = _said_to_design(llm)
    assert "150" in said and "100" in said
    assert "\u00d8" in said, "nothing warned that the diameter sign is not in the layer"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_callout_the_description_never_used_goes_back_to_design_naming_it():
    """
    The check `printed_on_the_sheet` was written for. A description that drops
    the Ø40 is not caught by counting the model's own sweep against itself —
    the callout is missing from both sides. Held against the file it is caught,
    and the question that comes back says the number.
    """
    thin = {"envelope": {"length": read("150 mm")}}
    llm = ScriptedLLMClient([design_reply(thin), design_reply({}),
                             plan_reply("Only"), code_reply("box()"),
                             verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    asked = json.loads(llm.calls[1][-1]["content"][0]["text"])
    assert "the_question" in asked, "Design was not sent back to the sheet at all"
    assert "40" in asked["the_question"]


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_looking_again_stops_when_it_stops_finding_anything():
    """
    A re-read is a provider call. Design answering with nothing, or with the
    same nothing twice, is a sheet that does not carry the value — and the run
    carries on with what it had rather than spending the budget proving it.
    """
    result, _, llm, _ = drive([design_reply(A_DATUM), design_reply({}),
                               plan_reply("Only"), code_reply("box()"),
                               verdict_reply("pass")],
                              attachments=[MODEL_MANIA])

    assert result.ok, "the run did not finish: the re-look kept going"
    design_turns = [call for call in llm.calls if call[0]["content"] == prompt("design")]
    assert len(design_turns) == 2, \
        "the sheet was asked again after it had already answered with nothing"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_the_second_look_is_bounded_by_the_re_read_ceiling():
    """
    Design finding one more callout every time and never all of them would
    otherwise loop for as long as the sheet has numbers on it.
    """
    llm = ScriptedLLMClient(
        [design_reply({"envelope": {"length": read("150 mm")}})]
        + [design_reply({"envelope": {"width %d" % at: read("%d mm" % at)}})
           for at in (40, 35, 30, 20, 10)]
        + [plan_reply("Only"), code_reply("box()"), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry,
        budgets=Budgets(rereads=2), attachments=[MODEL_MANIA])

    design_turns = [call for call in llm.calls if call[0]["content"] == prompt("design")]
    assert len(design_turns) <= 3, "the re-look ran past its ceiling"


def test_a_pdf_that_cannot_be_rendered_here_still_goes_out_as_a_file_block():
    """
    The fallback, and the reason PyMuPDF is a package the addon wants rather
    than one it needs. A PDF this cannot open — a stub, a truncated file, no
    PyMuPDF at all — takes exactly the path it took before FCC-102: the `file`
    content block LiteLLM carries on /chat/completions, at whatever resolution
    the provider chooses, with the model saying so if it cannot read it (FCC-89).
    """
    drawing = _a_real_pdf()
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this bracket", llm=llm, tools=FakeTools().registry, attachments=[drawing])

    content = llm.calls[0][-1]["content"]
    files = [block for block in content if block.get("type") == "file"]

    assert len(files) == 1, "the PDF was not sent, or was sent as a picture"
    assert files[0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert not [block for block in content if block.get("type") == "image_url"]


def test_a_jpeg_reaches_the_provider_labelled_as_a_jpeg():
    """
    `data:image/png;base64` was stamped on every attachment, so a JPEG or a
    WebP was already going out labelled as a PNG. FCC-89.
    """
    photo = _a_real_png(suffix=".jpg")
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[photo])

    picture = [b for b in llm.calls[0][-1]["content"] if b.get("type") == "image_url"][0]
    assert picture["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_the_render_still_goes_to_review_as_a_png():
    """
    The same helper carries Review's four views and the Worker's render, which
    are PNGs we wrote ourselves. That path must come out of this unchanged.
    """
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")],
                         views=[RENDER_PNG])

    picture = [b for b in llm.calls[-1][-1]["content"] if b.get("type") == "image_url"][0]
    assert picture["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_pdf_rides_into_review_beside_the_render():
    """
    Whatever carries a PDF to the phase that reads it carries it to Review as
    well, or the judgement is handed a drawing it cannot open — or a block the
    provider rejects, which turns a finished run into a Refusal at the last
    step. FCC-89.
    """
    drawing = _a_real_pdf()
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    result = run("Build this", llm=llm, tools=FakeTools(views=[RENDER_PNG]).registry,
                 attachments=[drawing])

    assert result.ok
    content = llm.calls[-1][-1]["content"]
    assert len([b for b in content if b.get("type") == "file"]) == 1
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_several_attachments_all_reach_the_design_phase():
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])
    second = _a_real_png()

    run("From both angles", llm=llm, tools=FakeTools().registry,
        attachments=[RENDER_PNG, second])

    content = llm.calls[0][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 2


def test_an_intent_with_no_attachments_sends_what_it_always_did():
    """Attaching nothing costs nothing: the text-only request keeps its shape."""
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")])

    content = llm.calls[0][-1]["content"]

    assert isinstance(content, str)
    assert json.loads(content)["intent"] == "Make a 10mm box"


def test_an_attachment_that_has_been_moved_is_dropped_not_fatal():
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    result = run("Build this", llm=llm, tools=FakeTools().registry,
                 attachments=["/nowhere/gone.png", RENDER_PNG])

    assert result.ok
    content = llm.calls[0][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_a_run_that_had_an_attachment_says_so_on_the_stream():
    """
    Nothing recorded an attachment at all: the chips came off the bar and the
    per-Session log could not say the run had one, so a replayed transcript
    could not either. FCC-88.
    """
    _, events, _, _ = drive([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")],
                            attachments=[RENDER_PNG])

    attached = events.first(ATTACHED)
    assert attached.data["files"] == [{"name": os.path.basename(RENDER_PNG),
                                       "path": RENDER_PNG}]
    assert [event.kind for event in events].index(ATTACHED) == 0


def test_the_attachment_is_recorded_even_where_the_file_has_gone():
    """
    A clipboard paste is a temp file the panel deletes when the run ends, so
    the record has to survive its file — the name is in the event. FCC-88.
    """
    _, events, _, _ = drive([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")],
                            attachments=["/nowhere/gone.png", RENDER_PNG])

    assert [f["name"] for f in events.first(ATTACHED).data["files"]] == \
        ["gone.png", os.path.basename(RENDER_PNG)]


def test_an_intent_with_no_attachment_adds_no_event():
    _, events, _, _ = drive([plan_reply("Only"), code_reply("box()"),
                             verdict_reply("pass")])

    assert events.first(ATTACHED) is None


# ── the part Design describes ─────────────────────────────────────────────────


def test_the_description_is_an_event_of_its_own_standing_before_the_plan():
    """
    It used to be a field on the Plan event, because Plan produced it. A phase
    of its own emits its own event, and a replayed transcript then shows it in
    the order it was actually produced. It carries the model, so the Session log
    holds the structure and not somebody's rendering of it. FCC-47, FCC-86.
    """
    _, events, _, _ = drive([design_reply(A_PART),
                             plan_reply("Only", satisfies=everything(A_PART)),
                             code_reply("box()"), assertion_reply("assert True"),
                             verdict_reply("pass")],
                            attachments=[RENDER_PNG])

    kinds = [event.kind for event in events]
    assert events.first(DESIGN).data["model"] == A_PART
    assert kinds.index(DESIGN) < kinds.index(PLAN)
    assert "reading" not in events.first(PLAN).data


def test_with_nothing_attached_there_is_no_design_turn_at_all():
    """
    No extra provider call for a sheet that does not exist, and the Plan Gate is
    identical to today's. Design then holds only what the user's answers supply.
    FCC-93.
    """
    _, events, llm, _ = drive([plan_reply("Only"), code_reply("box()"),
                               verdict_reply("pass")])

    assert events.first(DESIGN) is None
    assert len(llm.calls) == 3, "a Design turn was spent on an Intent with no picture"
    assert events.first(PLAN).data["steps"] == ["Only"]


def test_design_asks_the_user_itself_before_any_plan_exists():
    """
    The recorded run closed its first description with "Exact coordinate
    dimensions for the Ø35 hole … not defined" and then let Plan draft Steps
    around the gap, so the question reached the user out of a Plan that had
    already committed to a shape. Design owns the question: it asks for what it
    sees empty in its own model, before Plan, and the answer is merged in.
    FCC-93.
    """
    asked = []

    _, events, llm, _ = drive([
        design_reply(A_PART, question="Where are the Ø40 and Ø35 hole axes?"),
        design_reply({"features": [{"id": "hole-35", "kind": "Ø35 hole",
                                    "position": {"the left end face":
                                                 {"value": "25 mm", "flag": "assumed"}}}]}),
        plan_reply("Only"), code_reply("box()"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], ask=lambda q: asked.append(q) or "25 mm from the left end")

    assert asked == ["Where are the Ø40 and Ø35 hole axes?"]
    kinds = [event.kind for event in events]
    assert kinds.index(QUESTION) < kinds.index(DESIGN) < kinds.index(PLAN), \
        "the question reached the user after a Plan had been drafted"

    # A value the user supplied is assumed; one off the sheet stays read.
    shown = json.loads(llm.calls[2][-1]["content"])["what_the_part_is"]
    assert "the left end face: 25 mm (assumed)" in shown
    assert "the left end face: 15 mm (read)" in shown


def test_the_description_is_carried_into_execute_as_text():
    """
    Execute stays on text deliberately — it is called per Step and again per
    look, so the picture would multiply across dozens of calls.
    """
    _, _, llm, _ = drive([design_reply(A_PART),
                          plan_reply("Only", satisfies=everything(A_PART)),
                          code_reply("box()"), assertion_reply("assert True"),
                          verdict_reply("pass")],
                         attachments=[RENDER_PNG])

    asked = llm.calls[2][-1]["content"]
    assert isinstance(asked, str), "Execute was sent a picture, not text"
    assert json.loads(asked)["what_the_part_is"] == render(A_PART)


def test_the_description_reaches_plan_under_one_key():
    """
    One key, one source. Two were how the 100 mm was lost: the description and
    the answers arrived separately and Plan preferred the newer. FCC-93.
    """
    _, _, llm, _ = drive([design_reply(A_PART),
                          plan_reply("Only", satisfies=everything(A_PART)),
                          code_reply("box()"), assertion_reply("assert True"),
                          verdict_reply("pass")],
                         attachments=[RENDER_PNG])

    request = json.loads(llm.calls[1][-1]["content"])
    assert request["what_the_part_is"] == render(A_PART)
    assert "how_the_drawing_was_read" not in request
    assert "you_asked_and_were_answered" not in request


def test_execute_is_told_nothing_extra_when_nothing_was_described():
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")])

    assert "what_the_part_is" not in json.loads(llm.calls[1][-1]["content"])


def test_a_revision_plans_from_the_same_description_and_does_not_read_again():
    """
    The description depends on neither the Snapshot nor the feedback, so a
    second call at the same picture is a provider call spent on nothing. FCC-86.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("v1", satisfies=everything(A_PART)), code_reply("v1()"),
        assertion_reply("assert True"), verdict_reply("fail", "not right"),
        plan_reply("v2", satisfies=everything(A_PART)), code_reply("v2()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert len(events.of(DESIGN)) == 1
    replan = json.loads(llm.calls[5][-1]["content"])
    assert replan["what_the_part_is"] == render(A_PART)
    assert replan["what_went_wrong_last_time"] == "not right"


def test_review_is_shown_the_drawing_beside_the_render():
    """
    Review compared a render against a sentence: the picture never reached it,
    so "does this resemble what I was shown" was not a question it could ask.
    """
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this bracket", llm=llm, tools=FakeTools(views=[RENDER_PNG]).registry,
        attachments=[RENDER_PNG])

    content = llm.calls[-1][-1]["content"]
    images = [block for block in content if block.get("type") == "image_url"]

    assert len(images) == 2, "Review got the render or the drawing, not both"
    assert all(i["image_url"]["url"].startswith("data:image/png;base64,") for i in images)
    assert RENDER_PNG not in json.dumps(content), "a path was sent instead of the bytes"


def test_review_gets_only_the_render_when_nothing_was_attached():
    """The no-attachment request keeps the shape it has always had."""
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")],
                         views=[RENDER_PNG])

    content = llm.calls[-1][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_an_attachment_that_has_been_moved_does_not_break_review():
    llm = ScriptedLLMClient([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")])

    result = run("Build this", llm=llm, tools=FakeTools(views=[RENDER_PNG]).registry,
                 attachments=["/nowhere/gone.png"])

    assert result.ok
    content = llm.calls[-1][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


def test_review_never_sees_the_plan_the_code_or_executes_reasoning():
    _, _, llm, _ = drive([
        plan_reply("Create a 10mm box using Part::Box"),
        code_reply("doc.addObject('Part::Box', 'Box')",
                   thinking="I will use the Part workbench primitive here."),
        verdict_reply("pass"),
    ])

    asked = json.dumps(llm.calls[-1])
    assert "Create a 10mm box using Part::Box" not in asked, "Review saw the Plan"
    assert "doc.addObject" not in asked, "Review saw the code"
    assert "Part workbench primitive" not in asked, "Review saw Execute's reasoning"


def test_a_failed_verdict_returns_to_plan_and_never_to_execute():
    _, events, llm, _ = drive([
        plan_reply("Make it"),
        code_reply("make()"),
        verdict_reply("fail", "The box is 20mm, not 10mm."),
        plan_reply("Set Length to 10"),
        code_reply("fix()"),
        verdict_reply("pass"),
    ])

    # The 4th call is Plan again, carrying the verdict; the 5th is Execute.
    to_plan = json.dumps(llm.calls[3])
    to_execute = json.dumps(llm.calls[4])

    assert "The box is 20mm, not 10mm." in to_plan
    assert "The box is 20mm, not 10mm." not in to_execute, "the verdict reached Execute"
    assert events.first(PLAN_REVISED).data["steps"] == ["Set Length to 10"]


def test_the_verdict_is_surfaced_either_way():
    _, events, _, _ = drive([
        plan_reply("Only"),
        code_reply("box()"),
        verdict_reply("pass", "That is a 10mm box."),
    ])

    verdict = events.first(REVIEW_VERDICT)
    assert verdict.data["verdict"] == "pass"
    assert verdict.data["reason"] == "That is a 10mm box."


# ── Budgets ───────────────────────────────────────────────────────────────────


def test_three_attempts_per_step_then_blocked_with_what_was_tried():
    """`revisions=0`: the handback path has its own tests, this one is the budget."""
    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            code_reply("try2()"),
            code_reply("try3()"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
        budgets=Budgets(revisions=0),
    )

    assert not result.ok
    assert tools.applied_codes() == ["try1()", "try2()", "try3()"]
    blocked = events.first(BLOCKED)
    assert blocked is not None
    assert len(blocked.data["tried"]) == 3
    assert [t["traceback"] for t in blocked.data["tried"]] == ["one", "two", "three"]


def test_the_acceptance_list_rides_with_the_plan_it_belongs_to():
    """
    One list for the whole Plan, not one entry per Step: what is checked is the
    finished Document, and the check runs once the run is over. It reaches the
    Plan Gate through the event, because a criterion the user cannot see is a
    criterion they cannot correct. FCC-75.
    """
    _, events, _, _ = drive([
        design_reply(A_PART),
        plan_reply("Sketch the profile", "Pad it 10 mm", satisfies=everything(A_PART)),
        code_reply("sketch()", "pad()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    plan = events.first(PLAN)
    assert plan.data["steps"][1] == "Pad it 10 mm"
    assert plan.data["acceptance"] == acceptance_of(A_PART)


def test_a_run_with_nothing_described_still_plans_and_shows_an_empty_acceptance():
    """
    An empty list is not fatal and never was: the run goes on and Review judges
    alone, as it did before there was a list at all. Making it fatal would kill
    runs over a prompt regression, and the one fatal answer stays "no Steps".
    Derived rather than written, it is now the shape of a run with no drawing
    and no answers — which still plans. FCC-75, FCC-94.
    """
    result, events, _, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert events.first(PLAN).data["acceptance"] == []


def test_no_step_carries_named_parameters_any_more():
    """
    The whole mechanism, gone at every layer it crossed: the Plan event, the
    Execute request and the apply call. `type` plus real FreeCAD property names
    *is* Plan writing code, which is the level FCC-74 named as the root — the
    field kept demanding it. FCC-75.
    """
    _, events, llm, tools = drive([
        plan_reply({"do": "Pad the base",
                    "parameters": {"type": "PartDesign::Pad", "properties": {"Length": 10}}}),
        code_reply("pad()"),
        verdict_reply("pass"),
    ])

    assert events.first(PLAN).data["steps"] == ["Pad the base"]
    assert "parameters" not in events.first(PLAN).data
    assert "the_operation_this_step_names" not in llm.calls[1][1]["content"]
    assert all("parameters" not in args
               for name, args in tools.calls if name == "apply_step")


def test_review_is_no_longer_told_the_bounding_box():
    """
    `overall_size` is a union of `shape.BoundBox` — the axis-aligned envelope,
    with the flaw that makes it not a measurement: a nut turned 17° reports
    14.626 × 14.355 × 6.500 and not one of those numbers is on the drawing.
    Its place is taken by the acceptance list. FCC-75.
    """
    _, _, llm, tools = drive([
        plan_reply("Pad the base"), code_reply("pad()"), verdict_reply("pass"),
    ])

    # The prose about the views is appended after the JSON, past a blank line.
    review_request = llm.calls[-1][1]["content"][0]["text"].split("\n\n")[0]

    assert "overall_size_mm" not in json.loads(review_request)
    assert not any(name == "overall_size" for name, _ in tools.calls)


def test_plan_is_told_to_cite_and_told_it_does_not_write_the_list():
    """
    Plan gets smaller: it owns the path and nothing else. What replaces the copy
    about writing a list is the copy about citing one — the identity of a
    feature is settled before Plan is called, so the same cavity read off three
    views is one Step and Plan is told outright it may not decide that again.
    FCC-75, FCC-94.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    plan_system = " ".join(_system_messages(llm)[0].split())

    assert "what_the_plan_must_satisfy" in plan_system
    assert "Every entry marked `(read)` must be cited by at least one Step" in plan_system
    assert "Coverage, not exclusivity" in plan_system
    assert "it is not yours to decide again" in plan_system
    assert "You do not write the acceptance list" in plan_system

    # `Part.LineSegment` alone still appears in the traps, where it is a fact
    # about this build; what goes is Plan being asked to write it as a parameter.
    for cancelled in ("Every Step carries its **parameters**",
                      '"parameters": {"type": "PartDesign::Pad"',
                      '{"type": "Part.LineSegment"', '"properties": {"Length": 10}',
                      "twenty is the ceiling"):
        assert cancelled not in plan_system, \
            "plan.md still carries copy that has been removed: " + cancelled


def test_the_named_parameters_mechanism_is_gone_from_the_code():
    """
    Not merely unused — absent. A comparator left importable is a comparator
    the next ticket wires back in. FCC-75.
    """
    with pytest.raises(ImportError):
        from core.preflight_worker import wrong_parameters    # noqa: F401

    # The real registry, not the fake one: the schema is what reaches the model.
    from core.freecad_tools import build_registry
    described = {tool["name"]: tool for tool in build_registry(pipeline=object()).describe()}
    assert "parameters" not in described["apply_step"]["parameters"]["properties"]


def test_review_is_given_four_views_and_execute_cannot_ask_for_one():
    """
    One view is not enough: the mirrored gear of FCC-54 reads only from above,
    and in isometry the tooth flank is foreshortened, which is exactly why
    Review passed it. Execute may not ask for a view either — a picture on
    request would be a loop inside a phase. FCC-63.
    """
    views = [RENDER_PNG] * 4
    _, _, llm, tools = drive(
        [plan_reply("Add a box"),
         code_reply("doc.addObject('Part::Box', 'Box')"),
         verdict_reply("pass")],
        views=views,
    )

    assert [name for name, _ in tools.calls].count("review_views") == 1

    review_content = llm.calls[2][1]["content"]
    assert sum(1 for part in review_content if part["type"] == "image_url") == 4
    assert "the same result seen from" in review_content[0]["text"]
    assert "not several" in review_content[0]["text"]

    # review_views is not in the list Execute was offered to look with.
    assert "review_views" not in llm.calls[1][1]["content"]


def test_a_run_with_no_views_still_reaches_a_verdict():
    """
    No views still degrades Review to the Description alone, and a registry with
    no `review_views` at all — an older one, a test one — is the same case as
    a headless one. FCC-63.
    """
    result, _, llm, _ = drive(
        [plan_reply("Add a box"),
         code_reply("doc.addObject('Part::Box', 'Box')"),
         verdict_reply("pass")],
        # Headless: no views at the end, and no Step ever produced a picture.
        applies=[{"status": "applied", "created": ["Box"], "description": {}}],
        views=[],
    )

    assert result.ok
    assert "No render was produced" in llm.calls[2][1]["content"][0]["text"]


def test_a_step_that_cannot_be_built_returns_to_plan():
    """
    Execute has one escape today and it is narrow: `obsolete` means the Step no
    longer makes sense. "I cannot build this" was not handled at all — the run
    died with whatever had already been applied left in the Document. FCC-61.
    """
    result, events, _, tools = drive(
        [
            plan_reply("Impossible"),
            code_reply("nope1()"), code_reply("nope2()"), code_reply("nope3()"),
            plan_reply("Possible"),
            code_reply("yes()"),
            verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
    )

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None
    assert revised.data["steps"] == ["Possible"]
    # The Plan is told what could not be built and what came back from trying.
    assert "could not be built" in revised.data["reason"]
    assert "three" in revised.data["reason"]


def test_the_replan_after_a_handback_reads_the_document_as_it_is():
    """
    The Steps already applied are not repeated: the revision is planned from a
    fresh Snapshot, the same path `obsolete` already takes. FCC-61.
    """
    _, _, _, tools = drive(
        [
            plan_reply("First", "Impossible"),
            code_reply("first()", "nope1()"),
            code_reply("nope2()", first=2), code_reply("nope3()", first=2),
            plan_reply("Possible"),
            code_reply("yes()"),
            verdict_reply("pass"),
        ],
        applies=[None, failed("one"), failed("two"), failed("three")],
    )

    last_failure = max(index for index, (name, args) in enumerate(tools.calls)
                       if name == "apply_step" and args["code"] == "nope3()")
    after = [name for name, _ in tools.calls[last_failure:]]
    assert "snapshot" in after, "the revision was planned without re-reading the Document"
    # The Step that did apply is not planned again.
    assert tools.applied_codes() == [
        "first()", "nope1()", "nope2()", "nope3()", "yes()"]


def test_a_run_out_of_revisions_stops_and_names_handbacks_not_amendments():
    """
    FCC-22's rule survives the new cause: whichever branch spends the last
    unit is not blamed for the others. A run stopped by handbacks does not
    report amendments. FCC-61.
    """
    result, _, _, _ = drive(
        [
            plan_reply("Impossible"),
            code_reply("nope1()"),
            plan_reply("Impossible again"),
            code_reply("nope2()"),
        ],
        applies=[failed("one"), failed("two")],
        budgets=Budgets(attempts_per_step=1, revisions=1),
    )

    assert result.blocked
    assert "could not build a Step 1 time" in result.reason
    assert "amended" not in result.reason
    assert "Review rejected" not in result.reason


def test_the_revisions_ceiling_is_computed_from_the_plans_length():
    """
    A four-Step Plan and a forty-Step Plan cannot have the same allowance —
    a long Plan has more places to hand back from, and every handback spends
    one. The configured value is the floor. FCC-61.
    """
    assert revisions_ceiling(Budgets(), ["s"] * 4) == 3
    # A nut planned as actions is six to eight Steps, and still gets the floor.
    assert revisions_ceiling(Budgets(), ["s"] * 6) == 3
    assert revisions_ceiling(Budgets(), ["s"] * 14) == 3
    assert revisions_ceiling(Budgets(), ["s"] * 15) == 3
    assert revisions_ceiling(Budgets(), ["s"] * 40) == 8
    assert revisions_ceiling(Budgets(revisions=10), ["s"] * 4) == 10


def test_the_attempt_budget_is_per_step_not_per_run():
    result, _, _, tools = drive(
        [
            plan_reply("First", "Second"),
            code_reply("a1()", "b1()"),
            code_reply("a2()", "b1()"),
            code_reply("b2()", first=2),
            verdict_reply("pass"),
        ],
        applies=[failed("a"), None, failed("b"), None],
    )

    assert result.ok
    assert tools.applied_codes() == ["a1()", "a2()", "b1()", "b2()"]


def test_two_review_cycles_per_plan_then_blocked():
    result, events, _, _ = drive([
        plan_reply("v1"), code_reply("v1()"), verdict_reply("fail", "not right"),
        plan_reply("v2"), code_reply("v2()"), verdict_reply("fail", "still not right"),
        plan_reply("v3"), code_reply("v3()"), verdict_reply("fail", "no better"),
    ])

    assert not result.ok
    assert len(events.of(REVIEW_VERDICT)) == 3
    blocked = events.first(BLOCKED)
    assert "review" in blocked.data["reason"].lower()
    assert "no better" in json.dumps(blocked.data["tried"])


def test_repeated_obsolete_declarations_cannot_loop_forever():
    result, events, _, _ = drive([
        plan_reply("a"), obsolete_reply("stale 1"),
        plan_reply("b"), obsolete_reply("stale 2"),
        plan_reply("c"), obsolete_reply("stale 3"),
        plan_reply("d"), obsolete_reply("stale 4"),
    ])

    assert not result.ok
    assert events.first(BLOCKED) is not None


def test_budgets_are_configurable():
    result, _, _, tools = drive(
        [plan_reply("Only"), code_reply("once()")],
        applies=[failed("no")],
        budgets=Budgets(attempts_per_step=1),
    )

    assert not result.ok
    assert tools.applied_codes() == ["once()"]


def test_blocked_waits_for_the_user_rather_than_failing_silently():
    result, events, _, _ = drive(
        [plan_reply("Only"), code_reply("x()")],
        applies=[failed("no")],
        budgets=Budgets(attempts_per_step=1, revisions=0),
    )

    assert result.blocked
    assert not result.ok
    assert result.awaiting_user
    assert events[-1].kind == BLOCKED


# ── A provider that would not answer ──────────────────────────────────────────


def a_refusal():
    return ProviderRefused(
        "gemini/gemini-2.5-pro refused the request: your quota for this model is exhausted. "
        "The provider says to retry in 26s.",
        said='{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}',
    )


def test_a_refused_execute_ends_blocked_with_the_provider_s_reason():
    result, events, _, _ = drive([
        plan_reply("First", "Second"), code_reply("first()"), a_refusal(),
    ])

    assert result.blocked and result.awaiting_user
    assert "quota" in result.reason and "26s" in result.reason
    assert events[-1].kind == BLOCKED
    assert events.of(ERROR) == [], "a refusal was reported as a crash"


def test_the_steps_already_applied_are_still_counted_when_the_provider_refuses():
    result, _, _, tools = drive([
        plan_reply("First", "Second"), code_reply("first()"), a_refusal(),
    ])

    assert result.steps_applied == 1
    assert tools.applied_codes() == ["first()"]


def test_the_provider_s_verbatim_words_reach_the_session_log():
    _, events, _, _ = drive([
        plan_reply("Only"), a_refusal(),
    ])

    assert "RESOURCE_EXHAUSTED" in events.first(BLOCKED).data["provider_said"]
    assert "RESOURCE_EXHAUSTED" in events.as_log()


def test_half_a_step_is_never_applied_when_the_stream_dies_partway():
    result, _, _, tools = drive([
        plan_reply("Only"),
        [Chunk(TEXT, '{"code": "doc.addObject('), a_refusal()],
    ])

    assert result.blocked
    assert tools.applied_codes() == [], "a half-streamed Step reached the Document"


def test_what_was_tried_before_the_refusal_is_still_reported():
    result, _, _, _ = drive(
        [plan_reply("Only"), code_reply("bad()"), a_refusal()],
        applies=[failed("boom")],
    )

    assert [t["traceback"] for t in result.tried] == ["boom"]


def test_a_refusal_during_plan_ends_the_same_way_as_one_during_execute():
    result, events, _, _ = drive([a_refusal()])

    assert result.blocked
    assert "quota" in result.reason
    assert events[-1].kind == BLOCKED


def test_a_genuinely_malformed_reply_still_says_it_was_malformed():
    """Twice, because the first prose reply is asked again rather than fatal."""
    result, events, _, _ = drive([plan_reply("Only"), "not JSON at all",
                                  "still not JSON"])

    assert not result.blocked
    assert "did not reply with JSON" in result.reason
    assert events.first(ERROR) is not None


def test_a_reply_that_is_all_reasoning_and_no_answer_is_not_called_malformed():
    """
    Kimi-k3 spent a whole design turn thinking and stopped without answering.
    "did not reply with JSON. It said: " with nothing after it told the user
    nothing about what went wrong (FCC-108).
    """
    result, events, _, _ = drive([{"reasoning": "The centreline should be around y=247."},
                                  {"reasoning": "Still thinking about the centreline."}],
                                 attachments=[RENDER_PNG])

    assert "did not reply with JSON" not in result.reason
    assert "reasoning" in result.reason and "design" in result.reason
    assert "output tokens" not in result.reason, "a cap it never reached is the wrong advice"
    assert events.first(ERROR) is not None


# ── The event stream ──────────────────────────────────────────────────────────


def test_the_event_stream_of_a_clean_run():
    _, events, _, _ = drive([
        plan_reply("Only", thinking="A box is a Part::Box."),
        code_reply("box()", thinking="addObject then set Length."),
        verdict_reply("pass", "Good.", thinking="The render shows a cube."),
    ])

    # Execute's reasoning stands before the first Step starts rather than
    # inside it: it is reasoning about the whole Plan, and it is spent before
    # any Step runs (FCC-95). What a Step still has of its own is
    # `step_started`, the apply and `applied`.
    assert [e.kind for e in events] == [
        THOUGHT, PLAN, GATE, GATE,
        THOUGHT, STEP_STARTED, TOOL_CALL, TOOL_RESULT, APPLIED,
        THOUGHT, REVIEW_VERDICT,
    ]


def test_the_event_stream_of_a_clean_run_with_acceptance():
    """
    The same run with something to check: the Assertion phase stands between
    the last Step and the verdict, and its code rides on the Tool call the way
    a Step's does. FCC-76.
    """
    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass", "Good."),
    ], attachments=[RENDER_PNG])

    assert [e.kind for e in events] == [
        ATTACHED, DESIGN, PART, PLAN, GATE, GATE,
        STEP_STARTED, TOOL_CALL, TOOL_RESULT, APPLIED,
        TOOL_CALL, TOOL_RESULT, ASSERTION,
        REVIEW_VERDICT,
    ]


def test_the_event_stream_of_a_retried_run():
    _, events, _, _ = drive(
        [plan_reply("Only"), code_reply("bad()"), code_reply("good()"), verdict_reply("pass")],
        applies=[failed("boom"), None],
    )

    assert [e.kind for e in events] == [
        PLAN, GATE, GATE,
        STEP_STARTED,
        TOOL_CALL, TOOL_RESULT, PREFLIGHT_FAILED,
        TOOL_CALL, TOOL_RESULT, APPLIED,
        REVIEW_VERDICT,
    ]


def test_the_event_stream_of_a_blocked_run():
    _, events, _, _ = drive(
        [plan_reply("Only"), code_reply("x()")],
        applies=[failed("boom")],
        budgets=Budgets(attempts_per_step=1, revisions=0),
    )

    assert [e.kind for e in events] == [
        PLAN, GATE, GATE, STEP_STARTED, TOOL_CALL, TOOL_RESULT, PREFLIGHT_FAILED,
        BLOCKED,
    ]


def test_tool_calls_and_their_results_are_both_visible():
    _, events, _, _ = drive([
        plan_reply("Only"), code_reply("box()", label="Add the box"), verdict_reply("pass"),
    ])

    call = events.first(TOOL_CALL)
    result = events.first(TOOL_RESULT)
    assert call.data == {"name": "apply_step", "arguments": {"code": "box()", "label": "Add the box"}}
    assert result.data["name"] == "apply_step"
    assert result.data["result"]["status"] == "applied"


def test_no_step_puts_a_picture_in_the_stream():
    """
    The per-Step viewport screenshot is gone, panel and all (FCC-81). Nobody
    read it: the only images that reach a model go through `_as_image`, and
    the step render was never among them.
    """
    _, events, _, _ = drive([
        plan_reply("Only"), code_reply("box()"), verdict_reply("pass"),
    ])

    assert [e for e in events if e.kind == "render"] == []
    assert "render" not in events.first(TOOL_RESULT).data["result"]


def test_reasoning_is_streamed_tagged_with_the_phase_it_came_from():
    _, events, _, _ = drive([
        plan_reply("Only", thinking="planning out loud"),
        code_reply("box()", thinking="coding out loud"),
        verdict_reply("pass", thinking="judging out loud"),
    ])

    assert [(e.data["phase"], e.data["text"]) for e in events.of(THOUGHT)] == [
        ("plan", "planning out loud"),
        ("execute", "coding out loud"),
        ("review", "judging out loud"),
    ]


def test_the_stream_can_be_watched_live():
    seen = []
    events = EventStream()
    events.subscribe(lambda event: seen.append(event.kind))

    run("Make a box", llm=ScriptedLLMClient([
        plan_reply("Only"), code_reply("box()"), verdict_reply("pass"),
    ]), tools=FakeTools().registry, events=events)

    assert seen == [e.kind for e in events]
    assert seen[0] == PLAN


# ── Failure modes ─────────────────────────────────────────────────────────────


def test_a_reply_that_is_not_json_is_an_error_the_user_is_told_about():
    result, events, _, _ = drive(["I would love to help but here is prose instead.",
                                  "Still prose, I am afraid."])

    assert not result.ok
    error = events.first(ERROR)
    assert error is not None
    assert "json" in error.data["message"].lower()


def test_a_plan_with_no_steps_is_an_error_not_a_silent_success():
    result, events, _, _ = drive([plan_reply()])

    assert not result.ok
    assert events.first(ERROR) is not None


def test_a_provider_failure_is_surfaced_rather_than_swallowed():
    class Exploding:
        def stream(self, messages, **kw):
            raise RuntimeError("429 rate limited")

    events = EventStream()
    result = run("Make a box", llm=Exploding(), tools=FakeTools().registry, events=events)

    assert not result.ok
    assert "429 rate limited" in events.first(ERROR).data["message"]


# ── Stopping mid-run ──────────────────────────────────────────────────────────


def test_the_user_can_stop_between_steps():
    """User story 18: never a passenger."""
    stop = {"now": False}
    events = EventStream()
    tools = FakeTools()

    def cancel_after_the_first_step(event):
        if event.kind == APPLIED:
            stop["now"] = True

    events.subscribe(cancel_after_the_first_step)

    result = run("Make a box", llm=ScriptedLLMClient([
        plan_reply("First", "Second", "Third"),
        code_reply("first()"),
        code_reply("second()"),
    ]), tools=tools.registry, events=events, cancelled=lambda: stop["now"])

    assert result.blocked
    assert result.awaiting_user
    assert "Stopped by you" in result.reason
    assert tools.applied_codes() == ["first()"], "it kept going after being stopped"


def test_stopping_says_what_is_already_in_the_document():
    stop = {"now": False}
    events = EventStream()
    events.subscribe(lambda e: stop.update(now=True) if e.kind == PLAN else None)

    run("Make a box", llm=ScriptedLLMClient([plan_reply("First")]),
        tools=FakeTools().registry, events=events, cancelled=lambda: stop["now"])

    blocked = events.first(BLOCKED)
    assert "still in the Document" in blocked.data["reason"]


def test_a_run_that_is_never_stopped_is_unaffected():
    result, _, _, tools = drive([
        plan_reply("Only"), code_reply("box()"), verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["box()"]


# ── The loop is a function of (Intent, context) ───────────────────────────────


def test_the_loop_takes_everything_it_needs_as_arguments():
    """A narrower Intent is all a future sub-agent needs."""
    import inspect

    signature = inspect.signature(run)

    assert list(signature.parameters)[0] == "intent"
    for injected in ("llm", "tools", "events", "budgets"):
        assert injected in signature.parameters


def test_two_loops_can_run_over_different_dependencies_without_touching_each_other():
    first = drive([plan_reply("A"), code_reply("a()"), verdict_reply("pass")],
                  intent="First intent")
    second = drive([plan_reply("B"), code_reply("b()"), verdict_reply("pass")],
                   intent="Second intent")

    assert first[3].applied_codes() == ["a()"]
    assert second[3].applied_codes() == ["b()"]
    assert first[1] is not second[1]


def test_a_run_reports_what_it_did():
    result, _, _, _ = drive([
        plan_reply("First", "Second"),
        code_reply("a()", "b()"),
        verdict_reply("pass", "Both done."),
    ])

    assert result.ok
    assert result.steps_applied == 2
    assert result.verdict == "pass"


# ── Plan asks rather than inventing a parameter (FCC-25) ──────────────────────

GEAR = "модельку шестерні"
MISSING = "I need the module, the tooth count, the thickness and the bore diameter."


def test_plan_can_ask_instead_of_planning():
    """
    The in-process half of FCC-25: with a value the part cannot be built
    without missing, Plan answers with a question and no Plan is shown.
    """
    asked = []

    result, events, _, tools = drive(
        [question_reply(MISSING), design_reply(GEAR_AS_TOLD),
         plan_reply("Sketch the blank", "Pad it"), code_reply("a()", "b()"),
         assertion_reply("assert True"), verdict_reply("pass")],
        intent=GEAR, ask=lambda question: asked.append(question) or "module 2, 20 teeth")

    assert asked == [MISSING], "Plan did not ask, or asked something else: %s" % asked
    question = events.first(QUESTION)
    assert question is not None and question.data["text"] == MISSING
    # The question came before any Plan reached the user.
    kinds = [e.kind for e in events]
    assert kinds.index(QUESTION) < kinds.index(PLAN)
    assert result.ok
    assert tools.applied_codes() == ["a()", "b()"]


GEAR_AS_TOLD = {"features": [{"id": "teeth", "kind": "20 teeth of module 2",
                              "position": {"the blank axis": {"value": "20 teeth",
                                                              "flag": "assumed"}}}]}


def test_the_users_answer_is_written_into_the_description_and_not_handed_to_plan():
    """
    A person's answer goes back through Design and lands in the same model by
    the same merge, so what Plan is handed next is the description grown — never
    an answer standing beside it, which is the arrangement that lost the 100 mm.
    The user supplied it, so it is `assumed`. FCC-25, FCC-93.
    """
    llm = ScriptedLLMClient([
        question_reply(MISSING),
        design_reply(GEAR_AS_TOLD),
        plan_reply("Sketch a 20-tooth module-2 blank"), code_reply("a()"),
        verdict_reply("pass"),
    ])
    tools = FakeTools()

    run(GEAR, llm=llm, tools=tools.registry, ask=lambda _: "module 2, 20 teeth, 10 thick, ⌀8 bore")

    # The answer went to Design first, with the question it answers.
    told = json.loads(llm.calls[1][-1]["content"][0]["text"])
    assert told["the_question"] == MISSING
    assert told["the_answer_you_were_given"] == "module 2, 20 teeth, 10 thick, ⌀8 bore"

    described = json.loads(llm.calls[2][-1]["content"])
    assert "20 teeth (assumed)" in described["what_the_part_is"]
    assert "you_asked_and_were_answered" not in described


def test_a_user_who_declines_gets_a_plan_that_chose_for_them():
    result, events, llm, _ = drive(
        [question_reply(MISSING),
         plan_reply("Sketch the blank, assumed module 2 and 20 teeth"), code_reply("a()"),
         verdict_reply("pass")],
        intent=GEAR, ask=lambda _: "")

    assert result.ok
    told = json.dumps(llm.calls[1])
    assert "Choose sensible values yourself" in told
    assert events.first(ANSWER).data["by"] == "nobody"


def test_with_nobody_to_ask_the_run_does_not_stall():
    """Level 1, Level 2 and headless runs have no user; they must still plan."""
    result, _, llm, _ = drive(
        [question_reply(MISSING), plan_reply("Sketch it, assumed module 2"), code_reply("a()"),
         verdict_reply("pass")],
        intent=GEAR)

    assert result.ok
    assert "Choose sensible values yourself" in json.dumps(llm.calls[1])


def test_a_plan_that_only_ever_asks_is_bounded():
    """
    The user is asked at most `questions` times, and at most `questions` may
    stand unanswered. Past that the run stops rather than looping — as a block
    naming what to answer, not an error (FCC-96).
    """
    asked = []

    result, _, _, tools = drive(
        [question_reply("q1"), design_reply(),
         question_reply("q2"), design_reply(),
         question_reply("q3"), question_reply("q4"), question_reply("q5")],
        intent=GEAR, ask=lambda q: asked.append(q) or "some answer",
        budgets=Budgets(questions=2))

    assert asked == ["q1", "q2"], "the user was asked past the budget: %s" % asked
    assert result.blocked
    assert "q3; q4; q5" in result.reason, "the block did not say what to answer"
    assert tools.applied_codes() == []


# ── the question goes back to the description, never to the user (FCC-90) ─────

THROUGH_OR_NOT = ("Does the hourglass pocket go through the 150 mm length, or is it "
                  "a set depth? And which edges take the R2 fillet?")
OFF_THE_SECTION = {"features": [
    {"id": "pocket-front", "kind": "hourglass pocket", "topology": "blind",
     "position": {"the front face": {"value": "20 mm deep", "flag": "read"}},
     "seen_in": ["SECTION A-A"]},
]}


def test_a_question_about_the_drawing_goes_to_the_sheet_before_the_user():
    """
    The run asked the user whether the pockets go through and which edges take
    R2. SECTION A-A shows the depth, hatched, and the R2 edges are drawn in red
    in every view — the user attached the sheet so they would not have to answer
    what it shows. The first pass missed it, which is what a first pass does;
    the route is what has to stop the miss becoming a question. FCC-90.
    """
    asked = []

    result, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply(THROUGH_OR_NOT),
        design_reply(OFF_THE_SECTION),
        plan_reply("Pocket both faces 20 mm",
                   satisfies=everything(A_PART, OFF_THE_SECTION)),
        code_reply("a()"), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], ask=lambda q: asked.append(q) or "")

    assert result.ok
    assert asked == [], "the user was asked what the drawing answers"
    answered = events.first(ANSWER)
    assert answered.data["by"] == "drawing"
    assert "20 mm deep (read)" in answered.data["text"]

    # The re-read was given the picture and the description it already wrote.
    re_read = llm.calls[2][-1]["content"]
    assert [b for b in re_read if b.get("type") == "image_url"], "it re-read without the sheet"
    asked_again = json.loads(re_read[0]["text"])
    assert asked_again["the_question"] == THROUGH_OR_NOT
    assert asked_again["the_part_as_you_described_it"] == A_PART

    # And Plan was handed the description grown, not an answer beside it.
    to_plan = json.loads(llm.calls[3][-1]["content"])
    assert "- pocket-front" in to_plan["what_the_part_is"]
    assert "the front face: 20 mm deep (read)" in to_plan["what_the_part_is"]
    assert "you_asked_and_were_answered" not in to_plan


def test_a_value_already_described_is_not_overwritten_by_a_later_pass():
    """
    The run read the left bore axis 20 mm from the left end face and then 25 mm,
    both `read`, and built 25. 20 is still what the Gate shows, and the
    disagreement is visible rather than absent — the merge is enforced by `id`
    in the loop, not only asked for in the prompt. FCC-93.
    """
    _, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply("Where is the Ø8 hole?"),
        design_reply({"features": [{"id": "hole-8-left",
                                    "position": {"the left end face": read("25 mm")}}]}),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    grown = events.last(DESIGN).data["model"]
    assert grown["features"][0]["position"]["the left end face"] == read("15 mm")
    assert grown["conflicts"][0]["was_also_said"] == read("25 mm")

    shown = json.loads(llm.calls[3][-1]["content"])["what_the_part_is"]
    assert "the left end face: 15 mm (read)" in shown
    assert "In conflict" in shown, "the disagreement never reached the Plan Gate"


def test_a_question_the_sheet_does_not_answer_still_reaches_the_user():
    """
    A tolerance nobody printed is not on the sheet, and the person is asked.

    The sheet saying nothing is on the stream too. Without it a stalled run
    cannot be told from one where the drawing was never looked at, which is the
    first thing anybody reading the log asks (FCC-96).
    """
    asked = []

    result, events, _, _ = drive([
        design_reply(A_PART),
        question_reply("What general tolerance applies?"),
        design_reply(),                         # not on the sheet
        design_reply({"not_defined": [{"what": "the general tolerance",
                                       "looked_in": ["the notes"]}]}),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG],
        ask=lambda q: asked.append(q) or "±0.1 mm")

    assert result.ok
    assert asked == ["What general tolerance applies?"]
    assert [answer.data["by"] for answer in events.of(ANSWER)] == ["drawing", "user"]
    assert "nothing in the views" in events.first(ANSWER).data["text"]


def test_the_drawing_answering_does_not_spend_the_users_question_budget():
    """
    `budgets.questions` bounds how much a person is asked, and the drawing
    answering is not a person being asked. FCC-90.
    """
    asked = []

    result, _, _, _ = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply({"datums": [{"name": "d1"}]}),
        question_reply("q2"), design_reply({"datums": [{"name": "d2"}]}),
        question_reply("q3"), design_reply(),          # not on the sheet
        design_reply({"datums": [{"name": "d3"}]}),    # the person's answer, written in
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(questions=2),
        ask=lambda q: asked.append(q) or "an answer")

    assert result.ok
    assert asked == ["q3"], "two drawing answers ate the user's budget: %s" % asked


def test_the_re_reads_have_a_ceiling_of_their_own():
    """
    A Plan that keeps asking and a drawing that keeps answering would never
    terminate. Past the ceiling the question reaches the user as it did before.
    """
    asked = []

    drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply({"datums": [{"name": "d1"}]}),
        question_reply("q2"),
        design_reply({"datums": [{"name": "d2"}]}),    # the person's answer, written in
        plan_reply("Sketch it"), code_reply("a()"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(rereads=1),
        ask=lambda q: asked.append(q) or "an answer")

    assert asked == ["q2"], "the second question was put to the sheet past the ceiling"


# ── the questioning belongs to the run, not to one Plan (FCC-96) ─────────────


def test_a_question_raised_after_being_told_to_choose_still_reaches_the_sheet():
    """
    The run that stalled on Model Mania 2025 asked three times. The third
    question was the good one — it had spotted that its own outer profile could
    not be 50 mm tall with 30 mm end rounds — and it never reached the drawing,
    because the loop stopped one line above the re-read. A question raised after
    Plan was told to choose is a *new* obstacle, and the sheet answers it as
    often as any other. FCC-96.
    """
    asked = []
    off_the_sheet = {"datums": [{"name": "the front-to-rear centre plane"}]}

    result, events, llm, _ = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply(),          # not on the sheet
        question_reply("q2"), design_reply(off_the_sheet),
        plan_reply("Sketch it", satisfies=everything(A_PART)), code_reply("a()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG], budgets=Budgets(questions=1),
        ask=lambda q: asked.append(q) or "")

    assert result.ok, "the second question killed the run: %s" % result.reason
    assert asked == ["q1"]
    assert [answer.data["by"] for answer in events.of(ANSWER)] \
        == ["drawing", "nobody", "drawing"]
    re_read = llm.calls[4][-1]["content"]
    assert json.loads(re_read[0]["text"])["the_question"] == "q2"
    assert [b for b in re_read if b.get("type") == "image_url"], "it re-read without the sheet"


def test_the_same_question_twice_is_a_block_naming_it_and_not_an_error():
    """
    Nothing has changed since the last time it was asked, so asking again
    cannot end. That is a stop with the question in it, not an error nobody can
    act on. FCC-96.
    """
    result, events, _, tools = drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply(),          # not on the sheet
        question_reply("q1"),
    ], attachments=[RENDER_PNG], ask=lambda _: "")

    assert result.blocked
    assert "q1" in result.reason
    assert events.of(ERROR) == []
    assert tools.applied_codes() == []


def test_the_questions_nobody_answered_are_shown_beside_the_plan():
    """
    A Plan told to choose for itself carries values nobody supplied, and the
    Gate is the only place they are correctable — so the Gate is handed the
    questions, not only the Steps. FCC-96.
    """
    _, events, _, _ = drive(
        [question_reply("What module?"), plan_reply("Sketch it"), code_reply("a()"),
         verdict_reply("pass")],
        intent=GEAR, ask=lambda _: "")

    assert events.first(PLAN).data["unanswered"] == ["What module?"]


def test_a_replan_does_not_refill_the_re_read_ceiling():
    """
    The counters used to be locals of one Plan, so every re-plan started the
    questioning over: the same question went to the user twice and the sheet
    could be re-read without end. FCC-96.
    """
    asked = []

    drive([
        design_reply(A_PART),
        question_reply("q1"), design_reply({"datums": [{"name": "d1"}]}),
        plan_reply("Impossible", satisfies=everything(A_PART)), code_reply("nope()"),
        question_reply("q2"),                          # the re-plan asks too
        plan_reply("Possible", satisfies=everything(A_PART)), code_reply("yes()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], applies=[failed("one")], attachments=[RENDER_PNG],
        budgets=Budgets(rereads=1, attempts_per_step=1),
        ask=lambda q: asked.append(q) or "an answer")

    assert asked == ["q2"], "the re-plan refilled the re-read ceiling: %s" % asked


def test_with_no_attachment_a_question_goes_straight_to_the_user():
    """
    No sheet to ask, so no turn is spent asking one. The answer still comes back
    through Design, because merge is the only way the description grows. FCC-93.
    """
    asked = []

    _, _, llm, _ = drive(
        [question_reply(MISSING), design_reply(GEAR_AS_TOLD), plan_reply("Sketch it"),
         code_reply("a()"), assertion_reply("assert True"), verdict_reply("pass")],
        intent=GEAR, ask=lambda q: asked.append(q) or "module 2")

    assert asked == [MISSING]
    assert len(llm.calls) == 6, "a turn was spent re-reading a drawing that is not there"
    # The one Design turn there was had no picture with it.
    written_in = llm.calls[1][-1]["content"]
    assert len(written_in) == 1 and written_in[0]["type"] == "text"


def test_a_question_alongside_steps_is_still_a_plan():
    """A question is not a Plan with a caveat; steps win and nothing is asked."""
    asked = []

    result, events, _, _ = drive(
        [json.dumps({"question": "how big?", "steps": ["Sketch it"]}),
         code_reply("a()"), verdict_reply("pass")],
        intent=GEAR, ask=lambda q: asked.append(q) or "")

    assert result.ok
    assert asked == []
    assert events.first(QUESTION) is None


# ── a re-plan is a Plan: it may ask, and never comes back empty (FCC-67) ──────


def test_a_replan_after_a_handback_asks_rather_than_planning_blind():
    """
    A re-plan that needs a value nobody gave it used to answer with a question
    and no Steps, and the loop took `planned.steps` blind: the question was
    dropped and the revised Plan was empty. FCC-67.
    """
    asked = []

    result, events, llm, _ = drive(
        [
            plan_reply("Impossible"),
            code_reply("nope1()"), code_reply("nope2()"), code_reply("nope3()"),
            question_reply(MISSING), design_reply(GEAR_AS_TOLD),
            plan_reply("Possible"),
            code_reply("yes()"),
            assertion_reply("assert True"),
            verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
        ask=lambda question: asked.append(question) or "module 2, 20 teeth",
    )

    assert asked == [MISSING], "the re-plan planned without asking: %s" % asked
    assert result.ok
    assert events.first(PLAN_REVISED).data["steps"] == ["Possible"]
    # The answer reached the revised Plan, through the description it grew.
    revised = json.loads(llm.calls[6][-1]["content"])
    assert "20 teeth (assumed)" in revised["what_the_part_is"]


def test_a_replan_after_a_rejected_verdict_asks_too():
    asked = []

    result, _, llm, _ = drive(
        [
            plan_reply("Make it"), code_reply("make()"), verdict_reply("fail", "20mm, not 10mm"),
            question_reply(MISSING), design_reply(GEAR_AS_TOLD),
            plan_reply("Make it 10mm"), code_reply("fix()"),
            assertion_reply("assert True"), verdict_reply("pass"),
        ],
        ask=lambda question: asked.append(question) or "10mm across the flats",
    )

    assert asked == [MISSING]
    assert result.ok
    assert "10mm across the flats" in json.dumps(llm.calls[4])
    assert "20 teeth (assumed)" in json.loads(llm.calls[5][-1]["content"])["what_the_part_is"]


def test_a_replan_that_only_ever_asks_stops_the_run_rather_than_looping():
    asked = []

    result, _, _, _ = drive(
        [
            plan_reply("Impossible"), code_reply("nope1()"),
            question_reply("q1"), design_reply(),
            question_reply("q2"), question_reply("q3"),
        ],
        applies=[failed("one")],
        budgets=Budgets(attempts_per_step=1, questions=1),
        ask=lambda question: asked.append(question) or "some answer",
    )

    assert asked == ["q1"], "the user was asked past the budget: %s" % asked
    assert result.blocked
    assert "q2; q3" in result.reason, "the block did not say what to answer"


def test_no_plan_without_steps_ever_reaches_execute(monkeypatch):
    """
    Unreachable through `plan()`, which either has Steps, or a question, or
    raises. The guard is the cheap insurance that no future path sends Review
    over a Document nothing changed. FCC-67.
    """
    from core import loop as loop_module

    monkeypatch.setattr(loop_module, "plan",
                        lambda *args, **kwargs: loop_module.Plan(steps=[]))

    result, _, _, tools = drive([plan_reply("never asked for")])

    assert result.blocked
    assert "no Steps" in result.reason
    assert tools.applied_codes() == []


# ── the Assertion phase ───────────────────────────────────────────────────────


# A nut described, and the Acceptance derived from it. The list is no longer
# written by Plan, so a run that has something to check is a run that had
# something described (FCC-94).
A_NUT = {"envelope": {"across the flats": read("13.0 mm"), "height": read("6.5 mm")}}

ACCEPTANCE = acceptance_of(A_NUT)


def a_run_over_the_nut(*turns, **kwargs):
    """The nut described, then whatever the test scripts after it."""
    return drive([design_reply(A_NUT)] + list(turns),
                 attachments=[RENDER_PNG], **kwargs)


def planning_the_nut(*steps):
    return plan_reply(*steps, satisfies=everything(A_NUT))

CHECKS = ("flats = [f for f in doc.Objects[0].Shape.Faces]\n"
          "assert abs(13.0 - across(flats)) < 1e-6, '13.0 mm across the flats'\n")


def assertion_failed(traceback):
    return {"ok": False, "traceback": traceback}


ASSERT_FAILED = ('Traceback (most recent call last):\n'
                 '  File "<step>", line 2, in <module>\n'
                 "AssertionError: 13.0 mm across the flats\n")

CHECK_IS_BROKEN = ('Traceback (most recent call last):\n'
                   '  File "<step>", line 1, in <module>\n'
                   "AttributeError: 'Part::Feature' object has no attribute 'Flats'\n")


def test_the_assertion_phase_runs_after_the_last_step_and_before_review():
    """
    Once per run, on the finished Document: what is checked is how close the
    built part is to the goal, not the arithmetic of a state that may not be
    ready yet. Read-only, so it goes through a Tool of its own rather than
    through apply. FCC-76.
    """
    _, events, llm, tools = a_run_over_the_nut(
        planning_the_nut("Sketch it", "Pad it"),
        code_reply("sketch()", "pad()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
    )

    assert [name for name, _ in tools.calls
            if name in ("apply_step", "check_assertions")] == [
        "apply_step", "apply_step", "check_assertions"]
    checked = next(args for name, args in tools.calls if name == "check_assertions")
    assert checked["code"] == CHECKS.strip(), "the Tool ran something other than the checks"

    kinds = [e.kind for e in events]
    assert kinds.index(ASSERTION) < kinds.index(REVIEW_VERDICT)
    assert events.first(ASSERTION).data["ok"] is True


def test_an_empty_acceptance_list_costs_no_llm_call():
    """
    Nothing to check means no phase at all — not a phase that checks nothing.
    The run reaches Review exactly as it did before this ticket. FCC-76.
    """
    result, events, llm, tools = drive([
        plan_reply("Add a box"),
        code_reply("box()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert len(llm.calls) == 3, "an extra provider call on a Plan with nothing to check"
    assert events.first(ASSERTION) is None
    assert not any(name == "check_assertions" for name, _ in tools.calls)


def test_failed_assertions_return_to_plan_and_never_to_execute():
    """
    A failed check is a rejected result: revision, feedback, a new Plan and a
    new Plan Gate. The traceback is what Plan is told, and Execute is told
    none of it — the mirror of a failed verdict. FCC-76.
    """
    _, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        assertion_reply(CHECKS),
        planning_the_nut("Make it 13 mm across the flats"),
        code_reply("fix()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[assertion_failed(ASSERT_FAILED), None])

    to_plan = json.dumps(llm.calls[4])
    to_execute = json.dumps(llm.calls[5])

    assert "13.0 mm across the flats" in to_plan
    assert "AssertionError" in to_plan
    assert "AssertionError" not in to_execute, "the failed check reached Execute"
    assert events.first(PLAN_REVISED).data["steps"][0].startswith(
        "Make it 13 mm across the flats")
    assert events.first(ASSERTION).data["ok"] is False


def test_a_run_out_of_revisions_names_the_assertions_not_review():
    """
    FCC-22's separate accounting, extended: a run stopped by failed checks must
    not tell the user Review kept rejecting the result. FCC-76.
    """
    result, _, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS)] * 5,
        budgets=Budgets(revisions=2),
        checks=[assertion_failed(ASSERT_FAILED)] * 5,
    )

    assert result.blocked
    assert "acceptance checks failed" in result.reason
    assert "Review rejected" not in result.reason
    assert "you amended" not in result.reason


def test_review_is_told_the_facts_the_checks_confirmed():
    """
    Measured facts beside the four views, so Review judges what the checks did
    not cover rather than re-judging what they did. The views do not go
    (ADR-0015). FCC-76.
    """
    _, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        assertion_reply(CHECKS, uncovered=[ACCEPTANCE[1]]),
        verdict_reply("pass"),
        views=[RENDER_PNG] * 4)

    review_request = json.loads(llm.calls[-1][1]["content"][0]["text"].split("\n\n")[0])

    assert review_request["facts_the_checks_confirmed"] == [ACCEPTANCE[0]]
    assert review_request["what_the_checks_could_not_cover"] == [ACCEPTANCE[1]]
    # And it is visible as uncovered rather than dropped or asserted as passing.
    assert events.first(ASSERTION).data["uncovered"] == [ACCEPTANCE[1]]


def test_a_broken_check_is_rewritten_and_never_reaches_plan_as_geometry():
    """
    An AttributeError in the checking code says nothing about the nut. Read as
    a failed model it would send Plan to rebuild a part that is already right,
    so it is handed back to the phase that wrote it, on the same attempts
    budget Execute uses. FCC-76.
    """
    result, events, llm, tools = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        assertion_reply("bad_check()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
        checks=[assertion_failed(CHECK_IS_BROKEN), None])

    assert result.ok
    assert events.first(PLAN_REVISED) is None, "a broken check sent Plan to rebuild"
    assert len([c for c in tools.calls if c[0] == "check_assertions"]) == 2
    # The traceback came back to the phase that wrote the check, not to Plan.
    assert "AttributeError" in json.dumps(llm.calls[4])


def test_a_check_that_cannot_be_written_lets_review_judge_alone():
    """
    Attempts spent on a check that never runs is not a reason to rebuild a good
    model, and not a reason to lose a finished run either: the phase gives up,
    says so, and everything it failed to cover goes to Review. FCC-76.
    """
    result, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"),
        code_reply("make()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        verdict_reply("pass"),
        checks=[assertion_failed(CHECK_IS_BROKEN)] * 3)

    assert result.ok
    assert events.first(PLAN_REVISED) is None
    assert events.first(ASSERTION).data["ok"] is None, "an unwritable check is not a verdict"


def test_stop_during_the_checks_does_not_wait_out_three_provider_calls():
    """
    Stop is a decision, not a request. Three attempts at writing the checks is
    long enough that a run which ignored it there would feel hung. FCC-76.
    """
    llm = ScriptedLLMClient([
        design_reply(A_NUT),
        planning_the_nut("Make it"),
        code_reply("make()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        verdict_reply("pass"),
    ])
    tools = FakeTools(checks=[assertion_failed(CHECK_IS_BROKEN)] * 3)
    events = EventStream()

    def cancelled():
        # Runs clean until the checks start; then the user presses Stop.
        return any(name == "check_assertions" for name, _ in tools.calls)

    result = run("Make a nut", llm=llm, tools=tools.registry, events=events,
                 cancelled=cancelled, attachments=[RENDER_PNG])

    assert result.blocked
    assert "Stopped by you" in result.reason
    assert len([c for c in tools.calls if c[0] == "check_assertions"]) == 1


# ── the Plan consumes the description (FCC-94) ────────────────────────────────


# One physical cavity, seen in three views and reconciled into one feature by
# the phase that owns the description. Steps 4, 5 and 6 of the recorded run cut
# it three times, once per view.
THE_CAVITY = {
    "envelope": {"depth front to rear": read("100 mm")},
    "features": [{"id": "central-opening", "kind": "hourglass pocket",
                  "topology": "two opposed blind pockets, 10 mm web",
                  "position": {"the front face": read("20 mm")},
                  "seen_in": ["front view", "SECTION A-A", "SECTION B-B"]}],
}

CAVITY_DEPTH = "envelope → depth front to rear"
CAVITY_POCKET = "features → central-opening → position → the front face"


def test_the_acceptance_is_the_descriptions_own_entries_and_not_plans_paraphrase():
    """
    Between the sheet and the assert there were two rewrites, and in the run
    that named this defect both lied: every entry claimed `(read)` although the
    values came out of a re-read, and the depth entry carried 50 mm, so
    Assertion would have measured 50 mm on the built part and reported a pass on
    a part the drawing never showed. Derived, there is nothing to rewrite.
    FCC-94.
    """
    _, events, llm, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply("Pad the blank", "Cut the central opening",
                   satisfies=everything(THE_CAVITY)),
        code_reply("pad()", "cut()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    shown = events.first(PLAN).data["acceptance"]
    assert shown == ["envelope → depth front to rear: 100 mm (read)",
                     "features → central-opening → position → the front face: 20 mm (read)"]
    # And the checks are written from that same list, not from a Plan's version.
    assert _turns_of(llm, "assertion")[0]["acceptance"] == shown


def test_a_value_the_user_assumed_is_still_marked_assumed_in_the_acceptance():
    """
    The kind of value most likely to be wrong is the one somebody settled, so it
    is a criterion and it says at the Gate that it is theirs to correct. FCC-94.
    """
    told = {"envelope": {"length": {"value": "150 mm", "flag": "assumed"}}}

    _, events, _, _ = drive([
        design_reply(told),
        plan_reply("Pad it 150 mm"), code_reply("pad()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN).data["acceptance"] == ["envelope → length: 150 mm (assumed)"]


def test_a_step_says_at_the_gate_which_part_of_the_part_it_is_for():
    """
    A reader who cannot tell from the Gate which entry a Step is for cannot tell
    that one cavity has been planned three times, which is what Steps 4, 5 and 6
    of the recorded run were. FCC-94.
    """
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank 100 mm front to rear",
                    "satisfies": [CAVITY_DEPTH]},
                   {"do": "Cut the central opening, both faces",
                    "satisfies": [CAVITY_POCKET]}),
        code_reply("pad()", "cut()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN).data["steps"] == [
        "Pad the blank 100 mm front to rear (satisfies: envelope → depth front to rear)",
        "Cut the central opening, both faces (satisfies: features → central-opening → "
        "position → the front face)",
    ]


def test_a_plan_that_leaves_a_read_entry_unconsumed_is_planned_again():
    """
    The headline defect: the revised Plan dropped the 100 mm front to rear and
    no rule, prompt or judgement noticed. A set difference over addresses
    notices, and the run says which entry went missing. FCC-94.
    """
    result, events, llm, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Cut the central opening", "satisfies": [CAVITY_POCKET]}),
        plan_reply({"do": "Pad the blank 100 mm front to rear",
                    "satisfies": [CAVITY_DEPTH, CAVITY_POCKET]}),
        code_reply("pad()"), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok
    revised = events.first(PLAN_REVISED)
    assert revised is not None, "a Plan that dropped the depth reached the user as final"
    assert CAVITY_DEPTH in revised.data["reason"]

    # And Plan was told which entry, verbatim, so it can cite it back.
    told = json.loads(llm.calls[2][-1]["content"])["what_went_wrong_last_time"]
    assert CAVITY_DEPTH in told
    assert CAVITY_POCKET not in told, "an entry that was cited was reported as missing"


def test_a_plan_that_leaves_nothing_unconsumed_is_never_replanned():
    """Coverage, not exclusivity: one Step may satisfy several entries."""
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank and cut the opening",
                    "satisfies": [CAVITY_DEPTH, CAVITY_POCKET]}),
        code_reply("build()"), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN_REVISED) is None


def test_a_plan_citing_the_whole_line_it_was_handed_has_cited_the_entry():
    """
    `what_the_plan_must_satisfy` gives the address and the value on one line,
    and a Plan copying that line means the entry the line is. Refusing it would
    make the check fail on the one Plan that did exactly as it was told, and
    every run would replan to a block. FCC-94.
    """
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank and cut the opening",
                    "satisfies": [CAVITY_DEPTH + ": 100 mm (read)",
                                  CAVITY_POCKET + ": 20 mm (read)"]}),
        code_reply("build()"), assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN_REVISED) is None


def test_one_entry_may_feed_several_steps():
    """
    An overall height feeds both the base solid and a through hole's depth, and
    citing it twice is right. What may not happen is an entry feeding no Step.
    FCC-94.
    """
    _, events, _, _ = drive([
        design_reply(THE_CAVITY),
        plan_reply({"do": "Pad the blank 100 mm front to rear",
                    "satisfies": [CAVITY_DEPTH]},
                   {"do": "Cut the opening 100 mm deep, front to rear",
                    "satisfies": [CAVITY_DEPTH, CAVITY_POCKET]}),
        code_reply("pad()", "cut()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert events.first(PLAN_REVISED) is None


def test_an_assumed_entry_is_not_something_a_plan_can_be_refused_over():
    """
    A Plan cannot be refused for failing to build a number nobody supplied. The
    entry is still checked; it is not a coverage duty. FCC-94.
    """
    told = {"envelope": {"length": {"value": "150 mm", "flag": "assumed"}}}

    result, events, _, _ = drive([
        design_reply(told),
        plan_reply("Pad it"), code_reply("pad()"),
        assertion_reply("assert True"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert result.ok
    assert events.first(PLAN_REVISED) is None


def test_a_plan_that_never_learns_to_cite_stops_the_run_and_says_why():
    """
    The coverage re-plan spends the same shared ceiling as every other cause, so
    a prompt regression stops the run with a reason instead of looping — and
    FCC-22's separate accounting names the right cause. FCC-94.
    """
    result, _, _, tools = drive(
        [design_reply(THE_CAVITY)] + [plan_reply("Cut it")] * 5,
        attachments=[RENDER_PNG], budgets=Budgets(revisions=2),
    )

    assert result.blocked
    assert "left part of the description unplanned" in result.reason
    assert "Review rejected" not in result.reason
    assert tools.applied_codes() == [], "a Plan that dropped an entry built something"


def test_a_run_with_no_attachment_still_plans_and_still_shows_an_acceptance():
    """
    With no drawing the description holds only what the user's answers supplied,
    and where it holds nothing at all an empty Acceptance means no Assertion
    phase rather than a phase that checks nothing — the behaviour from before
    there was a list. FCC-94.
    """
    told = {"envelope": {"length": {"value": "150 mm", "flag": "assumed"}}}

    result, events, _, tools = drive(
        [question_reply(MISSING), design_reply(told),
         plan_reply("Pad it 150 mm"), code_reply("pad()"),
         assertion_reply("assert True"), verdict_reply("pass")],
        ask=lambda _: "150 mm long")

    assert result.ok
    assert events.first(PLAN).data["acceptance"] == ["envelope → length: 150 mm (assumed)"]
    assert any(name == "check_assertions" for name, _ in tools.calls)


# ── what a failed run leaves behind (FCC-98) ──────────────────────────────────
#
# A recorded run finished eight Steps, failed six of its acceptance entries,
# and stopped. Nothing carried that out of the run: the choice the user was
# offered was made in the panel and never reached the transcript, and the next
# drawing was planned against the same Document as though the part were sound.
# The second run then rebuilt on top of six entries that were already wrong.


SIX_FAILED = ('Traceback (most recent call last):\n'
              '  File "<step>", line 156, in <module>\n'
              'AssertionError: these did not hold: envelope \u2192 across the flats: '
              '13.0 mm (read); envelope \u2192 height: 6.5 mm (read)\n')


def test_the_entries_that_did_not_hold_come_out_on_the_result():
    result, _, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS)] * 5,
        budgets=Budgets(revisions=1),
        checks=[assertion_failed(SIX_FAILED)] * 5,
    )

    assert result.unmet == ["envelope \u2192 across the flats: 13.0 mm (read)",
                            "envelope \u2192 height: 6.5 mm (read)"]


def test_a_result_the_user_took_as_it_is_still_says_what_is_wrong_with_it():
    """
    ACCEPT ends the run as a pass. It does not make the part right, and the
    Document the next Intent starts from still has both entries wrong in it.
    """
    from core.loop import ACCEPT

    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        checks=[assertion_failed(SIX_FAILED)],
        decide=lambda failure: ACCEPT,
    )

    assert result.ok, "the user took it, so the run passed"
    assert result.unmet == ["envelope \u2192 across the flats: 13.0 mm (read)",
                            "envelope \u2192 height: 6.5 mm (read)"]


def test_a_run_whose_checks_passed_leaves_nothing_outstanding():
    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        verdict_reply("pass"))

    assert result.ok
    assert result.unmet == []


def test_a_run_stopped_partway_still_reports_what_was_wrong():
    """
    Every way out of the loop carries it, not only the tidy one — a run that
    ran out of revisions is exactly the run whose Document nobody fixed.
    """
    result, _, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS)] * 5,
        budgets=Budgets(revisions=1),
        checks=[assertion_failed(SIX_FAILED)] * 5,
    )

    assert result.blocked
    assert result.unmet


def test_the_assertion_event_names_the_entries_and_not_only_the_traceback():
    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        checks=[assertion_failed(SIX_FAILED)],
        decide=lambda failure: "accept",
    )

    assert events.first(ASSERTION).data["unmet"] == [
        "envelope \u2192 across the flats: 13.0 mm (read)",
        "envelope \u2192 height: 6.5 mm (read)"]


def test_a_document_that_cannot_recompute_leaves_the_whole_list_outstanding():
    """Nothing was measured, so nothing can be named, and nothing is assumed right."""
    broken = ("Traceback (most recent call last):\n"
              "RuntimeError: recompute left these objects in error:\n"
              "Pocket (PartDesign::Pocket): Links go out of the allowed scope\n")

    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        checks=[assertion_failed(broken)],
        decide=lambda failure: "accept",
    )

    assert result.unmet == ACCEPTANCE


# ── the choice the user is offered is part of the transcript ──────────────────


def test_what_the_user_decided_about_a_failed_result_is_on_the_stream():
    decided = []

    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        checks=[assertion_failed(SIX_FAILED)],
        decide=lambda failure: decided.append(failure) or "accept",
    )

    assert decided, "the user was never asked"
    question = events.of(QUESTION)[-1]
    answer = events.of(ANSWER)[-1]
    assert "what to do about it is yours to say" in question.data["text"]
    assert "did not hold" in question.data["text"]
    assert answer.data["text"] == "You took the result as it is."
    assert answer.data["by"] == "user"


def test_starting_over_is_recorded_as_the_licence_it_is():
    from core.loop import START_OVER

    _, events, _, _ = a_run_over_the_nut(
        *[planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS)] * 5,
        budgets=Budgets(revisions=1),
        checks=[assertion_failed(SIX_FAILED)] * 5,
        decide=lambda failure: START_OVER,
    )

    assert "delete what was built" in events.of(ANSWER)[-1].data["text"]


def test_nothing_is_asked_when_the_run_applied_nothing_to_lose():
    asked = []

    a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), code_reply("make()"),
        code_reply("make()"),
        applies=[failed("one"), failed("two"), failed("three")],
        checks=[assertion_failed(SIX_FAILED)],
        decide=lambda failure: asked.append(failure) or "accept",
    )

    assert asked == []


# ── the next run inherits it ──────────────────────────────────────────────────


STILL_WRONG = ["envelope \u2192 across the flats: 13.0 mm (read)"]


def test_a_run_that_inherits_a_failure_says_so_before_it_reads_the_drawing():
    from core.events import OUTSTANDING

    _, events, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        verdict_reply("pass"),
        outstanding=STILL_WRONG)

    assert events.first(OUTSTANDING).data["entries"] == STILL_WRONG
    assert list(events)[0].kind == OUTSTANDING, \
        "what is already wrong turned up after the run had started planning"


def test_the_first_plan_of_an_inheriting_run_is_told_what_is_wrong():
    _, _, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        verdict_reply("pass"),
        outstanding=STILL_WRONG)

    to_plan = json.dumps(llm.calls[1])

    assert "acceptance checks did not pass" in to_plan
    assert "13.0 mm" in to_plan
    assert "Do not plan around it" in to_plan


def test_an_inherited_failure_is_checked_again_at_the_end_of_the_new_run():
    """
    The second drawing's acceptance list describes the part it wants and knows
    nothing of what the run before it got wrong. Adding the entries is what
    makes the checks an answer about the Document rather than about the changes.
    """
    inherited = ["features \u2192 bore-left \u2192 position: 25 mm (read)"]

    _, _, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        verdict_reply("pass"),
        outstanding=inherited)

    checked = json.loads(llm.calls[3][-1]["content"])

    assert checked["acceptance"] == ACCEPTANCE + inherited


def test_an_inherited_entry_the_new_run_already_lists_is_not_listed_twice():
    _, _, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        verdict_reply("pass"),
        outstanding=[ACCEPTANCE[0]])

    assert json.loads(llm.calls[3][-1]["content"])["acceptance"] == ACCEPTANCE


def test_an_inherited_failure_that_now_passes_is_no_longer_outstanding():
    result, _, _, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        verdict_reply("pass"),
        outstanding=STILL_WRONG)

    assert result.ok
    assert result.unmet == [], "a passing check did not clear what it inherited"


def test_a_run_that_inherits_nothing_says_nothing_about_it():
    from core.events import OUTSTANDING

    _, events, llm, _ = a_run_over_the_nut(
        planning_the_nut("Make it"), code_reply("make()"), assertion_reply(CHECKS),
        verdict_reply("pass"))

    assert events.first(OUTSTANDING) is None
    assert "acceptance checks did not pass" not in json.dumps(llm.calls[1])


# ── a look this run already took (FCC-99) ─────────────────────────────────────
#
# A look costs a whole provider turn: Execute replies with a question instead
# of code and has to be asked again. A recorded run asked what the same scratch
# object had eight times and what PartDesign::Pocket has five, across the
# regenerations of one Plan, and every one of those answers had already been
# given. So what the run has been told is handed back to Execute before it can
# ask, and an answer it asks for anyway comes out of what it was told.


def test_execute_is_handed_what_the_run_already_looked_at():
    _, _, llm, _ = drive([
        plan_reply("Sketch it", "Pad it"),
        look_reply("describe_type", type_id="PartDesign::Pad"),
        code_reply("sketch()", "pad()"),
        verdict_reply("pass"),
    ])

    # The turn after the look already carries it, which is the turn it was
    # asked in. What matters is the next Execute call after that.
    asked_with = json.loads(llm.calls[2][-1]["content"])

    assert [look["tool"] for look in asked_with["what_you_looked_at"]] == ["describe_type"]


def test_a_regeneration_does_not_have_to_look_again():
    """
    The Step failed, so Execute is asked again — and asked with everything the
    turn before it was told. That is the turn the eight repeats came out of.
    """
    _, _, llm, _ = drive([
        plan_reply("Pad it"),
        look_reply("describe_type", type_id="PartDesign::Pad"),
        code_reply("pad_wrong()"),
        code_reply("pad_right()"),
        verdict_reply("pass"),
    ], applies=[failed("Reversed")])

    regenerated = json.loads(llm.calls[3][-1]["content"])

    assert [look["tool"] for look in regenerated["what_you_looked_at"]] == ["describe_type"]
    assert regenerated["what_you_looked_at"][0]["arguments"] == {"type_id": "PartDesign::Pad"}


def test_asking_twice_for_the_same_thing_calls_the_tool_once():
    _, events, _, tools = drive([
        plan_reply("Pad it"),
        look_reply("describe_type", type_id="PartDesign::Pad"),
        look_reply("describe_type", type_id="PartDesign::Pad"),
        code_reply("pad()"),
        verdict_reply("pass"),
    ], budgets=Budgets(looks_per_turn=2))

    assert len([c for c in tools.calls if c[0] == "describe_type"]) == 1
    # Both are still narrated: a transcript showing the question and not the
    # answer would read as a look that went nowhere.
    told = [e for e in events.of(TOOL_RESULT) if e.data["name"] == "describe_type"]
    assert len(told) == 2
    assert [e.data["already_known"] for e in told] == [False, True]


def test_a_different_argument_is_a_different_look():
    _, _, _, tools = drive([
        plan_reply("Pad it"),
        look_reply("describe_type", type_id="PartDesign::Pad"),
        look_reply("describe_type", type_id="PartDesign::Pocket"),
        code_reply("pad()"),
        verdict_reply("pass"),
    ], budgets=Budgets(looks_per_turn=2))

    assert len([c for c in tools.calls if c[0] == "describe_type"]) == 2


def test_what_an_object_has_is_forgotten_once_a_step_lands():
    """
    A property list read off the Document is a fact about the Document as it
    was. Every applied Step is a chance for it to have moved, so it is not
    carried across one and not answered from memory after one.
    """
    _, _, llm, tools = drive([
        plan_reply("Sketch it", "Pad it"),
        look_reply("describe_object", name="Box"),
        code_reply("sketch()", "pad_wrong()"),
        look_reply("describe_object", name="Box"),
        code_reply("pad_right()"),
        verdict_reply("pass"),
    ], applies=[FakeTools.APPLIED, failed("Reversed")])

    assert len([c for c in tools.calls if c[0] == "describe_object"]) == 2, \
        "an object read before a Step landed was reported as though it still held"
    regenerated = json.loads(llm.calls[3][-1]["content"])
    assert "what_you_looked_at" not in regenerated


def test_what_a_type_has_survives_a_step_because_the_build_does_not_change():
    _, _, llm, tools = drive([
        plan_reply("Sketch it", "Pad it"),
        look_reply("describe_type", type_id="PartDesign::Pad"),
        code_reply("sketch()", "pad_wrong()"),
        code_reply("pad_right()"),
        verdict_reply("pass"),
    ], applies=[FakeTools.APPLIED, failed("Reversed")])

    regenerated = json.loads(llm.calls[3][-1]["content"])

    assert [look["tool"] for look in regenerated["what_you_looked_at"]] == ["describe_type"]


def test_a_look_that_failed_is_not_remembered_as_an_answer():
    """A ToolError is a thing to correct, and correcting it means asking again."""
    _, _, _, tools = drive([
        plan_reply("Pad it"),
        look_reply("describe_object", name="NoSuchThing"),
        look_reply("describe_object", name="NoSuchThing"),
        code_reply("pad()"),
        verdict_reply("pass"),
    ], budgets=Budgets(looks_per_turn=2))

    assert len([c for c in tools.calls if c[0] == "describe_object"]) == 2


def test_the_carried_looks_are_bounded():
    """An unbounded list would grow the request for the whole length of a run."""
    from core.loop import Looks

    looks = Looks()
    for number in range(Looks.CARRIED + 4):
        looks.remember("describe_type", {"type_id": "T%d" % number}, {"ok": number})

    carried = looks.still_true()

    assert len(carried) == Looks.CARRIED
    assert carried[-1]["arguments"] == {"type_id": "T%d" % (Looks.CARRIED + 3)}, \
        "the oldest looks went, not the newest"


# ── failing the same way twice (FCC-100) ──────────────────────────────────────
#
# A recorded run failed one Step three times running with the identical "moved
# no material" traceback. Each rewrite renamed the objects and restructured the
# loop that built the profiles, and none of them changed the direction of the
# cut the traceback was describing. Then it replanned and did the same three
# again. The traceback said what to change; nothing said that the answer being
# tried had already been tried.


NO_MATERIAL = ("Traceback (most recent call last):\n"
               "RuntimeError: this Step added a feature that neither removed nor added "
               "any material:\nPocketHook (PartDesign::Pocket): 421045.77 mm3 before, "
               "421045.77 mm3 after\n")

IN_PIECES = ("Traceback (most recent call last):\n"
             "RuntimeError: this Step left a Body in more than one piece:\n"
             "Body: 5 separate solids\n")


def test_the_same_failure_twice_is_told_not_to_write_a_third_variation():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"), code_reply("third()"),
        verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), failed(NO_MATERIAL)])

    third = json.loads(llm.calls[3][-1]["content"])
    said = third["you_have_failed_this_the_same_way_twice"]

    assert "failed 2 times with the same failure" in said
    assert "Do not write a third variation" in said
    assert "Change the *direction* this time" in said
    assert "Reversed" in said and "ThroughAll" in said
    assert "not the size of the cut" in said


def test_the_directive_matches_the_failure_and_not_some_other_one():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"), code_reply("third()"),
        verdict_reply("pass"),
    ], applies=[failed(IN_PIECES), failed(IN_PIECES)])

    said = json.loads(llm.calls[3][-1]["content"])["you_have_failed_this_the_same_way_twice"]

    assert "do not meet" in said
    assert "share their endpoints" in said
    assert "Reversed" not in said, "the wrong failure's advice"


def test_two_different_failures_in_a_row_are_progress_and_are_left_alone():
    """
    The first thing was fixed and a second surfaced. Telling a run that is
    getting somewhere to change tack would be wrong.
    """
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"), code_reply("third()"),
        verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), failed(IN_PIECES)])

    third = json.loads(llm.calls[3][-1]["content"])

    assert "you_have_failed_this_the_same_way_twice" not in third


def test_one_failure_says_nothing_about_repeating_yourself():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"),
        verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL)])

    assert "you_have_failed_this_the_same_way_twice" not in json.loads(
        llm.calls[2][-1]["content"])


def test_a_failure_nothing_recognises_is_not_given_invented_advice():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"), code_reply("third()"),
        verdict_reply("pass"),
    ], applies=[failed("ZeroDivisionError: division by zero"),
                failed("ZeroDivisionError: division by zero")])

    assert "you_have_failed_this_the_same_way_twice" not in json.loads(
        llm.calls[3][-1]["content"])


def test_the_directive_offers_leaving_the_step_out_rather_than_repeating():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("first()"), code_reply("second()"), code_reply("third()"),
        verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), failed(NO_MATERIAL)])

    said = json.loads(llm.calls[3][-1]["content"])["you_have_failed_this_the_same_way_twice"]

    assert "leave this Step out" in said
    assert "Plan is given this one back" in said


def test_a_step_that_never_failed_any_other_way_tells_plan_to_change_the_step():
    """
    Rewording it sends Execute back at the same wall, which is what the
    recorded run did: three identical failures, a replan, and three more.
    """
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("one()"), code_reply("two()"), code_reply("three()"),
        plan_reply("Cut the hooks, differently"),
        code_reply("four()"),
        verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL)] * 3, budgets=Budgets(attempts_per_step=3))

    to_plan = json.dumps(llm.calls[4])

    assert "failed the same way" in to_plan
    assert "Rewording it will send Execute back at the same wall" in to_plan
    assert "Change what the Step asks for" in to_plan


def test_a_step_that_failed_variously_does_not_tell_plan_to_change_the_step():
    _, _, llm, _ = drive([
        plan_reply("Cut the hooks"),
        code_reply("one()"), code_reply("two()"), code_reply("three()"),
        plan_reply("Cut the hooks, differently"),
        code_reply("four()"),
        verdict_reply("pass"),
    ], applies=[failed(NO_MATERIAL), failed(IN_PIECES), failed(NO_MATERIAL)],
       budgets=Budgets(attempts_per_step=3))

    assert "Rewording it" not in json.dumps(llm.calls[4])


# ── one approval, standing (FCC-101) ──────────────────────────────────────────
#
# Sixty-nine of a hundred and thirty-five minutes of one recorded session were
# spent waiting at a Plan Gate, against six seconds of FreeCAD. Four of those
# waits were revisions of a Plan the user had already read and approved: a Step
# Execute could not build, checks that did not hold, part of the description
# nothing planned. A standing approval is the user saying "get on with it" —
# and it covers the Plan and only the Plan.


def gate_that(*answers):
    """A Gate scripted turn by turn; anything past the script approves once."""
    from core.loop import APPROVE, Approval

    queued = list(answers)
    asked = []

    def gate(steps):
        asked.append(list(steps))
        return queued.pop(0) if queued else Approval(APPROVE)

    gate.asked = asked
    return gate


def driven_through(gate, turns, **kwargs):
    llm = ScriptedLLMClient(turns)
    tools = FakeTools(kwargs.pop("applies", ()), views=kwargs.pop("views", None),
                      checks=kwargs.pop("checks", ()))
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                 budgets=kwargs.pop("budgets", None) or Budgets(), gate=gate, **kwargs)
    return result, events, llm, tools


def test_a_standing_approval_is_not_asked_again_when_the_plan_is_revised():
    from core.loop import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("The Document already has it"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 1, "the revised Plan asked again: %d Gates" % len(gate.asked)


def test_without_a_standing_approval_every_plan_still_reaches_the_gate():
    """FCC-68 unchanged: this is opt-in, and the default is what it always was."""
    gate = gate_that()

    result, _, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("The Document already has it"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 2


def test_a_standing_approval_is_on_the_stream_as_an_approval_not_as_a_silence():
    from core.loop import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    _, events, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("Already done"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        verdict_reply("pass"),
    ])

    gates = events.of(GATE)

    assert [g.data.get("decision") for g in gates] == [None, "approve", None, "approve"]
    assert gates[0].data["asked"] is True
    assert gates[1].data["standing"] is True
    assert gates[2].data["asked"] is False, \
        "a Gate nobody was asked at is not distinguishable from one they were"
    assert gates[3].data["standing"] is True


def test_an_ordinary_approval_does_not_stand():
    from core.loop import APPROVE, Approval

    gate = gate_that(Approval(APPROVE), Approval(APPROVE))

    _, events, _, _ = driven_through(gate, [
        plan_reply("Build it"),
        obsolete_reply("Already done"),
        plan_reply("Build the rest"),
        code_reply("rest()"),
        verdict_reply("pass"),
    ])

    assert all(g.data.get("standing") in (None, False) for g in events.of(GATE))


def test_a_standing_amendment_is_not_a_standing_approval():
    """
    Only an approval can stand. An amendment is the user changing the Plan, and
    the Plan they get back is one they have not read.
    """
    from core.loop import AMEND, APPROVE, Approval

    gate = gate_that(Approval(AMEND, "make it 20mm", standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Build it 10mm"),
        plan_reply("Build it 20mm"),
        code_reply("build()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 2, "an amendment stood in for an approval"


def test_a_standing_approval_still_lets_the_user_be_asked_about_the_result():
    """
    It covers the Plan. What to do about a result that did not pass is not the
    Plan, and neither is deleting the user's own work.
    """
    from core.loop import ACCEPT, APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))
    decided = []

    result, _, _, _ = driven_through(
        gate,
        [design_reply(A_NUT), planning_the_nut("Make it"), code_reply("make()"),
         assertion_reply(CHECKS)],
        checks=[assertion_failed(ASSERT_FAILED)],
        attachments=[RENDER_PNG],
        decide=lambda failure: decided.append(failure) or ACCEPT,
    )

    assert result.ok
    assert decided, "a standing approval swallowed the question about the result"


def test_a_standing_approval_does_not_cover_an_assumption_the_user_has_not_read():
    """
    FCC-101, corrected. The value nobody supplied is the one thing a standing
    approval cannot stand over: it is remade from nothing on every revision,
    and the Step's prose is the only place it is visible before the geometry.

    The recorded run had an undimensioned cutout approved as a rectangle, come
    back as a semicircular R15 and then as a circular R30 that swallowed the
    ring the clamp exists to close, with the user shown only the rectangle.
    """
    from core.loop import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, events, _, _ = driven_through(gate, [
        plan_reply("Cut an assumed rectangular pocket 30 wide"),
        obsolete_reply("The Document already has it"),
        plan_reply("Cut an assumed circular R30 pocket"),
        code_reply("cut()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 2, "a new assumption went past a standing approval"
    assert [g.data.get("assumed") for g in events.of(GATE)] == [None, None, 1, None], \
        "the stream does not say why the Gate was asked at again"


def test_a_standing_approval_still_covers_a_revision_that_assumes_nothing_new():
    """
    What stands is approval of the path, and FCC-101 is the whole reason it
    stands: a revision that only rewrites how the part is built asks nobody.
    """
    from core.loop import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Cut an assumed rectangular pocket 30 wide"),
        obsolete_reply("The Document already has it"),
        plan_reply("Cut an assumed rectangular pocket 30 wide"),
        code_reply("cut()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 1, "a Plan assuming nothing new was put to the user again"


def test_a_plan_that_assumes_nothing_is_covered_by_a_standing_approval():
    """The ordinary revision — no invented value in it at all — is untouched."""
    from core.loop import APPROVE, Approval

    gate = gate_that(Approval(APPROVE, standing=True))

    result, _, _, _ = driven_through(gate, [
        plan_reply("Pad the profile 30 mm"),
        obsolete_reply("The Document already has it"),
        plan_reply("Pocket the bore through all"),
        code_reply("cut()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert len(gate.asked) == 1


def test_a_standing_approval_does_not_survive_into_another_run():
    """It is one run's patience, not a setting."""
    from core.loop import APPROVE, Approval

    driven_through(gate_that(Approval(APPROVE, standing=True)), [
        plan_reply("Build it"), code_reply("build()"), verdict_reply("pass"),
    ])

    gate = gate_that()
    driven_through(gate, [
        plan_reply("Build it"), code_reply("build()"), verdict_reply("pass"),
    ])

    assert len(gate.asked) == 1


def test_a_rejection_still_stops_a_run_that_had_a_standing_approval_before_it():
    from core.loop import APPROVE, REJECT, Approval

    gate = gate_that(Approval(REJECT))

    result, _, _, tools = driven_through(gate, [plan_reply("Build it")])

    assert result.blocked
    assert tools.applied_codes() == []


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_part_whose_outside_nobody_carries_goes_back_to_the_drawing():
    """
    `silhouette` has raised "no feature carries the outer contour" for as long
    as it has existed and nothing ever acted on it. In the recorded run
    (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`) it stood in
    `conflicts`, the Gate showed the Steps, the user approved, and the Plan
    padded a profile it invented.

    So the drawing is asked before anything is planned, and a Design turn that
    writes the outer profile in settles it — the run goes on and never troubles
    the user with it.
    """
    said_in_passing = {
        "datums": [{"name": "the left end face",
                    "is": "the planar end face at the left"}],
        "envelope": {"length between the end faces": read("150 mm")},
        "features": [{"id": "fillets", "kind": "R2 fillets on all edges shown red",
                      "radius": read("2 mm"),
                      "edge_selection": [{"what": "the capsule-shaped exterior loops",
                                          "value": "each loop", "flag": "read"}]}],
    }
    the_contour = {"features": [
        {"id": "outer-profile",
         "kind": "obround (capsule) outer profile, 150 x 50, ends R25",
         "plane": read("the plane of the length and the height"),
         "position": {"the left end face": read("25 mm")},
         "radius": read("25 mm")}]}

    result, events, llm, _ = drive(
        [design_reply(said_in_passing), design_reply(the_contour),
         plan_reply("Pad the profile",
                    satisfies=everything(said_in_passing, the_contour)),
         code_reply("box()"), assertion_reply("assert True, 'the length'"),
         verdict_reply("pass")],
        attachments=[RENDER_PNG])

    assert result.ok, result.reason
    asked = json.loads(llm.calls[1][-1]["content"][0]["text"])
    assert "outer profile" in asked["the_question"], asked.get("the_question")
    assert events.first(QUESTION) is None, \
        "the user was asked about a contour the drawing settled"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_a_run_that_still_cannot_say_what_shape_the_part_is_does_not_plan_one():
    """
    The block this replaces was a part built as a box with every check green.
    Where neither the drawing nor a person will say what the outer contour is,
    there is nothing to build but the prism the overall sizes make — so the run
    stops and says what to answer, rather than planning one.
    """
    said_in_passing = {
        "envelope": {"length between the end faces": read("150 mm")},
        "features": [{"id": "fillets", "kind": "R2 fillets on all edges shown red",
                      "radius": read("2 mm"),
                      "edge_selection": [{"what": "the rounded end surfaces",
                                          "value": "each loop", "flag": "read"}]}],
    }

    result, events, _, _ = drive(
        [design_reply(said_in_passing)] + [design_reply({})] * 4,
        attachments=[RENDER_PNG], ask=lambda question: "")

    assert result.blocked, result.reason
    assert "outer profile" in result.reason or "outside" in result.reason, result.reason
    assert events.first(QUESTION) is not None, \
        "the run blocked without ever putting the question to the user"


@pytest.mark.skipif(not sheet_reader.available(), reason="PyMuPDF is not installed")
def test_design_is_given_the_line_work_of_the_views_in_millimetres():
    """
    The text layer told Design what the sheet *says*; a drawing says most of
    what it says in lines. Without them a distance nobody dimensioned — the
    height at which an arm meets a ring, an end flush with a tangent — has no
    evidence behind it at all, and the recorded run put three such values to the
    user and then invented them.

    So the geometry goes out beside the strings, in the part's own millimetres,
    said to be measurable rather than decorative.
    """
    llm = ScriptedLLMClient([design_reply(A_DATUM), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this", llm=llm, tools=FakeTools().registry, attachments=[MODEL_MANIA])

    said = _said_to_design(llm)
    assert "line work" in said, "the views' own geometry never reached Design"
    assert "arc R" in said or "line (" in said, "the line work arrived with no entities"
    assert "derived" in said, \
        "nothing told Design that a value measured off the line work is not a guess"


def test_a_phase_that_answered_and_then_explained_itself_is_still_an_answer():
    """
    The recorded run (`logs/sessions/6fbd31e98fb540818086611841fa9aff.jsonl`)
    died on a correct reply: Design answered `{"design": {}, "question": ""}`
    and then wrote a paragraph explaining that the two strings it was asked
    about are the item numbers of a notes list. The whole reply had to parse or
    none of it did, so the object that was right there was thrown away and the
    run ended with an error the user could do nothing about.
    """
    from core.loop import _as_json

    said = ('{"design": {}, "question": ""}\n\nThe two unaccounted strings, `3.` and '
            '`4.`, are the item numbers of the NOTES list on page 2.')

    assert _as_json(said, "design") == {"design": {}, "question": ""}


def test_a_reply_with_no_json_in_it_is_still_a_phase_that_did_not_answer():
    """
    Reaching into a reply for an object must not turn prose into an answer: a
    phase that never replied with JSON is a phase that did not do its job, and
    the error quotes what it said instead.
    """
    from core.loop import PhaseError, _as_json

    with pytest.raises(PhaseError) as raised:
        _as_json("I would rather describe this in words.", "design")

    assert "did not reply with JSON" in str(raised.value)


def test_a_phase_that_answered_in_prose_is_asked_once_more_rather_than_ending_the_run():
    """
    Measured on the gateway
    (`logs/sessions/ea390959745a453488138fddcc879635.jsonl`): ten Steps approved
    at the Gate, and Execute replied "I need to check the Hole feature's
    properties before writing the counterbore steps" — which is a look, and a
    look is a key of the object Execute answers with. The model had the answer
    and wrote it as a sentence, and the run died on it.
    """
    result, _, llm, tools = drive([
        plan_reply("Add a box"),
        "I need to check the Hole feature's properties first.",
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])

    assert result.ok, result.reason
    assert tools.applied_codes() == ["doc.addObject('Part::Box', 'Box')"]


def test_the_re_ask_quotes_what_the_phase_said_and_names_the_shape():
    """
    A directive about JSON in general is one the last reply already disobeyed.
    What makes the second turn different is the model's own sentence handed
    back to it, with the one thing that is nearly always true of it: the
    sentence is the answer, in the wrong clothes.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        "I need to check the Hole feature's properties first.",
        code_reply("box()"),
        verdict_reply("pass"),
    ])

    asked_again = llm.calls[2][-1]["content"]

    assert "I need to check the Hole feature's properties first." in asked_again
    assert "asks for the look *in* the JSON" in asked_again


def test_the_re_ask_of_a_phase_that_is_shown_pictures_keeps_the_pictures():
    """
    Design's request is content blocks, not a string. A re-ask that flattened
    it would take the drawing away in the turn the phase is being asked to read
    it more carefully.
    """
    _, _, llm, _ = drive([
        "Let me describe this part in words instead.",
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert _pictures_shown(llm, at=1), "the re-ask went out without the drawing"
    assert "carried no JSON object" in _said_to_design(llm, at=1)


def test_a_revision_is_shown_the_plan_it_is_revising():
    """
    The harness defect behind
    `logs/sessions/9958ccdc8e324a74ade9b08405be465b.jsonl`: the coverage check
    told Plan which entries nothing built, and Plan was asked for a new Plan
    without being shown the one it had written. It answered the complaint alone
    — two Steps for the two named entries — was told the nine Steps it had just
    dropped were now unplanned, wrote those again, dropped the two, and spent
    the whole revision budget without a single Step running.
    """
    part = {"envelope": {"length": read("80 mm"), "width": read("40 mm")}}
    both = everything(part)

    _, _, llm, _ = drive([
        design_reply(part),
        plan_reply("Pad the length", satisfies=both[:1]),
        plan_reply("Pad the length", "Pad the width", satisfies=both),
        code_reply("box()"),
        assertion_reply("assert True, 'the length'"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    revised = _turns_of(llm, "plan")[1]

    assert "the_plan_you_wrote" in revised, \
        "Plan was asked to rewrite a Plan it was not shown"
    assert revised["the_plan_you_wrote"][0].startswith("Pad the length")
    assert "every Step you already wrote" in revised["what_went_wrong_last_time"]


def test_the_first_plan_of_a_run_is_not_a_revision_of_anything():
    """A key that is there on every turn stops meaning anything on the turn it matters."""
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("box()"),
        verdict_reply("pass"),
    ])

    assert "the_plan_you_wrote" not in _turns_of(llm, "plan")[0]


# ── a direction is put back to the drawing before it stops the run (FCC-110) ──


# The re-read that says which of the two was misread, and the Plan that follows
# from it: two axes are two planes, so they are two Steps (FCC-111).
_THE_AXIS_PUT_RIGHT = [
    design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
    design_reply({"features": [{"id": "right-mounting-hole",
                                "axis": {"value": "the height", "flag": "read",
                                         "round_in": "the upper plan"}}]}),
    plan_reply({"do": "Bore the clamp ring",
                "satisfies": ["envelope → length", "features → clamp-bore → axis"]},
               {"do": "Pocket the mounting hole",
                "satisfies": ["features → right-mounting-hole → axis"]}),
    code_reply("bore()", "pocket()"),
    assertion_reply("assert True, 'the hole'"),
    verdict_reply("pass"),
]


def test_a_re_read_that_corrects_the_axis_reaches_the_plan_instead_of_stopping():
    """
    The stop was the only move the loop had. The recorded run
    (`logs/sessions/daa17fb149474317840ef98439cfef6f.jsonl`) died before the
    Plan Gate on four findings that were the check's own fault, with nothing
    between "something is wrong" and "stop". Now the finding goes back to
    Design first, and a re-read that corrects it is a run that carries on.
    """
    result, events, _, tools = drive(
        _THE_AXIS_PUT_RIGHT, attachments=[RENDER_PNG])

    assert result.ok, events.first(BLOCKED) and events.first(BLOCKED).data["reason"]
    assert tools.applied_codes() == ["bore()", "pocket()"]


def test_the_re_read_is_told_what_the_finding_is():
    _, _, llm, _ = drive([
        design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
        design_reply({}),
    ], attachments=[RENDER_PNG])

    asked = json.loads(_said_to_design(llm, at=1))["the_question"]

    assert "round in the view its axis points at" in asked
    assert ("features" + UNDER + "clamp-bore" + UNDER + "axis") in asked


def test_what_the_correction_displaced_is_kept_in_the_reading():
    """A value is never lost — it is superseded, in writing, in the file."""
    _, events, _, _ = drive(_THE_AXIS_PUT_RIGHT, attachments=[RENDER_PNG])

    axis = part_file.read(events.last(PART).data["path"])["features"][1]["axis"]
    assert axis["value"] == "the height"
    assert axis["was"] == "the depth"
    assert axis["because"]


def test_a_re_read_that_settles_nothing_still_stops_the_run():
    """The stop stays. It is the last rung now, not the first."""
    result, events, _, tools = drive([
        design_reply(THE_CLAMP_READ_OFF_ONE_VIEW),
        design_reply({}),
        plan_reply("Pad the outer profile"),
    ], attachments=[RENDER_PNG])

    assert not result.ok
    assert not tools.applied_codes()
    assert "read off the wrong view" in events.first(BLOCKED).data["reason"]


# ── the geometry contradicts the sheet (FCC-122) ──────────────────────────────


def _a_sheet_that_contradicts_itself(tmp_path, wide=98.2, printed=100.0):
    """
    A traced plate that does not measure what is printed across it.

    1.8 mm by default, and that is not a free choice: the tracing has to be
    near enough to the drawing for a dimension's foot to find the face it names
    at all. Past `sketch_check.A_FOOT` the foot reaches nothing, and there is
    no comparison to make — see the test below, which holds that ceiling in
    place on purpose.
    """
    from core import sheet_reader

    corners = [(0.0, 0.0), (wide, 0.0), (wide, 30.0), (0.0, 30.0)]
    entities = [
        sheet_reader.Entity(kind="line", source="traced", role="contour",
                            size=sheet_reader._distance(corners[at],
                                                        corners[(at + 1) % 4]),
                            start=corners[at], end=corners[(at + 1) % 4])
        for at in range(4)]
    outline = sheet_reader.Outline(entities=entities, closed=True, area=3000.0)
    return sheet_reader.Sheet(
        source=str(tmp_path / "a.pdf"),
        drawn=[sheet_reader.Drawn(view="the front view", entities=entities,
                                  outlines=[outline])],
        between=[sheet_reader.Between(printed=printed, text="%g" % printed,
                                      view="the front view",
                                      first=(0.0, 15.0),
                                      second=(printed, 15.0))])


def test_a_sheet_that_contradicts_its_own_tracing_says_so_on_the_part(
        tmp_path, monkeypatch):
    """
    The whole of FCC-116 to FCC-121 arriving somewhere. `100` printed across a
    plate the pixels measure at 98.2 is two things that cannot both be true, and
    that is a conflict — not a correction, because which of the two is wrong is
    exactly what this cannot know.
    """
    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    monkeypatch.setattr(loop.sheet_reader, "read", lambda path, **kw: sheet)

    grown = loop._the_geometry_disagrees({"features": []}, ["a.pdf"])

    assert len(grown["conflicts"]) == 1
    said = str(grown["conflicts"][0])
    assert "100" in said and "98.2" in said
    assert "the front view" in said


def test_a_reading_too_far_out_is_past_what_this_check_can_see(
        tmp_path, monkeypatch):
    """
    The ceiling, held in place deliberately. A dimension reaches the geometry
    through the feet of its own extension lines, so a tracing further out than
    `sketch_check.A_FOOT` from the drawing has no face for the foot to name and
    there is nothing to compare.

    Widening the band does not fix that, it breaks it: measured on the four
    sheets, a band of a tenth of the printed value writes 16 distances of which
    3 then disagree by 4.5 to 4.9 mm, and an unambiguity rule instead of a band
    writes 25 of which 13 disagree — including a 150 matched to faces 98.77
    apart. Those sheets agree with themselves, so every one of those is a wrong
    face, not a finding.

    So this is a floor: it proves the tracing, the printed sizes and the
    printed distances agree. A misread digit is caught by the arithmetic that
    needs no correspondence at all — the sheet measuring its own numbers
    (`Printed.measures`), and a number nobody printed (`uncited`).
    """
    sheet = _a_sheet_that_contradicts_itself(tmp_path, wide=100.0, printed=108.0)
    monkeypatch.setattr(loop.sheet_reader, "read", lambda path, **kw: sheet)

    assert loop._the_geometry_disagrees({"features": []}, ["a.pdf"]) == {
        "features": []}


def test_a_sheet_that_agrees_with_itself_adds_nothing(tmp_path, monkeypatch):
    """
    Measured on the four sheets in `tests/modelmania`: 13 printed distances
    reach a traced outline and every one of them agrees within 1.08 mm, which
    is the tracing's own bias. So a run against a real sheet gains nothing
    here, and that is the right answer rather than a weak check.
    """
    from core import sheet_reader

    sheet = _a_sheet_that_contradicts_itself(tmp_path, wide=100.0, printed=100.0)
    monkeypatch.setattr(loop.sheet_reader, "read", lambda path, **kw: sheet)

    assert loop._the_geometry_disagrees({"features": []}, ["a.pdf"]) == {
        "features": []}


def test_without_a_freecad_to_build_in_the_description_is_untouched(
        tmp_path, monkeypatch):
    """Degrades the way the rest of the reading does: silently and completely."""
    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    monkeypatch.setattr(loop.sheet_reader, "read", lambda path, **kw: sheet)
    monkeypatch.setattr(loop.sketch_check, "available", lambda: False)

    assert loop._the_geometry_disagrees({"features": []}, ["a.pdf"]) == {
        "features": []}


# ── a number on no sheet (FCC-124) ────────────────────────────────────────────


def test_a_number_on_no_sheet_is_put_to_the_drawing_and_then_left_as_a_conflict(
        monkeypatch):
    """
    The other direction of the sweep, which is the shape of ADR-0019. Asked
    once, and then recorded — the number may be right and derived from
    something this cannot see, so the Gate is where a person says so.
    """
    monkeypatch.setattr(loop, "_off_the_sheet", lambda attachments, part: [1234.0])
    asked = []

    def design(payload, llm, events, attachments):
        asked.append(payload["the_question"])
        return {}, None

    monkeypatch.setattr(loop, "design", design)

    grown = loop._every_number_is_the_sheets(
        {"features": []}, ["a.pdf"], None, None, loop.Budgets(), lambda: False)

    assert asked and "1234" in asked[0]
    assert len(grown["conflicts"]) == 1
    assert "1234" in str(grown["conflicts"][0])


def test_a_number_the_look_accounted_for_is_not_a_conflict(monkeypatch):
    """Where the re-read points at where it came from, there is nothing to record."""
    answers = iter([[1234.0], []])
    monkeypatch.setattr(loop, "_off_the_sheet",
                        lambda attachments, part: next(answers))
    monkeypatch.setattr(loop, "design",
                        lambda payload, llm, events, attachments: ({}, None))

    grown = loop._every_number_is_the_sheets(
        {"features": []}, ["a.pdf"], None, None, loop.Budgets(), lambda: False)

    assert "conflicts" not in grown


def test_a_description_whose_numbers_are_all_the_sheets_is_left_alone(monkeypatch):
    monkeypatch.setattr(loop, "_off_the_sheet", lambda attachments, part: [])
    monkeypatch.setattr(loop, "design", lambda *a, **kw: 1 / 0)

    assert loop._every_number_is_the_sheets(
        {"features": []}, ["a.pdf"], None, None, loop.Budgets(),
        lambda: False) == {"features": []}
