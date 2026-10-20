"""
Shared fixtures and a scripted LLM/Tool harness for the `test_loop_<phase>`
files that used to live together in one `test_loop.py`.

DESCRIPTION holds what a real Apply hands back: the feature tree with its
parameters and the geometry read off the Shape.

A_CUBE is the smallest part there is, for tests that only care that a Design
turn happened at all.

A_DATUM is a description carrying no value at all: nothing for a Step to cite
and nothing for the checks to measure, so a run over it has neither a coverage
duty nor an Assertion phase. What the prompt tests want, which is a Design turn
and nothing else.

The prompts are wrapped copy; a sentence crosses line breaks. Many tests unwrap
system messages with " ".join(text.split()) before asserting on their content.

The prose about the views is appended after the JSON, past a blank line. Tests
that parse Review's JSON input use .split("\n\n")[0] to discard it.

An empty design_reply() is the sheet answering nothing: the re-read came back
with no value, so what was asked stays unsettled by the drawing.

A_BOX_FROM_WORDS is what Design comes back with when there is nothing to look
at: every value settled by the model, each with the `because` it settled it off,
which is the whole of what a run over an Intent and no drawing has to plan from.

A_READING_THAT_PROVOKES_EVERY_RE_READ is a description that trips every check
the Design phase makes of it at once — a callout of the sheet nobody used, an
outside called rounded and carried by nothing, an axis pointing along the
direction it is placed in, and a number no sheet prints — so that one run
reaches all eight Design call sites.

GEAR, A_NUT, THE_2023_CLAMP and MODEL_MANIA are scenarios several of the split
files run the loop over; kept here, not duplicated, because the same drive()
records the same fake conversation regardless of which file asserts on it.
"""

import ast
import base64
import json
import math
import os
import re
import tempfile
from dataclasses import replace

import pytest

import core.loop.describe as loop_describe

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, CALL_STARTED, COST, ERROR,
    GATE, INTENT, PART, PLAN, PLAN_REVISED, DESIGN, PREFLIGHT_FAILED, QUESTION,
    REVIEW_SUSPECTED, REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL,
    TOOL_RESULT, UNBUILT, WRITING, EventStream, replay,
)
from core import kernel
from core.loop import describe as loop_describe
from core.loop.design import _the_groups
from core.loop.describe import _round_evidence, _the_directions_are_the_drawings

from core.design import part_file
from core.sheets import sheet_reader
from core.freecad_apply import TRAPS
from core.llm.scripted import _replay
from core.llm import REASONING, TEXT, Chunk, ProviderRefused, ScriptedLLMClient, StreamCutOff
from core.design.core import (
    UNDER,
    entry_at,
    said_at,
)
from core.design.merge import merge
from core.design.orientation_checks import (
    pointing,
    read_off_the_wrong_view,
)
from core.design.report import (
    distilled,
    each_disagreement_once,
    render,
)
from core.design.gaps import (
    decided_for_you,
    entries,
)
from core.loop.budgets import Budgets, revisions_ceiling
from core.loop.prompt import _PHASE_VOCABULARY, _language_sections, prompt
from core.nobody import nobody_answering
from core.loop.run import run
from core.tools import ToolError, ToolRegistry
from tests import modelmania_harness


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

DESCRIPTION = {
    "features": [{"name": "Box", "label": "Box", "type": "Part::Box",
                  "parameters": {"Length": "10.0 mm"}}],
    "geometry": {"solids": 1, "surfaces": [{"kind": "Plane", "count": 6}]},
}


def read(value):
    return {"value": value, "flag": "read"}


def settled(value, because):
    """A value nobody supplied: the model chose it and says what off."""
    return {"value": value, "flag": "assumed", "because": because}


A_CUBE = {"envelope": {"length": read("10 mm")}}

A_CUBE_WITH_A_WALL = {"envelope": {"length": read("10 mm"), "thickness": read("2 mm")}}

A_CUBE_WITH_A_HOLE = {**A_CUBE, "features": [{"id": "bore", "kind": "hole",
                                              "position": {"the top face": read("2 mm")}}]}

A_DATUM = {"datums": [{"name": "the left end face", "is": "the flat face at the left"}]}

A_PART = {
    "datums": [{"name": "the left end face", "is": "the flat face at the left"}],
    "envelope": {"length": read("80 mm"), "width": read("40 mm"),
                 "thickness": read("10 mm")},
    "features": [{"id": "hole-8-left", "kind": "Ø8 hole", "topology": "through",
                  "goes_through": True,
                  "axis": read("perpendicular to the top face"),
                  "position": {"the left end face": read("15 mm")},
                  "seen_in": ["top view"]}],
    "not_defined": [{"what": "the material", "looked_in": ["the title block"]}],
}


def plan_reply(*steps, thinking=None, satisfies=None, rollback=None, disputes=None):
    """
    One Plan turn. `satisfies` is what the *first* Step cites. `rollback`,
    given as `{"to": K, "why": "..."}`, is the reply's own field for undoing
    part of the History -- never a Step's prose.

    Coverage is checked over the Plan and not over a Step, so one Step citing
    every entry is a Plan that leaves none of them to nobody — which is what
    most of these tests want, since what they are about is somewhere else.
    """
    written = list(steps)
    if satisfies and written:
        written[0] = (dict(written[0], satisfies=list(satisfies))
                      if isinstance(written[0], dict)
                      else {"do": written[0], "satisfies": list(satisfies)})
    declared = {"steps": written}
    if rollback is not None:
        declared["rollback"] = rollback
    if disputes is not None:
        declared["disputes"] = disputes
    body = json.dumps({"declare": declared})
    return {"reasoning": thinking, "text": body} if thinking else body


