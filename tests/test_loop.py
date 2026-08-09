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
    ANSWER, APPLIED, ASSERTION, BLOCKED, ERROR, PLAN, PLAN_REVISED,
    PREFLIGHT_FAILED, QUESTION, READING, REVIEW_VERDICT, STEP_STARTED, THOUGHT,
    TOOL_CALL, TOOL_RESULT, EventStream,
)
from core.freecad_tools import TRAPS
from core.llm import TEXT, Chunk, ProviderRefused, ScriptedLLMClient
from core.loop import PROMPTS, Budgets, prompt, revisions_ceiling, run
from core.tools import ToolRegistry

SNAPSHOT = {"document": "Unnamed", "objects": [], "object_count": 0,
            "active_body": None, "selection": []}


def _a_real_png():
    """
    Review is meant to look at the render, so the tests hand it a file that
    actually decodes rather than a path to nothing.
    """
    handle, path = tempfile.mkstemp(prefix="loop_render_", suffix=".png")
    with os.fdopen(handle, "wb") as png:
        png.write(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8Dw"
            "HwAFAAH/q842iQAAAABJRU5ErkJggg=="))
    return path


RENDER_PNG = _a_real_png()

# What a real Apply hands back: the feature tree with its parameters, and the
# geometry read off the Shape (FCC-81).
DESCRIPTION = {
    "features": [{"name": "Box", "label": "Box", "type": "Part::Box",
                  "parameters": {"Length": "10.0 mm"}}],
    "geometry": {"solids": 1, "surfaces": [{"kind": "Plane", "count": 6}]},
}

A_READING = (
    "Base: 80 x 40 x 10 mm plate.\n"
    "Features: 2x ⌀8 through holes, 15 mm from each end on the centreline; "
    "R5 fillets on all four corners.\n"
    "Not defined by the drawing: material, tolerance, surface finish."
)


# ── scripted phase replies ────────────────────────────────────────────────────


def plan_reply(*steps, thinking=None, acceptance=None):
    reply = {"steps": list(steps)}
    if acceptance is not None:
        reply["acceptance"] = list(acceptance)
    body = json.dumps(reply)
    return {"reasoning": thinking, "text": body} if thinking else body


def reading_reply(reading, thinking=None):
    """The Reading phase's turn — first in a run that has an attachment."""
    body = json.dumps({"reading": reading})
    return {"reasoning": thinking, "text": body} if thinking else body


def code_reply(code, label="Step", thinking=None):
    body = json.dumps({"code": code, "label": label})
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
            return dict(SNAPSHOT)

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

        @registry.tool("apply_step", "Apply one Step.", {
            "type": "object",
            "properties": {"code": {"type": "string"}, "label": {"type": "string"}},
            "required": ["code"],
        })
        def apply_step(code, label="Step"):
            self.calls.append(("apply_step", {"code": code, "label": label}))
            # A None entry, or running off the end, means "this one worked".
            queued = self.applies.pop(0) if self.applies else None
            return queued if queued is not None else dict(self.APPLIED)

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
          views=None, checks=(), attachments=()):
    """Run the loop over scripted turns and hand back everything to assert on."""
    llm = ScriptedLLMClient(turns)
    tools = FakeTools(applies, views=views, checks=checks)
    events = EventStream()
    kwargs = {"ask": ask} if ask is not None else {}
    result = run(intent, llm=llm, tools=tools.registry, events=events,
                 budgets=budgets or Budgets(), attachments=list(attachments), **kwargs)
    return result, events, llm, tools


# ── the prompts each phase sends ──────────────────────────────────────────────


def _system_messages(llm):
    """The system message of every turn, in order, as the provider saw it."""
    return [call[0]["content"] for call in llm.calls]


@pytest.mark.parametrize("turn, phase",
                         [(0, "reading"), (1, "plan"), (2, "execute"), (3, "assertion"),
                          (4, "review")])
