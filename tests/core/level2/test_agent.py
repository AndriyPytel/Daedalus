"""
FCC-321: a phase agent iterates against its own kernel instead of answering
in one call.
"""
import json
import os

import pytest

from core.agent import phase_contract as pc
from core.agent import AgentGaveUp, run_phase
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
    """FCC-322 AC3: a phase's code turn may hand off a bounded question to a
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
