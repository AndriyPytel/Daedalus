"""
Level 1 — the Plan Gate.

The single point at which the user approves a Plan. Approval covers every Step
in it; there is no per-Step approval. Nothing runs before it. A revised Plan is
a new Plan and has its own Gate — which is still one Gate per Plan (FCC-68).
"""
import json

from core.events import BLOCKED, PLAN, PLAN_REVISED, EventStream
from core.llm import ScriptedLLMClient
from core.loop import AMEND, APPROVE, REJECT, Approval, Budgets, run
from tests.test_loop import (
    FakeTools, code_reply, failed, obsolete_reply, plan_reply, question_reply, verdict_reply,
)


def drive(turns, gate, budgets=None, ask=None):
    llm = ScriptedLLMClient(turns)
    tools = FakeTools()
    events = EventStream()
    kwargs = {"ask": ask} if ask is not None else {}
    result = run("Make a 10mm box", llm=llm, tools=tools.registry, events=events,
                 gate=gate, budgets=budgets or Budgets(), **kwargs)
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


def test_a_review_driven_revision_goes_back_to_the_gate():
    """
    Rewritten from user story 17, which said a revision is *told*, not asked
    again. FCC-68 reversed it: the user approved 18 Steps and a different set
    ran, learned about after the fact. A revised Plan is a new Plan, and every
    Plan has its own Gate — which is still one Gate per Plan, not per Step.
    """
    gate = Gate(Approval(APPROVE), Approval(APPROVE))

    _, events, tools = drive([
        plan_reply("Make it"), code_reply("make()"), verdict_reply("fail", "20mm, not 10mm"),
        plan_reply("Fix the length"), code_reply("fix()"), verdict_reply("pass"),
    ], gate)

    assert gate.shown == [["Make it"], ["Fix the length"]]
    # Still surfaced as a revision — the Gate is added to PLAN_REVISED, not
    # substituted for it.
    assert events.first(PLAN_REVISED).data["reason"] == "20mm, not 10mm"
    # What the previous Plan applied stays in the Document.
    assert tools.applied_codes() == ["make()", "fix()"]


def test_an_obsolete_driven_revision_goes_back_to_the_gate():
    """The other half of user story 17, reversed the same way. FCC-68."""
    gate = Gate(Approval(APPROVE), Approval(APPROVE))

    _, events, _ = drive([
        plan_reply("Delete the old pad"), obsolete_reply("There is no pad here."),
        plan_reply("Add the new one"), code_reply("add()"), verdict_reply("pass"),
    ], gate)

    assert gate.shown == [["Delete the old pad"], ["Add the new one"]]
    assert "no pad here" in events.first(PLAN_REVISED).data["reason"]


def test_a_revised_plan_is_shown_before_it_changes_anything():
    """A handback replans; the revision is at the Gate before it touches the Document."""
    applied_when_shown = []
    tools = FakeTools([failed("one")])

    def gate(steps):
        applied_when_shown.append(list(tools.applied_codes()))
        return Approval(APPROVE)

    run("Make a 10mm box", llm=ScriptedLLMClient([
        plan_reply("Impossible"), code_reply("nope()"),
        plan_reply("Possible"), code_reply("yes()"), verdict_reply("pass"),
    ]), tools=tools.registry, gate=gate, budgets=Budgets(attempts_per_step=1))

    assert applied_when_shown == [[], ["nope()"]], \
        "the revised Plan ran before it was shown: %s" % applied_when_shown


def test_rejecting_a_revised_plan_applies_nothing_new_and_says_so():
    gate = Gate(Approval(APPROVE), Approval(REJECT))

    result, _, tools = drive([
        plan_reply("Make it"), code_reply("make()"), verdict_reply("fail", "20mm, not 10mm"),
        plan_reply("Fix the length"),
    ], gate)

    assert result.blocked
    assert tools.applied_codes() == ["make()"], "a rejected Plan applied a Step"
    assert result.steps_applied == 1
    assert "rejected the Plan" in result.reason
    assert "nothing was applied" not in result.reason, \
        "the block claims an empty Document over one Step already in it: %r" % result.reason


def test_amending_a_revised_plan_spends_the_amendment_budget():
    gate = Gate(Approval(APPROVE), Approval(AMEND, "simpler"), Approval(AMEND, "simpler still"),
                Approval(AMEND, "simpler again"))

    result, _, _ = drive([
        plan_reply("Make it"), code_reply("make()"), verdict_reply("fail", "no"),
        plan_reply("v2"), plan_reply("v3"), plan_reply("v4"),
    ], gate, budgets=Budgets(revisions=3))

    assert result.blocked
    assert "amended the Plan 2 times" in result.reason
    assert "Review rejected the result 1 time" in result.reason


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


