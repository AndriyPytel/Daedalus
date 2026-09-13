"""
A phase agent iterates against its own kernel instead of answering
in one call.
"""
import json
import os

import pytest

from core.agent import phase_contract as pc
from core.agent import AgentGaveUp, run_phase
from core.agent import agent
from core.events import SUB_AGENT_ANSWERED, SUB_AGENT_SPAWNED, EventStream
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from core.agent.subagent import SessionTreeSupervisor

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_agent_iterates_after_a_bad_attempt_before_declaring(kernel, tmp_path):
    """AC1/TESTING STRATEGY: fails, tries different code, then declares."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box').NoSuchProperty = 1"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "pad the box"}),
        json.dumps({"declare": {"step": "pad the box"}}),
    ])

    declared, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"),
        {"design": {"kind": "box"}, "plan": [{"do": "pad 10mm"}]},
        llm, "pad a 10mm box", kernel=kernel,
    )

    assert declared == {"step": "pad the box"}
    assert len(llm.calls) == 3
    assert len(declarations) == 1
    assert os.path.exists(declarations[0]["checkpoint"])


def test_agent_does_not_iterate_past_a_first_attempt_success(kernel, tmp_path):
    """AC3: no extra turns once the model is done."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "pad the box"}),
        json.dumps({"declare": {"step": "pad the box"}}),
    ])

    declared, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"),
        {"design": {"kind": "box"}, "plan": [{"do": "pad 10mm"}]},
        llm, "pad a 10mm box", kernel=kernel,
    )

    assert declared == {"step": "pad the box"}
    assert len(llm.calls) == 2
    assert len(declarations) == 1


def test_agent_is_corrected_once_before_it_gives_up_on_an_unusable_reply(kernel, tmp_path):
    """A reply that is none of the three shapes is told the contract and asked
    again; the turn budget is what ends it, not the first mistake."""
    llm = ScriptedLLMClient([
        json.dumps({"comment": "thinking out loud"}),
        json.dumps({"comment": "still thinking out loud"}),
        json.dumps({"comment": "and again"}),
        json.dumps({"comment": "and once more"}),
    ])
    supervisor = SessionTreeSupervisor(turn_budget=2)

    with pytest.raises(AgentGaveUp):
        run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
                  kernel=kernel, supervisor=supervisor)

    assert len(llm.calls) == 4
    correction = llm.calls[-1][-1]
    assert "declare" in json.dumps(correction)


def test_agent_delegates_to_a_depth1_subagent_before_declaring(kernel, tmp_path):
    """A phase's code turn may hand off a bounded question to a
    depth-1 sub-agent and continue from its answer."""
    llm = ScriptedLLMClient([
        json.dumps({"delegate": "does 'ring axis' belong to the closed datum list?"}),
        "yes, it is on the closed list.",
        json.dumps({"declare": {"kind_vocabulary": "closed", "design": {}, "question": ""}}),
    ])
    supervisor = SessionTreeSupervisor()
    stream = EventStream()

    declared, session, declarations = run_phase(
        pc.DESIGN, str(tmp_path / "design"), {"intent": {"ask": "a plate"}},
        llm, "describe the plate", kernel=kernel, supervisor=supervisor, stream=stream,
    )

    assert declared == {"kind_vocabulary": "closed", "design": {}, "question": ""}
    assert len(llm.calls) == 3
    kinds = [event.kind for event in stream]
    assert SUB_AGENT_SPAWNED in kinds
    assert SUB_AGENT_ANSWERED in kinds
    assert declarations == []


def test_agent_delegate_without_a_supervisor_gives_up(kernel, tmp_path):
    llm = ScriptedLLMClient([json.dumps({"delegate": "anything"})])

    with pytest.raises(AgentGaveUp):
        run_phase(pc.DESIGN, str(tmp_path / "design"), {"intent": {}}, llm, "task", kernel=kernel)