def _grown(models):
    """The description as the run holds it: merged, then distilled to the document."""
    grown = {}
    for model in models:
        grown = merge(grown, model)
    return distilled(grown)


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
    one of them ends in a merge into the same model.
    """
    body = json.dumps({"declare": {"design": model or {}, "question": question}})
    return {"reasoning": thinking, "text": body} if thinking else body


def _first_pass_turns(model, attachments, question="", readings=1):
    """
    The scripted replies one `_first_pass` costs today: the same
    `design_reply` repeated *readings* times per page-sized group
    `core._the_groups` cuts *attachments* into, plus one more for the
    reconciling call — or the single reply it always was where nothing
    groups (no attachment, or one `sheet_reader` cannot read).

    Every copy answers alike on purpose: the merge each group's own
    readings end in, and the merge the fan-out itself ends in, are both
    order-independent and idempotent over the same model, so which of the
    concurrent group calls a scripted turn happens to land on does not
    change what the run ends up holding, and a test does not have to
    predict the race to script one.
    """
    groups = _the_groups(list(attachments))
    calls = len(groups) * readings + 1 if groups else 1
    return [design_reply(model, question=question)] * calls


class _MultiTurn(list):
    """A group of separate provider calls, unwrapped by `_flattened` -- not one call's own stream."""


def code_reply(*codes, label="Step", thinking=None, first=1):
    """
    A whole Execute phase: one turn per Step's code, closed by the turn that
    declares it (DAE-A-53) -- no code of its own, because the kernel already
    has it (`execute.md`).

    `first` is the number of the Step the first code belongs to, which is 1 for
    the whole Plan and N for a regeneration of Step N and the tail after it.
    `thinking` rides on the first Step's own turn, the one it was reasoned out in.
    """
    numbers = [first + offset for offset in range(len(codes))]
    turns = _MultiTurn()
    for position, (number, code) in enumerate(zip(numbers, codes)):
        body = json.dumps({"code": code, "step": number, "label": label})
        turns.append({"reasoning": thinking, "text": body}
                     if thinking and position == 0 else body)
    turns.append(json.dumps({"declare": {"steps": [
        {"step": number, "label": label} for number in numbers]}}))
    return turns


def retried_code_reply(*codes, step=1, label="Step", thinking=None):
    """
    One Step, refused and rewritten in the same kernel session as many times
    as *codes* has entries, closed by the turn that declares it:
    the Document's own refusal of a code turn does not end the phase, so
    every code here is a turn of the one Execute call this scripts, not a
    fresh call the way `code_reply`'s own turns are for distinct Steps.
    """
    turns = _MultiTurn()
    for position, code in enumerate(codes):
        body = json.dumps({"code": code, "step": step, "label": label})
        turns.append({"reasoning": thinking, "text": body}
                     if thinking and position == 0 else body)
    turns.append(json.dumps({"declare": {"steps": [{"step": step, "label": label}]}}))
    return turns


def assertion_reply(uncovered=()):
    """The Assertion phase's turn: an expression for each of the first sixty-four entries it is sent, bar the numbered ones it could not check."""
    checks = [{"entry": n, "uncovered": "cannot be measured"} if n in uncovered
              else {"entry": n, "expr": "True"} for n in range(64)]
    return json.dumps({"declare": {"checks": checks}})


def obsolete_reply(reason):
    return json.dumps({"declare": {"obsolete": reason}})


def question_reply(question):
    """Plan answering that it cannot plan yet, and why."""
    return json.dumps({"declare": {"question": question}})


def look_reply(tool, **arguments):
    return json.dumps({"declare": {"look": {"tool": tool, "arguments": arguments}}})


def verdict_reply(verdict, reason="", thinking=None):
    body = json.dumps({"declare": {"verdict": verdict, "reason": reason}})
    return {"reasoning": thinking, "text": body} if thinking else body


def suspect_reply(*suspected, thinking=None):
    """`review_suspect`'s turn: zero or more addresses it wants Assertion to measure."""
    body = json.dumps({"declare": {"suspected": list(suspected)}})
    return {"reasoning": thinking, "text": body} if thinking else body


class FakeTools:
    """
    The registry the loop is given. `applies` decides what apply_step returns,
    one entry per call; anything left over is a success.
    """

    APPLIED = {"status": "applied", "created": ["Box"], "description": DESCRIPTION}

    def __init__(self, applies=(), views=None, checks=(), undo_capacity=None, base=None,
                 faces=()):
        """
        What is in the Document by now. A Snapshot frozen at empty could not
        show that a regeneration is written against the Steps that did apply.
        
        What the end-of-run capture answers. None means a registry that has no
        `review_views` at all, which is the same case as headless.
        
        One entry per `check_assertions` call; anything left over passes.
        
        `undo_capacity` scripts a live Document whose own undo stack has
        already dropped Steps older than it can hold; `None` reports every
        Step ever applied, the same as a stack nothing has spilled off yet.
        """
        self.applies = list(applies)
        self.created = []
        self._applied_groups = []
        self.undo_capacity = undo_capacity
        self.base = base
        self.faces = list(faces)
        self.views = views
        self.checks = list(checks)
        self.calls = []
        self.registry = self._build()

    def _build(self):
        """A None entry, or running off the end, means "this one worked"."""
        registry = ToolRegistry()

        @registry.tool("snapshot", "State right now.")
        def snapshot():
            self.calls.append(("snapshot", {}))
            return dict(SNAPSHOT, objects=[{"name": name} for name in self.created],
                        object_count=len(self.created))

        registry.tool("base_inventory", "The base part, in words.")(
            lambda: {"inventory": self.base})

        @registry.tool("base_addresses", "The face addresses of the base part and of the Body now.")
        def base_addresses():
            self.calls.append(("base_addresses", {}))
            return self.faces.pop(0) if self.faces else {"base": [], "now": []}

        @registry.tool("overall_size", "How big the part came out.")
        def overall_size():
            self.calls.append(("overall_size", {}))
            return {"size_mm": [40, 30, 10]}

        @registry.tool("document_copy", "The Document saved for a phase kernel to open.")
        def document_copy():
            self.calls.append(("document_copy", {}))
            return {"path": None}

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

        @registry.tool("materials", "The material cards this build has, with their uuids.", {
            "type": "object", "properties": {"name": {"type": "string"}},
        })
        def materials(name=None):
            self.calls.append(("materials", {"name": name}))
            return {"materials": [{"name": "Steel-Generic", "uuid": "b9b3a6ff-1c8e",
                                   "density": "7900.00 kg/m^3"}], "count": 1}

        @registry.tool("apply_step", "Apply one Step.", {
            "type": "object",
            "properties": {"code": {"type": "string"}, "label": {"type": "string"},
                          "op": {"type": "string"},
                          "kernel_result": {"type": "object"},
                          "design": {"type": "object"},
                          "plan_values": {"type": "object"}},
            "required": ["code"],
        })
        def apply_step(code, label="Step", op=None, kernel_result=None, design=None,
                       plan_values=None):
            """A None entry, or running off the end, means "this one worked"."""
            arguments = {"code": code, "label": label}
            if op is not None:
                arguments["op"] = op
            if design is not None:
                arguments["design"] = design
            if plan_values is not None:
                arguments["plan_values"] = plan_values
            self.calls.append(("apply_step", arguments))
            queued = self.applies.pop(0) if self.applies else None
            result = queued if queued is not None else dict(self.APPLIED)
            if result.get("status") == "applied":
                group = list(result.get("created", ()))
                self._applied_groups.append(group)
                self.created.extend(group)
            return result

        @registry.tool("undo_step", "Undo the last Step's transaction.")
        def undo_step():
            self.calls.append(("undo_step", {}))
            if self._applied_groups:
                for name in self._applied_groups.pop():
                    self.created.remove(name)
            return {"object_count": len(self.created)}

        @registry.tool("undo_depth", "How many Steps could still be undone.")
        def undo_depth():
            self.calls.append(("undo_depth", {}))
            depth = len(self._applied_groups)
            if self.undo_capacity is not None:
                depth = min(depth, self.undo_capacity)
            return {"depth": depth}

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