def test_each_phase_sends_the_prompt_from_its_config_file(turn, phase):
    """
    The file reached the provider — not that the loader returned a string. The
    prompts are config, so this reads the config. FCC-3.

    Driven with an attachment, because Reading is a turn only where there is
    something to read (FCC-86); every later phase shifts by one with it.
    """
    _, _, llm, _ = drive([
        reading_reply("A 10 mm cube."),
        plan_reply("Add a box", acceptance=["10 mm on a side"]),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        assertion_reply("assert True, '10 mm on a side'"),
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


def test_the_reading_rules_moved_to_the_reading_phase_whole():
    """
    FCC-59's rules were live in plan.md and did not hold, so FCC-86 moved who
    reads and when rather than rewriting them a fourth time. They move as they
    are: what is asserted is that every one of them survived the move, that
    Plan no longer carries any of it, and that Review is still told none of it.
    FCC-59, FCC-86.
    """
    _, _, llm, _ = drive([
        reading_reply("A 10 mm cube."),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    reading_system, plan_system, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    for rule in ("A reading is not a description of the picture",
                 "what the drawing does not define",
                 "cannot both be true",
                 "an operation the drawing does not show",
                 "Read every plane the drawing uses",
                 "perpendicular"):
        assert rule in reading_system, "the Reading phase lost: " + rule
        assert rule not in plan_system, "plan.md still carries the Reading rule: " + rule
        assert rule not in review_system


def test_a_reading_carries_a_named_datum_a_value_and_a_read_or_assumed_flag():
    """
    Dimensions were read and positions were not: a magnitude arrived without
    what it was measured from, and a position with no named datum degrades into
    "somewhere sensible", which defaults to centred. Prose is what allows it —
    a sentence carries a number without carrying its datum. This is the third
    pass at the same symptom through copy, so what the copy now demands is the
    three fields, not another sentence. FCC-87.
    """
    _, _, llm, _ = drive([
        reading_reply("A 10 mm cube."),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    reading_system, _, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "A reading is not prose" in reading_system
    assert "<what it is> — from <datum> — <value> — read" in reading_system
    assert "named the way the drawing names it" in reading_system
    assert "A magnitude without its datum is the defect this phase exists for" \
        in reading_system
    assert "A reading is not prose" not in review_system


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
        reading_reply("A 10 mm cube."),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    reading_system = " ".join(_system_messages(llm)[0].split())

    assert "a fraction of a named overall" in reading_system
    assert "a fraction is a full record, not a fallback" in reading_system
    assert "Never stop to ask for dimensions" in reading_system
    assert "Exactly one assumption in the whole reading" in reading_system


def test_the_acceptance_inherits_the_datum_and_the_flag():
    """
    The hole `34.5` and `50.5` came through: the requirement to write an assumed
    value into the prose of a Step exists and was obeyed, and there was no
    matching requirement on the Acceptance — so invented numbers stood in the
    list as fact, and Assertion would measure the part against them and report
    a pass. FCC-87.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    plan_system, _, review_system = (
        " ".join(text.split()) for text in _system_messages(llm))

    assert "Every entry names what it is measured from" in plan_system
    assert "whether that came off the drawing or was assumed" in plan_system
    assert "an unmarked assumption is measured against the part" in plan_system
    # Assertion is untouched: it still writes asserts from the Acceptance text.
    assert "Every entry names what it is measured from" not in review_system


def test_a_structured_reading_survives_into_plan_and_onto_the_stream():
    """
    A model that answers with an object rather than a paragraph is being
    reasonable; the pipe has to carry it to the Plan Gate and to Plan without
    dropping it. FCC-87.
    """
    structured = {"features": [{"what": "Ø8 through hole", "datum": "the top face",
                                "value": "14 mm", "read": True}]}
    _, events, llm, _ = drive([
        reading_reply(structured),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    rendered = events.first(READING).data["text"]
    assert json.loads(rendered) == structured, "the structure was flattened away"
    assert json.loads(llm.calls[1][-1]["content"])["how_the_drawing_was_read"] == rendered


def test_plan_is_handed_a_finished_reading_and_not_the_picture():
    """
    Far too much of the split is invisible in the background otherwise: Plan is
    told plainly that this is a Reading somebody else produced, and that what it
    calls undefined stays undefined. FCC-86.
    """
    _, _, llm, _ = drive([
        reading_reply("A 10 mm cube."),
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])
    plan_system = " ".join(_system_messages(llm)[1].split())

    assert "how_the_drawing_was_read" in plan_system
    assert "You do not get the picture itself" in plan_system
    assert "What it lists as undefined stays undefined" in plan_system


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


def test_a_missing_prompt_file_raises_rather_than_sending_an_empty_prompt():
    with pytest.raises(IOError):
        prompt("no_such_phase")


# ── Plan ──────────────────────────────────────────────────────────────────────


def test_plan_produces_an_ordered_list_of_steps_from_the_intent_and_a_snapshot():
    result, events, llm, tools = drive([
        plan_reply("Create a 10mm box", "Fillet the top edge"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        code_reply("pass"),
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
        code_reply("first()"),
        code_reply("second()"),
        code_reply("third()"),
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
    ], budgets=Budgets(looks_per_step=2))

    assert result.ok
    assert len([c for c in tools.calls if c[0] == "supported_types"]) == 2

    final = json.loads(llm.calls[3][-1]["content"])
    assert final["tools_you_may_look_with"] == [], "the last turn still invited a look"
    assert "code" in final["this_is_your_last_turn"]
    assert "obsolete" in final["this_is_your_last_turn"]
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
        budgets=Budgets(looks_per_step=2, attempts_per_step=3, revisions=0),
    )

    assert not result.ok
    assert events.first(ERROR) is None
    blocked = events.first(BLOCKED)
    assert blocked is not None
    assert "Step 1 of 1" in blocked.data["reason"]
    assert len(blocked.data["tried"]) == 3
    assert all("no looks left" in t["traceback"] for t in blocked.data["tried"])


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


def test_execute_runs_one_step_at_a_time():
    _, events, _, _ = drive([
        plan_reply("First", "Second"),
        code_reply("first()"),
        code_reply("second()"),
        verdict_reply("pass"),
    ])

    started = events.of(STEP_STARTED)
    assert [(e.data["index"], e.data["total"]) for e in started] == [(1, 2), (2, 2)]


def test_execute_sees_only_that_steps_own_failed_attempts():
    _, _, llm, _ = drive(
        [
            plan_reply("First", "Second"),
            code_reply("first_bad()"),
            code_reply("first_good()"),
            code_reply("second()"),
            verdict_reply("pass"),
        ],
        applies=[failed("AttributeError: no Length on this"), None],
    )

    retry_of_first = json.dumps(llm.calls[2])
    first_of_second = json.dumps(llm.calls[3])

    assert "AttributeError: no Length on this" in retry_of_first
    assert "AttributeError: no Length on this" not in first_of_second, \
        "Step 2 was shown Step 1's failure"
    assert "first_bad()" not in first_of_second


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
        plan_reply("Only", acceptance=["10 mm on a side"]),
        code_reply("box()"),
        assertion_reply("assert True, '10 mm on a side'"),
        verdict_reply("pass"),
    ])

    to_assertion = json.loads(llm.calls[2][-1]["content"])
    # The prose about the views is appended after the JSON, past a blank line.
    to_review = json.loads(llm.calls[3][-1]["content"][0]["text"].split("\n\n")[0])

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


def test_an_attachment_reaches_the_reading_phase_as_a_picture():
    """
    The panel used to collect the attached paths and throw them away, so the
    user showed the assistant a photo it never saw. FCC-4. The phase that
    receives it is the Reading phase since FCC-86; Plan never sees a picture.
    """
    llm = ScriptedLLMClient([reading_reply(A_READING), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    run("Build this bracket", llm=llm, tools=FakeTools().registry, attachments=[RENDER_PNG])

    content = llm.calls[0][-1]["content"]
    images = [block for block in content if block.get("type") == "image_url"]

    assert "Build this bracket" in content[0]["text"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert RENDER_PNG not in json.dumps(content), "the path was sent instead of the bytes"
    assert isinstance(llm.calls[1][-1]["content"], str), "Plan was sent the picture"


def test_several_attachments_all_reach_the_reading_phase():
    llm = ScriptedLLMClient([reading_reply(A_READING), plan_reply("Only"),
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
    llm = ScriptedLLMClient([reading_reply(A_READING), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")])

    result = run("Build this", llm=llm, tools=FakeTools().registry,
                 attachments=["/nowhere/gone.png", RENDER_PNG])

    assert result.ok
    content = llm.calls[0][-1]["content"]
    assert len([b for b in content if b.get("type") == "image_url"]) == 1


# ── the reading of the drawing ────────────────────────────────────────────────


def test_the_reading_is_an_event_of_its_own_standing_before_the_plan():
    """
    The Reading used to be a field on the Plan event, because Plan produced it.
    A phase of its own emits its own event, and a replayed transcript then shows
    it in the order it was actually produced. FCC-47, FCC-86.
    """
    _, events, _, _ = drive([reading_reply(A_READING), plan_reply("Only"),
                             code_reply("box()"), verdict_reply("pass")],
                            attachments=[RENDER_PNG])

    kinds = [event.kind for event in events]
    assert events.first(READING).data["text"] == A_READING
    assert kinds.index(READING) < kinds.index(PLAN)
    assert "reading" not in events.first(PLAN).data


def test_with_nothing_attached_there_is_no_reading_turn_at_all():
    """No extra provider call, and the Plan Gate is identical to today's."""
    _, events, llm, _ = drive([plan_reply("Only"), code_reply("box()"),
                               verdict_reply("pass")])

    assert events.first(READING) is None
    assert len(llm.calls) == 3, "a Reading turn was spent on an Intent with no picture"
    assert events.first(PLAN).data["steps"] == ["Only"]


def test_the_reading_is_carried_into_execute_as_text():
    """
    Execute stays on text deliberately — it is called per Step and again per
    look, so the picture would multiply across dozens of calls.
    """
    _, _, llm, _ = drive([reading_reply(A_READING), plan_reply("Only"),
                          code_reply("box()"), verdict_reply("pass")],
                         attachments=[RENDER_PNG])

    asked = llm.calls[2][-1]["content"]
    assert isinstance(asked, str), "Execute was sent a picture, not text"
    assert json.loads(asked)["reading_of_the_drawing"] == A_READING


def test_the_reading_reaches_plan_under_a_key_of_its_own():
    _, _, llm, _ = drive([reading_reply(A_READING), plan_reply("Only"),
                          code_reply("box()"), verdict_reply("pass")],
                         attachments=[RENDER_PNG])

    assert json.loads(llm.calls[1][-1]["content"])["how_the_drawing_was_read"] == A_READING


def test_execute_is_told_nothing_extra_when_there_was_no_reading():
    _, _, llm, _ = drive([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")])

    assert "reading" not in json.loads(llm.calls[1][-1]["content"])


def test_a_revision_plans_from_the_same_reading_and_does_not_read_again():
    """
    The Reading depends on neither the Snapshot nor the feedback, so a second
    call at the same picture is a provider call spent on nothing. FCC-86.
    """
    _, events, llm, _ = drive([
        reading_reply(A_READING),
        plan_reply("v1"), code_reply("v1()"), verdict_reply("fail", "not right"),
        plan_reply("v2"), code_reply("v2()"), verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    assert len(events.of(READING)) == 1
    replan = json.loads(llm.calls[4][-1]["content"])
    assert replan["how_the_drawing_was_read"] == A_READING
    assert replan["what_went_wrong_last_time"] == "not right"


def test_review_is_shown_the_drawing_beside_the_render():
    """
    Review compared a render against a sentence: the picture never reached it,
    so "does this resemble what I was shown" was not a question it could ask.
    """
    llm = ScriptedLLMClient([reading_reply(A_READING), plan_reply("Only"),
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
        plan_reply("Sketch the hex profile", "Pad it 6.5 mm",
                   acceptance=["13.0 mm across the flats",
                               "6.5 mm tall",
                               "an M8 through hole, 4.0 mm bore radius"]),
        code_reply("sketch()"), code_reply("pad()"),
        verdict_reply("pass"),
    ])

    plan = events.first(PLAN)
    assert plan.data["steps"] == ["Sketch the hex profile", "Pad it 6.5 mm"]
    assert plan.data["acceptance"] == ["13.0 mm across the flats", "6.5 mm tall",
                                       "an M8 through hole, 4.0 mm bore radius"]


def test_a_plan_without_acceptance_is_still_a_plan():
    """
    An empty list is not fatal: the run goes on and Review judges alone, as it
    did before there was a list at all. Making it fatal would kill runs over a
    prompt regression, and the one fatal answer stays "no Steps". FCC-75.
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


def test_plan_is_told_how_to_write_an_acceptance_list():
    """
    A hybrid vocabulary, not a closed one — a fixed list of allowed
    measurements would bound too tightly what can be measured. What goes in the
    prompt is the rule: what may be checked and how to approach checking it.
    FCC-75.
    """
    _, _, llm, _ = drive([
        plan_reply("Add a box"),
        code_reply("doc.addObject('Part::Box', 'Box')"),
        verdict_reply("pass"),
    ])
    # The prompts are wrapped copy; a sentence crosses line breaks.
    plan_system = " ".join(_system_messages(llm)[0].split())

    assert "One list for the whole Plan" in plan_system
    assert "control points" in plan_system
    assert "twenty is the ceiling" in plan_system
    assert "measurable off the Shape" in plan_system
    assert "bounding box is not a measurement" in plan_system

    # `Part.LineSegment` alone still appears in the traps, where it is a fact
    # about this build; what goes is Plan being asked to write it as a parameter.
    for cancelled in ("Every Step carries its **parameters**",
                      '"parameters": {"type": "PartDesign::Pad"',
                      '{"type": "Part.LineSegment"', '"properties": {"Length": 10}'):
        assert cancelled not in plan_system, \
            "plan.md still carries the parameters copy this ticket removes: " + cancelled


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
            code_reply("first()"),
            code_reply("nope1()"), code_reply("nope2()"), code_reply("nope3()"),
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
            code_reply("a1()"), code_reply("a2()"),
            code_reply("b1()"), code_reply("b2()"),
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
    result, events, _, _ = drive([plan_reply("Only"), "not JSON at all"])

    assert not result.blocked
    assert "did not reply with JSON" in result.reason
    assert events.first(ERROR) is not None


# ── The event stream ──────────────────────────────────────────────────────────


def test_the_event_stream_of_a_clean_run():
    _, events, _, _ = drive([
        plan_reply("Only", thinking="A box is a Part::Box."),
        code_reply("box()", thinking="addObject then set Length."),
        verdict_reply("pass", "Good.", thinking="The render shows a cube."),
    ])

    assert [e.kind for e in events] == [
        THOUGHT, PLAN,
        STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, APPLIED,
        THOUGHT, REVIEW_VERDICT,
    ]


def test_the_event_stream_of_a_clean_run_with_acceptance():
    """
    The same run with something to check: the Assertion phase stands between
    the last Step and the verdict, and its code rides on the Tool call the way
    a Step's does. FCC-76.
    """
    _, events, _, _ = drive([
        plan_reply("Only", acceptance=["10 mm on a side"]),
        code_reply("box()"),
        assertion_reply("assert True, '10 mm on a side'"),
        verdict_reply("pass", "Good."),
    ])

    assert [e.kind for e in events] == [
        PLAN,
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
        PLAN,
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
        PLAN, STEP_STARTED, TOOL_CALL, TOOL_RESULT, PREFLIGHT_FAILED, BLOCKED,
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
    result, events, _, _ = drive(["I would love to help but here is prose instead."])

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
        code_reply("a()"), code_reply("b()"),
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
        [question_reply(MISSING),
         plan_reply("Sketch the blank", "Pad it"), code_reply("a()"), code_reply("b()"),
         verdict_reply("pass")],
        intent=GEAR, ask=lambda question: asked.append(question) or "module 2, 20 teeth")

    assert asked == [MISSING], "Plan did not ask, or asked something else: %s" % asked
    question = events.first(QUESTION)
    assert question is not None and question.data["text"] == MISSING
    # The question came before any Plan reached the user.
    kinds = [e.kind for e in events]
    assert kinds.index(QUESTION) < kinds.index(PLAN)
    assert result.ok
    assert tools.applied_codes() == ["a()", "b()"]


def test_the_answer_reaches_plan_and_nothing_ran_before_it():
    llm = ScriptedLLMClient([
        question_reply(MISSING),
        plan_reply("Sketch a 20-tooth module-2 blank"), code_reply("a()"),
        verdict_reply("pass"),
    ])
    tools = FakeTools()

    run(GEAR, llm=llm, tools=tools.registry, ask=lambda _: "module 2, 20 teeth, 10 thick, ⌀8 bore")

    second_plan = json.dumps(llm.calls[1])
    assert "you_asked_and_the_user_answered" in second_plan
    assert "20 teeth" in second_plan
    assert MISSING in second_plan, "Plan was given the answer without the question"


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
    The user is asked at most `questions` times. After that Plan is told to
    choose; if it asks even then, the run fails rather than looping.
    """
    asked = []

    result, _, _, tools = drive(
        [question_reply("q1"), question_reply("q2"), question_reply("q3"), question_reply("q4")],
        intent=GEAR, ask=lambda q: asked.append(q) or "some answer",
        budgets=Budgets(questions=2))

    assert asked == ["q1", "q2"], "the user was asked past the budget: %s" % asked
    assert not result.ok
    assert "told to choose for itself" in result.reason
    assert tools.applied_codes() == []


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
            question_reply(MISSING),
            plan_reply("Possible"),
            code_reply("yes()"),
            verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
        ask=lambda question: asked.append(question) or "module 2, 20 teeth",
    )

    assert asked == [MISSING], "the re-plan planned without asking: %s" % asked
    assert result.ok
    assert events.first(PLAN_REVISED).data["steps"] == ["Possible"]
    # The answer reached the revised Plan, so it is built on a named value.
    revised = json.dumps(llm.calls[5])
    assert "20 teeth" in revised and MISSING in revised


def test_a_replan_after_a_rejected_verdict_asks_too():
    asked = []

    result, _, llm, _ = drive(
        [
            plan_reply("Make it"), code_reply("make()"), verdict_reply("fail", "20mm, not 10mm"),
            question_reply(MISSING),
            plan_reply("Make it 10mm"), code_reply("fix()"), verdict_reply("pass"),
        ],
        ask=lambda question: asked.append(question) or "10mm across the flats",
    )

    assert asked == [MISSING]
    assert result.ok
    assert "10mm across the flats" in json.dumps(llm.calls[4])


def test_a_replan_that_only_ever_asks_stops_the_run_rather_than_looping():
    asked = []

    result, _, _, _ = drive(
        [
            plan_reply("Impossible"), code_reply("nope1()"),
            question_reply("q1"), question_reply("q2"), question_reply("q3"),
        ],
        applies=[failed("one")],
        budgets=Budgets(attempts_per_step=1, questions=1),
        ask=lambda question: asked.append(question) or "some answer",
    )

    assert asked == ["q1"], "the user was asked past the budget: %s" % asked
    assert not result.ok
    assert "told to choose for itself" in result.reason


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


ACCEPTANCE = ["13.0 mm across the flats", "6.5 mm tall"]

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
    _, events, llm, tools = drive([
        plan_reply("Sketch it", "Pad it", acceptance=ACCEPTANCE),
        code_reply("sketch()"), code_reply("pad()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
    ])

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
    _, events, llm, _ = drive([
        plan_reply("Make it", acceptance=ACCEPTANCE),
        code_reply("make()"),
        assertion_reply(CHECKS),
        plan_reply("Make it 13 mm across the flats", acceptance=ACCEPTANCE),
        code_reply("fix()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
    ], checks=[assertion_failed(ASSERT_FAILED), None])

    to_plan = json.dumps(llm.calls[3])
    to_execute = json.dumps(llm.calls[4])

    assert "13.0 mm across the flats" in to_plan
    assert "AssertionError" in to_plan
    assert "AssertionError" not in to_execute, "the failed check reached Execute"
    assert events.first(PLAN_REVISED).data["steps"] == ["Make it 13 mm across the flats"]
    assert events.first(ASSERTION).data["ok"] is False


def test_a_run_out_of_revisions_names_the_assertions_not_review():
    """
    FCC-22's separate accounting, extended: a run stopped by failed checks must
    not tell the user Review kept rejecting the result. FCC-76.
    """
    result, _, _, _ = drive(
        [plan_reply("Make it", acceptance=ACCEPTANCE), code_reply("make()"),
         assertion_reply(CHECKS)] * 5,
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
    _, _, llm, _ = drive([
        plan_reply("Make it", acceptance=ACCEPTANCE),
        code_reply("make()"),
        assertion_reply(CHECKS, uncovered=["6.5 mm tall"]),
        verdict_reply("pass"),
    ], views=[RENDER_PNG] * 4)

    review_request = json.loads(llm.calls[-1][1]["content"][0]["text"].split("\n\n")[0])

    assert review_request["facts_the_checks_confirmed"] == ["13.0 mm across the flats"]
    assert review_request["what_the_checks_could_not_cover"] == ["6.5 mm tall"]


def test_a_broken_check_is_rewritten_and_never_reaches_plan_as_geometry():
    """
    An AttributeError in the checking code says nothing about the nut. Read as
    a failed model it would send Plan to rebuild a part that is already right,
    so it is handed back to the phase that wrote it, on the same attempts
    budget Execute uses. FCC-76.
    """
    result, events, llm, tools = drive([
        plan_reply("Make it", acceptance=ACCEPTANCE),
        code_reply("make()"),
        assertion_reply("bad_check()"),
        assertion_reply(CHECKS),
        verdict_reply("pass"),
    ], checks=[assertion_failed(CHECK_IS_BROKEN), None])

    assert result.ok
    assert events.first(PLAN_REVISED) is None, "a broken check sent Plan to rebuild"
    assert len([c for c in tools.calls if c[0] == "check_assertions"]) == 2
    # The traceback came back to the phase that wrote the check, not to Plan.
    assert "AttributeError" in json.dumps(llm.calls[3])


def test_a_check_that_cannot_be_written_lets_review_judge_alone():
    """
    Attempts spent on a check that never runs is not a reason to rebuild a good
    model, and not a reason to lose a finished run either: the phase gives up,
    says so, and everything it failed to cover goes to Review. FCC-76.
    """
    result, events, _, _ = drive([
        plan_reply("Make it", acceptance=ACCEPTANCE),
        code_reply("make()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        assertion_reply("bad_check()"),
        verdict_reply("pass"),
    ], checks=[assertion_failed(CHECK_IS_BROKEN)] * 3)

    assert result.ok
    assert events.first(PLAN_REVISED) is None
    assert events.first(ASSERTION).data["ok"] is None, "an unwritable check is not a verdict"


def test_stop_during_the_checks_does_not_wait_out_three_provider_calls():
    """
    Stop is a decision, not a request. Three attempts at writing the checks is
    long enough that a run which ignored it there would feel hung. FCC-76.
    """
    llm = ScriptedLLMClient([
        plan_reply("Make it", acceptance=ACCEPTANCE),
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
                 cancelled=cancelled)

    assert result.blocked
    assert "Stopped by you" in result.reason
    assert len([c for c in tools.calls if c[0] == "check_assertions"]) == 1