def test_a_replan_after_an_amendment_asks_rather_than_planning_blind():
    """The third re-plan site, and it had the same hole as the other two. FCC-67."""
    asked = []
    gate = Gate(Approval(AMEND, "Use PartDesign, not Part primitives"), Approval(APPROVE))

    result, _, tools = drive([
        plan_reply("Add a Part::Box"),
        question_reply("Which thickness?"),
        plan_reply("Create a 10mm Body"), code_reply("a()"), verdict_reply("pass"),
    ], gate, ask=lambda question: asked.append(question) or "10mm")

    assert asked == ["Which thickness?"]
    assert result.ok
    assert gate.shown[-1] == ["Create a 10mm Body"]
    assert tools.applied_codes() == ["a()"]


def test_an_amendment_is_surfaced_as_a_revised_plan():
    gate = Gate(Approval(AMEND, "Simpler please"), Approval(APPROVE))

    _, events, _ = drive([
        plan_reply("A", "B", "C"), plan_reply("Just A"), code_reply("a()"),
        verdict_reply("pass"),
    ], gate)

    revised = events.first(PLAN_REVISED)
    assert revised.data["steps"] == ["Just A"]
    assert "Simpler please" in revised.data["reason"]


# ── the revision budget names what it was spent on (FCC-22) ───────────────────


def _amended_twice_then_obsolete_twice():
    """
    Two amendments and two obsolete Steps against a budget of three revisions.
    Whichever branch spent the last one used to take the blame for all of them.
    """
    gate = Gate(Approval(AMEND, "simpler"), Approval(AMEND, "simpler still"), Approval(APPROVE))
    return drive([
        plan_reply("v1"),
        plan_reply("v2"),
        plan_reply("v3"), obsolete_reply("stale 1"),
        plan_reply("v4"), obsolete_reply("stale 2"),
        plan_reply("v5"),
    ], gate)


def test_the_block_counts_each_cause_of_a_revision_separately():
    result, _, _ = _amended_twice_then_obsolete_twice()

    assert result.blocked
    assert "amended the Plan 2 times" in result.reason, \
        "the two amendments went unreported: %r" % result.reason
    assert "out of date 1 time" in result.reason, \
        "Execute was blamed for more than it did: %r" % result.reason


def test_the_block_says_what_was_refused_when_the_budget_ran_out():
    result, _, _ = _amended_twice_then_obsolete_twice()

    assert "out of date again" in result.reason, \
        "the block does not say what it refused: %r" % result.reason


def test_amendments_alone_are_reported_as_amendments_and_nothing_else():
    gate = Gate(*[Approval(AMEND, "no, again") for _ in range(9)])

    result, _, tools = drive([plan_reply("v%d" % n) for n in range(9)], gate)

    assert result.blocked
    assert "amended the Plan 3 times" in result.reason
    assert "out of date" not in result.reason, \
        "an amendment budget blamed Execute: %r" % result.reason
    assert "Review" not in result.reason
    assert tools.applied_codes() == []


def test_obsolescence_alone_is_reported_as_obsolescence_and_nothing_else():
    result, _, _ = drive([
        plan_reply("a"), obsolete_reply("stale 1"),
        plan_reply("b"), obsolete_reply("stale 2"),
        plan_reply("c"), obsolete_reply("stale 3"),
        plan_reply("d"), obsolete_reply("stale 4"),
    ], Gate())

    assert result.blocked
    assert "out of date 3 times" in result.reason
    assert "amended" not in result.reason, \
        "obsolescence was reported as the user amending: %r" % result.reason


def test_a_review_driven_rewrite_spends_the_budget_and_says_so():
    """
    The third spender. It is bounded separately by review_cycles, but it spends
    the shared revision budget too, and used to do so invisibly — incrementing
    the counter without ever checking it, so the next branch took the blame.
    """
    result, _, _ = drive([
        plan_reply("a"), code_reply("a()"), verdict_reply("fail", "no"),
        plan_reply("b"), code_reply("b()"), verdict_reply("fail", "still no"),
        plan_reply("c"), code_reply("c()"), verdict_reply("fail", "no better"),
    ], Gate(), budgets=Budgets(review_cycles=3, revisions=2))

    assert result.blocked
    assert "Review rejected the result 2 times" in result.reason, \
        "the Review-driven rewrites went unreported: %r" % result.reason
    assert "amended" not in result.reason and "out of date" not in result.reason, \
        "a Review-driven block blamed another branch: %r" % result.reason


def test_a_review_driven_rewrite_shows_up_in_another_branchs_block():
    """The misattribution the other way round: Review's spending was invisible."""
    result, _, _ = drive([
        plan_reply("a"), code_reply("a()"), verdict_reply("fail", "no"),
        plan_reply("b"), code_reply("b()"), verdict_reply("fail", "still no"),
        plan_reply("c"), obsolete_reply("stale"),
        plan_reply("d"), obsolete_reply("stale again"),
        plan_reply("e"),
    ], Gate(), budgets=Budgets(review_cycles=2, revisions=3))

    assert result.blocked
    assert "Review rejected the result 2 times" in result.reason, \
        "the Review-driven rewrites went unreported: %r" % result.reason
    assert "out of date 1 time" in result.reason


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