def drifted(traceback):
    return {"status": "failed", "traceback": traceback, "drift": True}


class _SecondPass(str):
    """A scripted Design turn that is the run's own second pass, so none is added ahead of it."""


def second_pass_reply(model=None):
    return _SecondPass(design_reply(model))


def _asks_a_question(turn):
    """Whether a scripted Design turn declares a `question` for a person."""
    body = turn.get("text") if isinstance(turn, dict) else turn
    try:
        return bool(json.loads(body)["declare"].get("question"))
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def _describes_a_part(turn):
    """Whether a scripted turn is a Design turn — the phase every run opens with."""
    body = turn.get("text") if isinstance(turn, dict) else turn
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            if "design" in parsed:
                return True
            if "declare" in parsed and isinstance(parsed["declare"], dict):
                return "design" in parsed["declare"]
        return False
    except (TypeError, ValueError):
        return False


class StubKernel:
    """
    A double for the kernel PROCESS `run_phase` talks to -- one layer in from
    the Document-mutation boundary `FakeTools`' fake `apply_step` already
    fakes. Both let a level-1 test assert on the loop's own control flow
    (retries, budgets, event order, Ledger text) without a level-1 test
    having to pay for, or trust, real geometry; that trust is what the
    `@pytest.mark.freecad` tests (this file's `_the_clamp_as_it_was_read`
    callers, `test_the_document_reaches_its_phases.py`,
    `test_vertical_slice_level2.py`, `test_apply_level2.py`) carry instead,
    against a real `Kernel`.

    It proves nothing about whether a Step's code is valid Python or valid
    FreeCAD -- every call after `fails` is exhausted answers `ok=True`
    regardless of what the code actually says. A test that needs to know
    whether code runs belongs on a real kernel, not this one.

    `fails` is the same shape as `OneFailingKernel`
    (`tests/core/agent/test_a_code_turn_is_visible.py`): one `KernelResult`
    per call that should not land, in order, so a retry a test drives is a
    real rejection this double answered with, not a shape asserted on
    because nothing produced it. It returns canned answers rather than a
    simplified working kernel, which makes it a Stub; recording every call
    it saw in `.calls` makes it a spy too, but Stub is what it is for.
    """

    def __init__(self, fails=(), plan_values=None):
        self.calls = []
        self._fails = list(fails)
        self.plan_values = dict(plan_values or {})

    def check(self, code, **kwargs):
        self.calls.append(code)
        if kwargs.get("capture_values"):
            namespace = dict(self.plan_values, math=__import__("math"))
            try:
                exec(code, namespace)  # noqa: S102
            except Exception:
                pass
            self.plan_values.update(
                {name: value for name, value in namespace.items()
                 if isinstance(value, (int, float)) and not isinstance(value, bool)})
        if self._fails:
            return self._fails.pop(0)
        return kernel.KernelResult(ok=True, checkpoint=_scripted_checkpoint(),
                                   plan_values=dict(self.plan_values))


_SCRIPTED_CHECKPOINT = None


def _scripted_checkpoint():
    """One placeholder file every `StubKernel` hands out, copied, never opened."""
    global _SCRIPTED_CHECKPOINT
    if _SCRIPTED_CHECKPOINT is None:
        handle, _SCRIPTED_CHECKPOINT = tempfile.mkstemp(suffix=".FCStd")
        os.close(handle)
    return _SCRIPTED_CHECKPOINT


def _flattened(turns):
    """
    `code_reply()` hands back a whole Execute phase as several turns; unwrap
    it. A bare list is left alone -- it is one call's own stream, an
    Exception anywhere in it raised there (`ScriptedLLMClient._replay`).
    """
    flat = []
    for turn in turns:
        if isinstance(turn, _MultiTurn):
            flat.extend(turn)
        else:
            flat.append(turn)
    return flat


def _is_the_second_pass(call):
    content = call[-1]["content"]
    return isinstance(content, list) and "Answer from the drawing: measure" in content[-1].get("text", "")


class _Provider(ScriptedLLMClient):
    """
    The scripted provider, whose `calls` leave out the second Design pass every
    run with a drawing makes: a test that counts or indexes turns is about the
    phase it names. `all_calls` has them all.
    """

    def __init__(self, turns=()):
        super().__init__(turns)
        self.all_calls = []

    def stream(self, messages, **overrides):
        self.all_calls.append(messages)
        if _is_the_second_pass(messages):
            with self._lock:
                if not self.turns:
                    raise AssertionError("ScriptedLLMClient ran out of turns on the second pass")
                turn = self.turns.pop(0)
            return _replay(turn, len(self.all_calls))
        return super().stream(messages, **overrides)