def test_a_step_hands_back_what_its_code_printed(kernel, tmp_path):
    """A probe's own print is the only way an agent can look at the Document,
    so what it printed is in the feedback its next turn reads."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('objects', len(doc.Objects))"}),
        json.dumps({"declare": {"step": "looked"}}),
    ])

    _, session, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look at the document",
        kernel=kernel,
    )

    feedback = json.loads(session.messages[-2]["content"])
    assert feedback["stdout"].strip() == "objects 0"


def test_a_failed_step_hands_back_what_it_printed_before_it_died(kernel, tmp_path):
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('reached'); raise RuntimeError('no')"}),
        json.dumps({"declare": {"step": "gave up on that"}}),
    ])

    _, session, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "try something",
        kernel=kernel,
    )

    feedback = json.loads(session.messages[-2]["content"])
    assert feedback["ok"] is False
    assert feedback["stdout"].strip() == "reached"


def test_the_spent_budget_buys_one_last_turn_to_declare(kernel, tmp_path):
    """The budget ends the iterating, not the run: the phase is told its last
    turn is here and declares what it has, rather than the run dying with
    nothing to show for what it already built."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "one box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()",
                    "step": 2, "label": "two boxes"}),
        json.dumps({"declare": {"step": "one box"}}),
    ])
    supervisor = SessionTreeSupervisor(turn_budget=1)

    declared, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
        kernel=kernel, supervisor=supervisor,
    )

    assert declared == {"step": "one box"}
    assert len(declarations) == 1
    assert "declare" in session.messages[-2]["content"]


def test_a_phase_that_will_not_declare_on_its_last_turn_gives_up(kernel, tmp_path):
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box3'); doc.recompute()"}),
    ])
    supervisor = SessionTreeSupervisor(turn_budget=1)

    with pytest.raises(AgentGaveUp):
        run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
                  kernel=kernel, supervisor=supervisor)


def test_agent_refuses_a_second_declaration_of_a_delivered_step_number(kernel, tmp_path):
    """AC1/AC2: a delivered Step's feedback carries its number and the next
    one; declaring that number again this call is refused with the same two
    numbers, and `on_declaration` never runs for the refused turn."""
    calls = []
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()",
                    "step": 1, "label": "first box again"}),
        json.dumps({"declare": {"step": "done"}}),
    ])

    def on_declaration(declaration):
        calls.append(declaration["step"])
        return {"delivered": True}

    _, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
        kernel=kernel, on_declaration=on_declaration,
    )

    assert calls == [1]
    assert len(declarations) == 1

    first_feedback = json.loads(session.messages[3]["content"])
    second_feedback = json.loads(session.messages[5]["content"])

    assert first_feedback["step"] == 1
    assert first_feedback["next_step"] == 2

    assert second_feedback["ok"] is False
    assert second_feedback["step"] == 1
    assert second_feedback["next_step"] == 2


def test_a_step_that_failed_delivery_can_be_declared_again(kernel, tmp_path):
    """AC4: a failed delivery is not the same invariant as an already-delivered
    Step -- the same number is refused only once it has succeeded, so the
    model can fix its code and redeclare the number that failed."""
    calls = []
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()",
                    "step": 1, "label": "first box fixed"}),
        json.dumps({"declare": {"step": "done"}}),
    ])

    def on_declaration(declaration):
        calls.append(declaration["step"])
        if len(calls) == 1:
            return {"delivered": False, "traceback": "signature mismatch"}
        return {"delivered": True}

    _, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
        kernel=kernel, on_declaration=on_declaration,
    )

    assert calls == [1, 1]
    assert [d["step"] for d in declarations] == [1, 1]
    assert declarations[0]["delivered"] is False
    assert declarations[1].get("delivered", True) is True

    second_feedback = json.loads(session.messages[5]["content"])
    assert second_feedback["step"] == 1
    assert second_feedback["next_step"] == 2


def test_a_run_that_only_ever_reads_is_demanded_a_declaration_then_gives_up(kernel, tmp_path, monkeypatch):
    """Acceptance: Execute counts turns since its last declared Step; past the
    limit it demands one on the next turn; a pass that still does not declare
    after the demand ends with AgentGaveUp naming the turns spent."""
    monkeypatch.setattr(agent, "_DECLARATION_LIMIT", 2)
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('probe 1')"}),
        json.dumps({"code": "print('probe 2')"}),
        json.dumps({"code": "print('probe 3')"}),
        json.dumps({"code": "print('probe 4')"}),
    ])

    with pytest.raises(AgentGaveUp) as excinfo:
        run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look around",
                  kernel=kernel)

    assert "3" in str(excinfo.value)
    assert len(llm.calls) == 4
    demand = llm.calls[-1][-1]
    assert "declare" in json.dumps(demand)


