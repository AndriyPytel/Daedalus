"""
Level 1 — the Plan -> Execute -> Review loop. Written first, no FreeCAD present.

The hardest logic in the project, verified the cheapest way: a scripted LLM and
a fake Tool registry. The event stream is an output and is asserted on; it is
never substituted.
"""
import json

import pytest

from core.events import (
    APPLIED, BLOCKED, ERROR, PLAN, PLAN_REVISED, PREFLIGHT_FAILED, RENDER,
    REVIEW_VERDICT, STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, EventStream,
)
from core.llm import ScriptedLLMClient
from core.loop import Budgets, run
from core.tools import ToolRegistry

SNAPSHOT = {"document": "Unnamed", "objects": [], "object_count": 0,
            "active_body": None, "selection": []}


# ── scripted phase replies ────────────────────────────────────────────────────


def plan_reply(*steps, thinking=None):
    body = json.dumps({"steps": list(steps)})
    return {"reasoning": thinking, "text": body} if thinking else body


def code_reply(code, label="Step", thinking=None):
    body = json.dumps({"code": code, "label": label})
    return {"reasoning": thinking, "text": body} if thinking else body


def obsolete_reply(reason):
    return json.dumps({"obsolete": reason})


def verdict_reply(verdict, reason="", thinking=None):
    body = json.dumps({"verdict": verdict, "reason": reason})
    return {"reasoning": thinking, "text": body} if thinking else body


# ── the fake Tool registry ────────────────────────────────────────────────────


class FakeTools:
    """
    The registry the loop is given. `applies` decides what apply_step returns,
    one entry per call; anything left over is a success.
    """

    APPLIED = {"status": "applied", "created": ["Box"],
               "objects": [{"name": "Box", "label": "Box", "type": "Part::Box"}],
               "render": "/tmp/render.png"}

    def __init__(self, applies=()):
        self.applies = list(applies)
        self.calls = []
        self.registry = self._build()

    def _build(self):
        registry = ToolRegistry()

        @registry.tool("snapshot", "State right now.")
        def snapshot():
            self.calls.append(("snapshot", {}))
            return dict(SNAPSHOT)

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

        return registry

    def applied_codes(self):
        return [args["code"] for name, args in self.calls if name == "apply_step"]


def failed(traceback):
    return {"status": "failed", "traceback": traceback}


def drive(turns, applies=(), budgets=None, intent="Make a 10mm box"):
    """Run the loop over scripted turns and hand back everything to assert on."""
    llm = ScriptedLLMClient(turns)
    tools = FakeTools(applies)
    events = EventStream()
    result = run(intent, llm=llm, tools=tools.registry, events=events,
                 budgets=budgets or Budgets())
    return result, events, llm, tools


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
    assert "/tmp/render.png" in asked
    assert "Part::Box" in asked, "Review did not see the final Document state"


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
    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            code_reply("try2()"),
            code_reply("try3()"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
    )

    assert not result.ok
    assert tools.applied_codes() == ["try1()", "try2()", "try3()"]
    blocked = events.first(BLOCKED)
    assert blocked is not None
    assert len(blocked.data["tried"]) == 3
    assert [t["traceback"] for t in blocked.data["tried"]] == ["one", "two", "three"]


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
        budgets=Budgets(attempts_per_step=1),
    )

    assert result.blocked
    assert not result.ok
    assert result.awaiting_user
    assert events[-1].kind == BLOCKED


# ── The event stream ──────────────────────────────────────────────────────────


def test_the_event_stream_of_a_clean_run():
    _, events, _, _ = drive([
        plan_reply("Only", thinking="A box is a Part::Box."),
        code_reply("box()", thinking="addObject then set Length."),
        verdict_reply("pass", "Good.", thinking="The render shows a cube."),
    ])

    assert [e.kind for e in events] == [
        THOUGHT, PLAN,
        STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, APPLIED, RENDER,
        THOUGHT, REVIEW_VERDICT,
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
        TOOL_CALL, TOOL_RESULT, APPLIED, RENDER,
        REVIEW_VERDICT,
    ]


def test_the_event_stream_of_a_blocked_run():
    _, events, _, _ = drive(
        [plan_reply("Only"), code_reply("x()")],
        applies=[failed("boom")],
        budgets=Budgets(attempts_per_step=1),
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


def test_the_render_review_judged_by_is_in_the_stream():
    _, events, _, _ = drive([
        plan_reply("Only"), code_reply("box()"), verdict_reply("pass"),
    ])

    assert events.first(RENDER).data["path"] == "/tmp/render.png"


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
    assert result.render == "/tmp/render.png"