def scripted(turns, attachments=(), resuming=False):
    """
    The provider, turn by turn, with the Design turn every run opens with.

    Design runs on every run, drawing or no drawing, so `llm.calls[0]`
    is always Design — read a phase's requests with `_turns_of` rather than by
    counting them. A script that opens with something else is given a Design
    turn that describes nothing, which is what a test about Plan or Execute
    means by not writing one. A run with a drawing has always scripted its own
    and is left alone, because a Design reply in the wrong shape — the case
    those tests are about — is not one this could recognise.

    A run with a drawing goes back to it once after the first pass, so a script
    that opens with a Design turn and no question is given an empty second one.

    `resuming` is a run given a prior Part and no new attachment:
    Design does not run at all, so nothing is inserted ahead of the script.
    """
    turns = _flattened(turns)
    if turns and not attachments and not resuming and not _describes_a_part(turns[0]):
        turns.insert(0, design_reply())
    if (turns and attachments and _describes_a_part(turns[0]) and not _asks_a_question(turns[0])
            and not any(isinstance(turn, _SecondPass) for turn in turns)):
        turns.insert(1, design_reply())
    return _Provider(turns)


def needs_drawing(*paths):
    """The `sheet_cache` guard (`tests/conftest.py`), for a run given a drawing directly."""
    for path in paths:
        if not os.path.exists(path):
            pytest.skip("drawing not found: %s; set DAE_MODELMANIA_DIR" % path)


def drive(turns, applies=(), budgets=None, intent="Make a 10mm box", ask=None,
          views=None, checks=(), attachments=(), decide=None, outstanding=(),
          the_part_path=None, kernel=None, undo_capacity=None, base=None, faces=()):
    """Run the loop over scripted turns and hand back everything to assert on."""
    needs_drawing(*attachments)
    llm = scripted(turns, attachments, resuming=bool(the_part_path))
    tools = FakeTools(applies, views=views, checks=checks, undo_capacity=undo_capacity,
                      base=base, faces=faces)
    events = EventStream()
    kwargs = {"ask": ask} if ask is not None else {}
    if decide is not None:
        kwargs["decide"] = decide
    if the_part_path is not None:
        kwargs["the_part_path"] = the_part_path
    result = run(intent, llm=llm, tools=tools.registry, events=events,
                 budgets=budgets or Budgets(), attachments=list(attachments),
                 outstanding=list(outstanding), kernel=kernel or StubKernel(), **kwargs)
    return result, events, llm, tools


def prompts_of(phase):
    """The phase's system prompt as a run with a person to ask sends it, and as a batch run does, edit or not."""
    prompts = [prompt(phase), prompt(phase, editing=True)]
    with nobody_answering():
        return tuple(prompts + [prompt(phase), prompt(phase, editing=True)])


def _system_messages(llm):
    """The system message of every turn, in order, as the provider saw it."""
    return [call[0]["content"] for call in llm.calls]


def _turns_of(llm, phase):
    """
    Every request body that phase was sent, in order — one entry per provider call.

    Matched by prefix, not equality: Execute's system prompt grows a ledger
    section past the static file once this run has a failure to carry forward,
    and the bare `prompt(phase)` this compares against is always
    that section's prefix.
    """
    systems = prompts_of(phase)
    return [json.loads(call[-1]["content"]) for call in llm.calls
            if call[0]["content"].startswith(systems)]


def _execute_attempts(llm):
    """
    The task Execute was given, once per call to `execute()` in this run --
    not the kernel's own feedback on a code turn inside one (DAE-A-53): that
    request carries `ok`, the task that opens a call carries `steps`, and a
    phase with N Steps written one at a time is N+1 requests for one attempt.
    """
    return [turn for turn in _turns_of(llm, "execute") if "steps" in turn]


MODEL_MANIA = str(modelmania_harness.MODELMANIA_DIR / "mm25-p1-1.pdf")


THE_2023_CLAMP = modelmania_harness.the_2023_clamp_as_read()


def _the_clamp_as_it_was_read():
    """The recorded run of the 2023 pipe clamp, down to the two files it leaves."""
    _, events, _, _ = drive([
        design_reply(THE_2023_CLAMP),
        plan_reply("Build the clamp", satisfies=acceptance_of(THE_2023_CLAMP)),
        code_reply("clamp()"),
        suspect_reply(),
        assertion_reply(),
        verdict_reply("pass"),
    ], budgets=Budgets(rereads=0), intent="Build the 2023 pipe clamp")

    written = events.last(PART).data["path"]
    return (part_file.read(written), part_file.read(part_file.evidence(written)),
            part_file.asked_for(written))


def _occupied_by(node, fields):
    """
    The characters *fields* occupy on the part the document is made of.

    Nothing of a feature a later page deletes, because the document does not
    carry one either, and what it does not carry it cannot have dropped.
    """
    if isinstance(node, dict):
        return sum(len(json.dumps(value, ensure_ascii=False)) if key in fields
                   else _occupied_by(value, fields) for key, value in node.items())
    if isinstance(node, list):
        return sum(_occupied_by(entry, fields) for entry in node
                   if not (isinstance(entry, dict) and entry.get("superseded_by")))
    return 0


READ_AGAIN_OFF_THE_LATER_PAGE = "read again off the later page"


THE_HOLE_THAT_CANNOT_BE_BOTH = dict(
    A_PART,
    features=[dict(A_PART["features"][0],
                   position={"the left end face": read("15 mm"),
                             "the right end face": read("70 mm")})],
    conflicts=[{"about": "features \u2192 hole-8-left \u2192 position",
                "stands": "15 mm from the left end face",
                "was_also_said": "70 mm from the right end face, on a part 80 mm long"},
               {"about": "features \u2192 hole-8-left \u2192 position",
                "stands": "15 mm from the left end face",
                "was_also_said": "the two ends do not meet on an 80 mm part"}],
)


THE_LEFT_LUG_PLANE = "the leftmost tangent plane of the left lug"


THE_ARMS_FREE_END = "the angled free-end face of the left arm"