def test_a_refused_redeclaration_never_reaches_the_kernel(kernel, tmp_path):
    """A Step already delivered is refused before its code runs, so the kernel
    is not left holding objects the Document never took."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Twin'); doc.recompute()",
                    "step": 1, "label": "first box again"}),
        json.dumps({"declare": {"step": "done"}}),
    ])

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
              kernel=kernel, on_declaration=lambda declaration: {"delivered": True})

    named = kernel.check("print([o.Name for o in doc.Objects])")
    assert "Twin" not in named.stdout


def test_a_declaration_resets_the_turns_counted_against_the_limit(kernel, tmp_path, monkeypatch):
    """The limit is turns since the last Step, not turns in the pass: a phase
    that declares keeps working for a whole limit again."""
    monkeypatch.setattr(agent, "_DECLARATION_LIMIT", 3)
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('probe 1')"}),
        json.dumps({"code": "print('probe 2')"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "print('probe 3')"}),
        json.dumps({"code": "print('probe 4')"}),
        json.dumps({"declare": {"step": "done"}}),
    ])

    declared, _, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
        kernel=kernel, on_declaration=lambda declaration: {"delivered": True})

    assert declared == {"step": "done"}
    assert len(declarations) == 1
    assert len(llm.calls) == 6


def test_a_delivered_steps_next_step_is_the_plans_number_not_step_plus_one(kernel, tmp_path):
    """AC1: a Plan whose Steps are 1, 3, 5 answers next_step 3 once Step 1
    lands -- Step 2 does not exist -- and answers no next_step once the
    Plan's own last Step, 5, lands."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "first"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()",
                    "step": 5, "label": "last"}),
        json.dumps({"declare": {"step": "done"}}),
    ])
    plan = [{"step": 1, "do": "first"}, {"step": 3, "do": "third"},
            {"step": 5, "do": "fifth"}]

    _, session, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {"plan": plan}, llm, "build in gaps",
        kernel=kernel, on_declaration=lambda declaration: {"delivered": True},
    )

    first_feedback = json.loads(session.messages[3]["content"])
    last_feedback = json.loads(session.messages[5]["content"])

    assert first_feedback["next_step"] == 3
    assert "next_step" not in last_feedback


def test_a_delegate_answered_after_the_demand_is_given_a_code_turn(kernel, tmp_path, monkeypatch):
    """AC2: a phase demanded a declaration that delegates and gets an answer
    is given the code turn that answer was for, not ended on it -- a run of
    demand -> delegate -> code."""
    monkeypatch.setattr(agent, "_DECLARATION_LIMIT", 1)
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('probe 1')"}),
        json.dumps({"code": "print('probe 2')"}),
        json.dumps({"delegate": "what shape is the base?"}),
        "a rectangle.",
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "box"}),
        json.dumps({"declare": {"step": "done"}}),
    ])

    declared, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
        kernel=kernel, supervisor=SessionTreeSupervisor(),
        on_declaration=lambda declaration: {"delivered": True},
    )

    assert declared == {"step": "done"}
    assert len(declarations) == 1


def test_run_phase_seeds_since_declaration_from_the_exploration_argument(kernel, tmp_path, monkeypatch):
    """
    The turn budget re-keyed to the Step, not the phase call: an
    `exploration` count already spent probing this Step in an earlier
    regeneration is read back in, so the demand fires on the very first
    code turn of this call rather than resetting to a fresh
    `_DECLARATION_LIMIT`.
    """
    monkeypatch.setattr(agent, "_DECLARATION_LIMIT", 2)
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('probe 3')"}),
        json.dumps({"code": "print('probe 4')"}),
    ])
    exploration = {"turns": 2}

    with pytest.raises(AgentGaveUp):
        run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look around",
                  kernel=kernel, exploration=exploration)

    assert len(llm.calls) == 2, "the demand fires on the first code turn, not the third"


def test_run_phase_writes_the_ending_count_back_into_the_exploration_argument(kernel, tmp_path):
    """A caller reusing the same `exploration` dict across a regeneration
    reads what this call spent, so the next call can carry it forward."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('probe 1')"}),
        json.dumps({"code": "print('probe 2')"}),
        json.dumps({"declare": {"unbuildable": "nothing to build here"}}),
    ])
    exploration = {}

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look around",
              kernel=kernel, exploration=exploration)

    assert exploration["turns"] == 2
