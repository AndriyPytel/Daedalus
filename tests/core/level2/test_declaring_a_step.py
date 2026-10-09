"""
A Step is declared at the moment it is built: the declaration is what
carries it out of the kernel, not a summary written after the fact.
"""
import json

import pytest

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.events import STEP_DECLARED, EventStream
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from core.measure import measure
from tests.event_helpers import events_of, log_of

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_exploration_turns_declare_nothing(kernel, tmp_path):
    """A code turn with no `step` runs and may print, but joins nothing."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "print(len(doc.Objects))"}),
        json.dumps({"code": "print(doc.Objects)"}),
        json.dumps({"declare": {"unbuildable": "nothing to build here"}}),
    ])

    declared, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look around", kernel=kernel,
    )

    assert declared == {"unbuildable": "nothing to build here"}
    assert declarations == []


def test_three_explorations_and_two_declarations_hand_back_exactly_two(kernel, tmp_path):
    """AC1: in order, each carrying the code that turn sent and its checkpoint."""
    first_code = "doc.addObject('Part::Box', 'Box1'); doc.recompute()"
    second_code = "doc.addObject('Part::Box', 'Box2'); doc.recompute()"
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('looking')"}),
        json.dumps({"code": "print(len(doc.Objects))"}),
        json.dumps({"code": first_code, "step": 1, "label": "first box"}),
        json.dumps({"code": "print(doc.Objects[-1].Name)"}),
        json.dumps({"code": second_code, "step": 2, "label": "second box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "first box"},
                                          {"step": 2, "label": "second box"}]}}),
    ])

    _, _, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes", kernel=kernel,
    )

    assert [d["step"] for d in declarations] == [1, 2]
    assert [d["label"] for d in declarations] == ["first box", "second box"]
    assert [d["code"] for d in declarations] == [first_code, second_code]
    assert all(d["checkpoint"] for d in declarations)


def test_a_declaring_turn_that_raises_is_not_handed_back_as_a_landed_step(kernel, tmp_path):
    """AC2: it never joins `declarations`; its traceback reaches the retry
    through the ordinary tool_result feedback, unchanged by this ticket."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "raise RuntimeError('bad step')", "step": 1, "label": "bad"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()",
                    "step": 1, "label": "fixed"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "fixed"}]}}),
    ])

    _, session, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box", kernel=kernel,
    )

    assert len(declarations) == 1
    assert declarations[0]["label"] == "fixed"
    retry_feedback = json.loads(session.messages[-4]["content"])
    assert retry_feedback["ok"] is False
    assert "bad step" in retry_feedback["traceback"]


def test_one_step_declared_event_per_declaration_reaches_the_stream(kernel, tmp_path):
    """AC3: `core.measure`'s Steps-started count over the recorded log equals
    a hand count of `STEP_DECLARED` events."""
    stream = EventStream()
    llm = ScriptedLLMClient([
        json.dumps({"code": "print('looking')"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box1'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()",
                    "step": 2, "label": "second box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "first box"},
                                          {"step": 2, "label": "second box"}]}}),
    ])

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes",
              kernel=kernel, stream=stream)

    declared_events = events_of(stream, STEP_DECLARED)
    assert len(declared_events) == 2
    assert [event.data["step"] for event in declared_events] == [1, 2]

    log_path = str(tmp_path / "session.jsonl")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(log_of(stream))

    result = measure(log_path)

    assert result.steps_started == len(declared_events) == 2