MEASURED_FROM_A_DELETED_FACE = [
    "envelope \u2192 length between %s and the rightmost end face" % THE_LEFT_LUG_PLANE,
    "features \u2192 outer-profile \u2192 position \u2192 %s" % THE_LEFT_LUG_PLANE,
    "features \u2192 outer-profile \u2192 position \u2192 the rightmost end face",
    "features \u2192 outer-profile \u2192 position \u2192 the ring axis",
    "features \u2192 ring-bore \u2192 position \u2192 %s" % THE_LEFT_LUG_PLANE,
    "features \u2192 ring-outer-cylinder \u2192 position \u2192 %s" % THE_LEFT_LUG_PLANE,
    "features \u2192 left-arm-slot \u2192 position \u2192 the left-lug-hole axis",
]


RE_MEASURED = [
    "envelope \u2192 length between %s and the rightmost end face" % THE_ARMS_FREE_END,
    "features \u2192 outer-profile \u2192 position \u2192 %s" % THE_ARMS_FREE_END,
    "features \u2192 outer-profile \u2192 position \u2192 the rightmost end face",
    "features \u2192 outer-profile \u2192 position \u2192 the ring axis",
    "features \u2192 ring-bore \u2192 position \u2192 %s" % THE_ARMS_FREE_END,
    "features \u2192 ring-outer-cylinder \u2192 position \u2192 %s" % THE_ARMS_FREE_END,
    "features \u2192 left-arm-slot \u2192 position \u2192 the left-arm-slot axis",
]


def _asked_for_phase_1(reading):
    """
    The same reading under an Intent that asks for Phase 1: the second page's
    features never become the part, so nothing is superseded.
    """
    replacing = {feature["superseded_by"] for feature in reading["features"]
                 if feature.get("superseded_by")}
    return dict(reading, features=[
        {field: said for field, said in feature.items() if field != "superseded_by"}
        for feature in reading["features"] if feature["id"] not in replacing])


THE_LEFT_LUGS_OVERALL = "envelope \u2192 length between %s and the rightmost end face" \
    % THE_LEFT_LUG_PLANE


NAMED_WHAT_ENDED_THE_RUN = [
    THE_LEFT_LUGS_OVERALL,
    "envelope \u2192 Phase 2 finished-part length",
    "features \u2192 angled-left-arm \u2192 73 mm extent",
    "features \u2192 angled-left-arm \u2192 73 mm endpoint",
]


ANSWERED_BEFORE_IT_DISTILLED = {
    "the exact lengthwise position and complete dimensional profile of the arm cutout":
        "features \u2192 arm-cutout \u2192 profile",
    "the width of the ring split and the meaning of the separate 1 mm and 2 mm "
    "callouts at the split": "features \u2192 ring-split \u2192 width",
}


NOBODY_PRINTED_A_TOLERANCE = {"what": "general dimensional tolerances",
                              "looked_in": ["notes, page 1", "title block, page 1"]}


def _under_the_heading(shown, heading):
    """The lines one section of the rendered part is written on."""
    lines = shown.split("\n")
    written = []
    for line in lines[lines.index(heading) + 1:]:
        if not line.startswith(" "):
            break
        written.append(line)
    return written


def _reaches(function, called):
    """Whether *function* names `part_file.<called>` anywhere in its body."""
    return any(isinstance(node, ast.Attribute) and node.attr == called
               and isinstance(node.value, ast.Name) and node.value.id == "part_file"
               for node in ast.walk(function))


A_BORE_AND_THE_EVIDENCE_FOR_IT = {
    "envelope": {"length": read("108 mm")},
    "features": [{"id": "ring-bore", "kind": "\u00d842 bore", "topology": "through",
                  "axis": {"value": "the depth", "flag": "derived",
                           "from": "the front elevation's line work: the ring is "
                                   "drawn round at 42 across",
                           "round_in": "the front elevation"},
                  "position": {"the left end face": read("65.5 mm")},
                  "seen_in": ["the front elevation", "the upper plan"]}],
}


A_CLAMP_ENVELOPE = ["envelope → length between the ends: 108 mm (read)",
                    "envelope → depth front to rear: 30 mm (read)",
                    "envelope → height of the right lug: 30 mm (read)"]


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
    return json.dumps({"declare": {"steps": [
        {"do": "Pad the outer profile in the plane of the length and the height",
         "satisfies": ["envelope → length", "features → outer-profile → plane"]},
        {"do": "Pad the lug in the plane of the length and the depth",
         "satisfies": ["features → right-mounting-lug → plane"]},
    ]}})


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


THE_CLAMP_READ_PROPERLY = {
    "envelope": {"length": {"value": "108 mm", "flag": "read"}},
    "features": [dict(THE_CLAMP_READ_OFF_ONE_VIEW["features"][1],
                      axis={"value": "the height", "flag": "read",
                            "round_in": "the upper plan"})],
}


def _user_blocks(llm, at=0):
    """
    Every content block a call's user turns carried, in order — one turn on a
    first pass, two (the original evidence, then the short nudge) on a retry
    that went out as a second turn of the conversation.
    """
    blocks = []
    for message in llm.calls[at]:
        if message["role"] != "user":
            continue
        content = message["content"]
        blocks.extend(content if isinstance(content, list) else [{"type": "text", "text": content}])
    return blocks


def _pictures_shown(llm, at=0):
    return [block for block in _user_blocks(llm, at) if block.get("type") == "image_url"]


def _said_to_design(llm, at=0):
    return "\n".join(block.get("text", "") for block in _user_blocks(llm, at)
                      if block.get("type") == "text")


def _asked_of_design(llm):
    """
    The body of every Design turn, in order. Not `_turns_of`: a Design request
    is content blocks rather than one string, because the sheet rides with it,
    and the body is the last of them — the evidence goes out first, so it is a
    stable prefix a repeated Design turn can cache (`core/loop.py :: design`).
    """
    return [json.loads(call[-1]["content"][-1]["text"]) for call in llm.all_calls
            if call[0]["content"].startswith(prompts_of("design"))]


