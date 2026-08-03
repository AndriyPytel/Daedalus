"""
Level 1 — the Plan Gate.

The single point at which the user approves a Plan. Approval covers every Step
in it; there is no per-Step approval. Nothing runs before it.
"""
import json

from core.events import BLOCKED, PLAN, PLAN_REVISED, EventStream
from core.llm import ScriptedLLMClient
from core.loop import AMEND, APPROVE, REJECT, Approval, Budgets, run
from tests.test_loop import (
    FakeTools, code_reply, obsolete_reply, plan_reply, verdict_reply,
)


def drive(turns, gate, budgets=None):
    llm = ScriptedLLMClient(turns)
    tools = FakeTools()
    events = EventStream()
    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                 gate=gate, budgets=budgets or Budgets())
    return result, events, tools


class Gate:
    """Records what it was shown and answers however the test says."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.shown = []

    def __call__(self, steps):
        self.shown.append(list(steps))
        return self.answers.pop(0) if self.answers else Approval(APPROVE)


# ── nothing runs before approval ──────────────────────────────────────────────


def test_no_step_executes_before_the_user_approves():
    gate = Gate(Approval(APPROVE))

    _, events, tools = drive(
        [plan_reply("Create a 10mm box"), code_reply("box()"), verdict_reply("pass")], gate)

    assert gate.shown == [["Create a 10mm box"]]
    assert tools.applied_codes() == ["box()"]
    # The Plan was shown to the gate before anything was applied.
    kinds = [e.kind for e in events]
    assert kinds.index(PLAN) == 0


def test_a_rejected_plan_applies_nothing():
    gate = Gate(Approval(REJECT))

    result, events, tools = drive([plan_reply("Create a 10mm box", "Fillet it")], gate)

    assert tools.applied_codes() == []
    assert result.blocked
    assert "rejected the Plan" in result.reason
    assert events.first(BLOCKED) is not None


def test_the_gate_sees_the_plan_it_is_approving():
    gate = Gate(Approval(APPROVE))

    drive([plan_reply("First", "Second", "Third"),
           code_reply("a()"), code_reply("b()"), code_reply("c()"),
           verdict_reply("pass")], gate)

    assert gate.shown == [["First", "Second", "Third"]]


# ── one approval covers the whole Plan ────────────────────────────────────────


def test_approval_covers_every_step_so_there_is_no_per_step_approval():
    gate = Gate(Approval(APPROVE))

    _, _, tools = drive([plan_reply("First", "Second", "Third"),
                         code_reply("a()"), code_reply("b()"), code_reply("c()"),
                         verdict_reply("pass")], gate)

    assert len(gate.shown) == 1, "the user was asked more than once"
    assert len(tools.applied_codes()) == 3


def test_a_review_driven_revision_is_surfaced_not_re_approved():
    """
    User story 17 says the user is *told* about a revision, not asked again —
    otherwise the single Plan Gate becomes a per-round gate.
    """
    gate = Gate(Approval(APPROVE))

    _, events, _ = drive([
        plan_reply("Make it"), code_reply("make()"), verdict_reply("fail", "20mm, not 10mm"),
        plan_reply("Fix the length"), code_reply("fix()"), verdict_reply("pass"),
    ], gate)

    assert len(gate.shown) == 1
    assert events.first(PLAN_REVISED).data["reason"] == "20mm, not 10mm"


def test_an_obsolete_driven_revision_is_surfaced_not_re_approved():
    gate = Gate(Approval(APPROVE))

    _, events, _ = drive([
        plan_reply("Delete the old pad"), obsolete_reply("There is no pad here."),
        plan_reply("Add the new one"), code_reply("add()"), verdict_reply("pass"),
    ], gate)

    assert len(gate.shown) == 1
    assert "no pad here" in events.first(PLAN_REVISED).data["reason"]


# ── amending ──────────────────────────────────────────────────────────────────


def test_the_user_can_amend_a_plan_before_it_runs():
    gate = Gate(Approval(AMEND, "Use PartDesign, not Part primitives"),
                Approval(APPROVE))

    result, events, tools = drive([
        plan_reply("Add a Part::Box"),
        plan_reply("Create a Body", "Sketch a 10mm square", "Pad it"),
        code_reply("a()"), code_reply("b()"), code_reply("c()"),
        verdict_reply("pass"),
    ], gate)

    assert result.ok
    assert gate.shown == [["Add a Part::Box"],
                          ["Create a Body", "Sketch a 10mm square", "Pad it"]]
    assert len(tools.applied_codes()) == 3


def test_the_amendment_reaches_plan():
    gate = Gate(Approval(AMEND, "Use PartDesign, not Part primitives"), Approval(APPROVE))
    llm = ScriptedLLMClient([
        plan_reply("Add a Part::Box"),
        plan_reply("Create a Body"),
        code_reply("a()"),
        verdict_reply("pass"),
    ])

    run("Make a 10mm box", llm=llm, tools=FakeTools().registry, gate=gate)

    assert "Use PartDesign, not Part primitives" in json.dumps(llm.calls[1])


def test_an_amendment_is_surfaced_as_a_revised_plan():
    gate = Gate(Approval(AMEND, "Simpler please"), Approval(APPROVE))

    _, events, _ = drive([
        plan_reply("A", "B", "C"), plan_reply("Just A"), code_reply("a()"),
        verdict_reply("pass"),
    ], gate)

    revised = events.first(PLAN_REVISED)
    assert revised.data["steps"] == ["Just A"]
    assert "Simpler please" in revised.data["reason"]


def test_amending_forever_cannot_loop():
    gate = Gate(*[Approval(AMEND, "no, again") for _ in range(9)])

    result, _, tools = drive([plan_reply("v%d" % n) for n in range(9)], gate)

    assert result.blocked
    assert "amended" in result.reason
    assert tools.applied_codes() == []


def test_rejecting_after_amending_still_applies_nothing():
    gate = Gate(Approval(AMEND, "different"), Approval(REJECT))

    result, _, tools = drive([plan_reply("A"), plan_reply("B")], gate)

    assert result.blocked
    assert tools.applied_codes() == []


# ── the default ───────────────────────────────────────────────────────────────


def test_a_gate_that_answers_nothing_is_treated_as_approval():
    """A gate returning None must not wedge the run."""
    _, _, tools = drive([plan_reply("Only"), code_reply("box()"), verdict_reply("pass")],
                        gate=lambda steps: None)

    assert tools.applied_codes() == ["box()"]