THE_PIPE_CLAMP = str(modelmania_harness.SHEETS[
    "model-mania_-in-person-2023 copy.pdf"])


THE_2023_SHEET = str(modelmania_harness.MODELMANIA_DIR / "model-mania_-in-person-2023 copy.pdf")


A_READING_THAT_PROVOKES_EVERY_RE_READ = {
    "datums": [{"name": "the ring axis", "fixes": "the height",
                "is": "the centreline of the clamp body"}],
    "envelope": {"length": read("150 mm"), "depth front to rear": read("777 mm")},
    "features": [{"id": "clamp-bore", "kind": "\u00d842 bore", "topology": "through",
                  "axis": read("along the height"),
                  "position": {"the ring axis": read("coincident")},
                  "seen_in": ["the plan"]}],
    "not_defined": [{"what": "the rounded ends of the outside",
                     "looked_in": ["the plan"]}],
}


def _a_run_that_reads_the_sheet_every_way(sheet, intent):
    """
    One run that reaches all nine Design turns.

    The first pass at the sheet, the re-read Design puts its own question
    back to before a person, the person's answer written in once that comes
    back empty, the five re-reads this reading provokes — a callout nobody
    used, the outer contour asked and then settled, an axis pointing along
    the direction it is placed in, a 777 mm nothing prints — and the
    question Plan puts back to the drawing. `rereads=1` is what fires each
    of them exactly once: every one of them looks again until it stops
    finding fewer.
    """
    return drive([design_reply(A_READING_THAT_PROVOKES_EVERY_RE_READ,
                               question="How thick is the pad under the arm?")]
                 + [design_reply({})] * 7
                 + [question_reply("Which end does the slot open at?"),
                    design_reply({}),
                    plan_reply("Build it",
                               satisfies=everything(A_READING_THAT_PROVOKES_EVERY_RE_READ)),
                    code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                 intent=intent, attachments=[sheet], ask=lambda question: "12 mm",
                 budgets=Budgets(rereads=1, readings=1))


A_BOX_FROM_WORDS = {
    "datums": [{"name": "the base", "is": "the face the box stands on",
                "fixes": "the height",
                "proves": "10 mm to the top face, matching the overall height"}],
    "envelope": {"length": settled("10 mm", "the Intent asks for a 10 mm box"),
                 "width": settled("10 mm", "a box named by one size is square"),
                 "height": settled("10 mm", "a box named by one size is square")},
}


def _a_run_with_no_drawing():
    """A whole run over an Intent and nothing else, through to a verdict; its three envelope sizes need no model-written check."""
    return drive([design_reply(A_BOX_FROM_WORDS), plan_reply("Pad a 10 mm box"),
                  code_reply("box()"), suspect_reply(), verdict_reply("pass")])


def a_refusal():
    return ProviderRefused(
        "gemini/gemini-2.5-pro refused the request: your quota for this model is exhausted. "
        "The provider says to retry in 26s.",
        said='{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}',
    )


def _cut_off():
    """The gateway closing the stream mid-reply, as the client reports it."""
    return [Chunk(REASONING, "Reading the Intent."),
            StreamCutOff("openai/kimi-k3 closed the stream before it answered.")]


class _OnASubscription(ScriptedLLMClient):
    """
    The scripted provider wearing the attributes `ClaudeCodeClient` carries
    and `ScriptedLLMClient` does not: what each call spent, and the model.
    """

    def __init__(self, turns):
        super().__init__(turns)
        self.model = "sonnet"

    def stream(self, messages, **overrides):
        self.last_cost = {"usd": 0.0049, "model": "sonnet"}
        return super().stream(messages, **overrides)


def on_a_subscription(turns):
    return _OnASubscription(scripted(turns).turns)


def a_clean_run(llm):
    """One run over *llm* with nothing wrong in it, as what it produced."""
    events = EventStream()
    result = run("Make a box", llm=llm, tools=FakeTools().registry, events=events,
                 kernel=StubKernel())
    return result, events


CLEAN = [plan_reply("Only"), code_reply("box()"), suspect_reply(), verdict_reply("pass")]


GEAR = "модельку шестерні"


MISSING = "I need the module, the tooth count, the thickness and the bore diameter."


GEAR_AS_TOLD = {"features": [{"id": "teeth", "kind": "20 teeth of module 2",
                              "position": {"the blank axis": {"value": "20 teeth",
                                                              "flag": "assumed"}}}]}


SETTLED = {
    "envelope": {"length": read("80 mm")},
    "features": [{"id": "outer-profile", "kind": "obround outer profile",
                  "radius": {"value": "25 mm", "flag": "assumed",
                             "because": "half the 50 mm height, from the silhouette"}}],
}


A_MATERIAL_CARD = ("Provide the UUID of an installed plain carbon steel material card, or "
                   "make the materials lookup tool available.")


THROUGH_OR_NOT = ("Does the hourglass pocket go through the 150 mm length, or is it "
                  "a set depth? And which edges take the R2 fillet?")


OFF_THE_SECTION = {"features": [
    {"id": "pocket-front", "kind": "hourglass pocket", "topology": "blind",
     "position": {"the front face": {"value": "20 mm deep", "flag": "read"}},
     "seen_in": ["SECTION A-A"]},
]}


A_NUT = {"envelope": {"across the flats": read("13.0 mm"), "thickness": read("6.5 mm")}}


ACCEPTANCE = acceptance_of(A_NUT)


def a_run_over_the_nut(*turns, **kwargs):
    """The nut described, then whatever the test scripts after it."""
    return drive([design_reply(A_NUT)] + list(turns),
                 attachments=[RENDER_PNG], **kwargs)


def planning_the_nut(*steps):
    return plan_reply(*steps, satisfies=everything(A_NUT))


CHECKS = ("flats = [f for f in doc.Objects[0].Shape.Faces]\n"
          "assert abs(13.0 - across(flats)) < 1e-6, '13.0 mm across the flats'\n")


def assertion_failed(entries):
    """What the check tool answers when the expressions of these entry numbers came out False."""
    return {"ok": True, "held": [], "failed": list(entries), "defects": []}


SELF_INTERSECTION_HELD = {"ok": True}


ASSERT_FAILED = [0]


CHECK_IS_BROKEN = ('Traceback (most recent call last):\n'
                   '  File "<step>", line 1, in <module>\n'
                   "AttributeError: 'Part::Feature' object has no attribute 'Flats'\n")


THE_CAVITY = {
    "envelope": {"depth front to rear": read("100 mm")},
    "features": [{"id": "central-opening", "kind": "hourglass pocket",
                  "topology": "two opposed blind pockets, 10 mm web",
                  "position": {"the front face": read("20 mm")},
                  "seen_in": ["front view", "SECTION A-A", "SECTION B-B"]}],
}


CAVITY_DEPTH = "envelope → depth front to rear"


CAVITY_POCKET = "features → central-opening → position → the front face"


THE_BRACKET = {
    "envelope": {"length": read("150 mm"), "depth front to rear": read("100 mm"),
                 "height": read("40 mm"), "width": read("60 mm")},
    "features": [{"id": "central-bore", "kind": "Ø8 vertical bore",
                  "position": {"the front face": read("20 mm")}}],
}


BUILT = everything(THE_BRACKET)[:4]


LEFT_TO_BUILD = everything(THE_BRACKET)[4:]


def _four_steps_applied_then_a_handback():
    """
    The scripted turns of the recorded run, up to the revision.

    Steps 1 to 4 apply, Step 5 cannot be built, and the revision plans the work
    that is left and nothing else.
    """
    return [
        design_reply(THE_BRACKET),
        plan_reply(*[{"do": "Pad part %d" % (number + 1), "satisfies": [entry]}
                     for number, entry in enumerate(BUILT)],
                   {"do": "Bore the central hole", "satisfies": list(LEFT_TO_BUILD)}),
        *[json.dumps({"code": "part%d()" % number, "step": number, "label": "Part %d" % number})
          for number in range(1, 5)],
        json.dumps({"declare": {"steps": [{"step": number, "label": "Part %d" % number}
                               for number in range(1, 5)],
                    "unbuildable": "the bore misses the web as planned"}}),
        plan_reply({"do": "Bore the central hole from the top face",
                    "satisfies": list(LEFT_TO_BUILD)}),
        code_reply("bore()", first=5),
        suspect_reply(), assertion_reply(), verdict_reply("pass"),
    ]


SIX_FAILED = [0, 1]


MIXED_TRACEBACK = ('Traceback (most recent call last):\n'
                   '  File "<step>", line 3, in <module>\n'
                   "AssertionError: these did not hold: envelope \u2192 thickness: 6.5 mm "
                   "(read)\n")


def assertion_mixed(held, failed):
    """The worker's own reply for expressions of which some came out True and others False."""
    return {"ok": True, "held": list(held), "failed": list(failed), "defects": []}


STILL_WRONG = ["envelope \u2192 across the flats: 13.0 mm (read)"]


NO_MATERIAL = ("Traceback (most recent call last):\n"
               "RuntimeError: this Step added a feature that neither removed nor added "
               "any material:\nPocketHook (PartDesign::Pocket): 421045.77 mm3 before, "
               "421045.77 mm3 after\n")


IN_PIECES = ("Traceback (most recent call last):\n"
             "RuntimeError: this Step left a Body in more than one piece:\n"
             "Body: 5 separate solids\n")


NO_SUCH_PROPERTY = ("Traceback (most recent call last):\n"
                    "RuntimeError: this Step did not build the operation the Plan named:\n"
                    "Name: OuterProfile (Sketcher::SketchObject) has no such property: "
                    "TangentLength\n")


FILLET_RADIUS = ("Traceback (most recent call last):\n"
                 "RuntimeError: recompute left these objects in error:\n"
                 "EndFillets_R12_5 (PartDesign::Fillet): BRep_API: command not done\n")


NO_ENUM = ("Traceback (most recent call last):\n"
           "ValueError: 'Foo' is not part of the enumeration\n")


def gate_that(*answers):
    """A Gate scripted turn by turn; anything past the script approves once."""
    from core.loop.gate import APPROVE, Approval

    queued = list(answers)
    asked = []
    calls = []

    def gate(steps, **kwargs):
        asked.append(list(steps))
        calls.append(dict(steps=list(steps), **kwargs))
        return queued.pop(0) if queued else Approval(APPROVE)

    gate.asked = asked
    gate.calls = calls
    return gate


def driven_through(gate, turns, **kwargs):
    llm = scripted(turns, kwargs.get("attachments", ()))
    tools = FakeTools(kwargs.pop("applies", ()), views=kwargs.pop("views", None),
                      checks=kwargs.pop("checks", ()))
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                 budgets=kwargs.pop("budgets", None) or Budgets(), gate=gate,
                 kernel=kwargs.pop("kernel", None) or StubKernel(), **kwargs)
    return result, events, llm, tools


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
    suspect_reply(),
    assertion_reply(),
    verdict_reply("pass"),
]


def _a_sheet_that_contradicts_itself(tmp_path, wide=98.2, printed=100.0):
    """
    A traced plate that does not measure what is printed across it.

    1.8 mm by default, and that is not a free choice: the tracing has to be
    near enough to the drawing for a dimension's foot to find the face it names
    at all. Past `sketch_check.A_FOOT` the foot reaches nothing, and there is
    no comparison to make — see the test below, which holds that ceiling in
    place on purpose.
    """
    corners = [(0.0, 0.0), (wide, 0.0), (wide, 30.0), (0.0, 30.0)]
    entities = [
        sheet_reader.Entity(kind="line", source="traced", role="contour",
                            size=math.dist(corners[at], corners[(at + 1) % 4]),
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


def _a_sheet_of_many_outlines(tmp_path, how_many=4):
    """One view holding several traced plates, so a budget has something to run out of."""
    sheet = _a_sheet_that_contradicts_itself(tmp_path)
    drawn = sheet.drawn[0]
    return sheet_reader.Sheet(
        source=sheet.source,
        drawn=[sheet_reader.Drawn(view=drawn.view, entities=drawn.entities,
                                  outlines=drawn.outlines * how_many)],
        between=sheet.between)


class _AChildThatCannotComeBack(kernel.Kernel):
    """
    A worker that came up once and can never be started again.

    Not a fake reading: `_ask` is the real one, so what the sweep sees is the
    parent's own answer to a restart that failed.
    """

    def __init__(self):
        super().__init__(freecadcmd="/nonexistent/freecadcmd")
        self.came_up = 0

    def start(self):
        self.came_up += 1
        if self.came_up > 1:
            raise RuntimeError("Preflight Worker did not come up: ")


def _never_called(*_, **__):
    raise AssertionError("the reading was built in the addon's own process")


THE_CLAMP_POINTED_AT_THE_WRONG_VIEW = {
    "envelope": {"length": read("108 mm")},
    "features": [
        {"id": "clamp-bore", "kind": "\u00d842 bore through the clamp ring",
         "topology": "through", "diameter": read("42 mm"),
         "axis": {"value": "the depth", "flag": "read",
                  "round_in": "the front elevation"}},
    ],
}


TRACED_IN_THE_PLAN = {"the upper plan": [42.0]}


_THE_BORE_PUT_RIGHT = [
    design_reply(THE_CLAMP_POINTED_AT_THE_WRONG_VIEW),
    design_reply(),
    plan_reply("Bore the ring",
               satisfies=everything(THE_CLAMP_POINTED_AT_THE_WRONG_VIEW)),
    code_reply("bore()"),
    suspect_reply(),
    assertion_reply(),
    verdict_reply("pass"),
]


def _traced(monkeypatch, round_here):
    """What the tracer saw curved, view by view, whatever the attachment is."""
    monkeypatch.setattr(loop_describe, "_round_evidence", lambda attachments: dict(round_here))


THE_CLAMP_READ_OFF_THE_WRONG_VIEW = {
    "datums": [{"name": "the front face", "fixes": "the depth",
                "is": "the front face of the clamp body"},
               {"name": "the longitudinal centre plane", "fixes": "the depth",
                "is": "the plane halfway through the depth"}],
    "envelope": {"length": read("108 mm")},
    "features": [
        {"id": "right-mounting-hole", "kind": "Ø6.6 through hole",
         "topology": "through", "diameter": read("6.6 mm"),
         "axis": {"value": "perpendicular to the front face, along the depth",
                  "flag": "read", "round_in": "the front elevation"},
         "position": {"the front face": read("12.5 mm"),
                      "the longitudinal centre plane": read("coincident")}},
        {"id": "left-mounting-hole", "kind": "Ø6.6 through hole",
         "topology": "through", "diameter": read("6.6 mm"),
         "axis": {"value": "perpendicular to the front face, along the depth",
                  "flag": "read", "round_in": "the front elevation"},
         "position": {"the front face": read("12.5 mm"),
                      "the longitudinal centre plane": read("coincident")}},
        {"id": "clamp-bore", "kind": "Ø42 bore through the clamp ring",
         "topology": "through", "diameter": read("42 mm"),
         "axis": {"value": "perpendicular to the front face, along the depth",
                  "flag": "read", "round_in": "the front elevation"},
         "position": {"the front face": read("25 mm"),
                      "the longitudinal centre plane": read("coincident")}},
        {"id": "ring-split", "kind": "saw cut through the clamp ring",
         "topology": "open split through to the right end face",
         "axis": read("perpendicular to the front face, along the depth"),
         "position": {"the front face": read("54 mm"),
                      "the longitudinal centre plane": read("coincident")}},
    ],
}


TRACED_ROUND_IN_THE_UPPER_PLAN = {"the upper plan": [6.6, 6.6, 42.0]}


THE_VIEW_LOOKED_AT_AGAIN = {"features": [
    {"id": feature["id"],
     "axis": {"value": "along the height", "flag": "traced",
              "round_in": "the upper plan"}}
    for feature in THE_CLAMP_READ_OFF_THE_WRONG_VIEW["features"]]}


def _re_read_as(monkeypatch, fragment):
    """The re-read a provider would have replied with, without a provider."""
    monkeypatch.setattr(loop_describe, "design",
                        lambda body, llm, events, attachments=(): (fragment, []))


def _looked_at_the_view_again(monkeypatch, part, fragment):
    _traced(monkeypatch, TRACED_ROUND_IN_THE_UPPER_PLAN)
    _re_read_as(monkeypatch, fragment)
    return _the_directions_are_the_drawings(
        json.loads(json.dumps(part)), "Make the clamp", [], None, EventStream(),
        Budgets(rereads=2), lambda: False)


A_BRACKET_IN_STEEL = {"envelope": {"length": read("80 mm")},
                      "material": read("plain carbon steel")}


THE_LENGTH = "envelope \u2192 length: 80 mm (read)"


THE_MATERIAL = "material: plain carbon steel (read)"


NO_CARD = "no material card in this build carries plain carbon steel"


def a_run_that_cannot_assign_the_material(**kwargs):
    """The part padded and the material Step left out, with a reason."""
    return drive([
        design_reply(A_BRACKET_IN_STEEL),
        plan_reply({"do": "Pad the outline 80 mm long", "satisfies": [THE_LENGTH]},
                   {"do": "Assign plain carbon steel to the Body",
                    "satisfies": [THE_MATERIAL]}),
        json.dumps({"code": "pad()", "step": 1, "label": "Pad"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Pad"}],
                    "unbuildable": NO_CARD}}),
        suspect_reply(),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG], **kwargs)


THE_LUG_FROM_A_PHOTO = {
    "datums": [{"name": "the front face", "fixes": "the depth",
                "is": "the front face of the plate"}],
    "features": [
        {"id": "boss-front", "kind": "cylindrical boss, through",
         "topology": "through",
         "axis": {"value": "perpendicular to the front face, along the depth",
                  "flag": "read", "round_in": "the isometric render"},
         "position": {"the front face": read("0 mm")}},
    ],
}


THE_SAME_PICTURE_LOOKED_AT_AGAIN = {"features": [
    {"id": "boss-front",
     "axis": {"value": "perpendicular to the front face, along the depth",
              "flag": "read", "round_in": "the isometric render"}}]}


CLOSED_OPS = "closed"
