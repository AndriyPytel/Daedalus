"""
Level 2 -- `copilot.sdk.Client` end to end, against a real headless kernel.

No FreeCAD GUI, no Qt application: `Client` wires `ApplyPipeline`'s `dispatch`
straight through (the same pattern `run.py` uses), so a script can
create one, send a prompt, and watch Steps and events land exactly as
`ui/copilot_panel.py`'s own worker thread does, minus the thread.
"""
import json
import os

import pytest

from core.events import ANSWER, COMPLETED, INTENT, QUESTION, ROUTED
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad


def _a_box_run():
    """design -> plan -> execute -> review, the smallest Plan that lands."""
    return ScriptedLLMClient([
        "design",
        json.dumps({"design": {}, "question": ""}),
        json.dumps({"steps": ["Add a 10mm cube"]}),
        json.dumps({"steps": [
            {"step": 1, "code": "doc.addObject('Part::Box', 'Box')",
             "label": "Add the box"},
        ]}),
        json.dumps({"suspected": []}),
        json.dumps({"verdict": "pass", "reason": "A 10mm cube."}),
    ])


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=120) as running:
        yield running


def test_a_prompt_against_a_real_document_produces_steps_and_events(kernel):
    from copilot.sdk import Client

    client = Client(kernel=kernel, llm=_a_box_run())
    seen = []
    client.watch(seen.append)

    result = client.send("Add a 10mm cube")

    assert result.ok, result.reason
    assert result.steps_applied == 1
    kinds = [event.kind for event in seen]
    assert ROUTED in kinds
    assert INTENT in kinds
    assert COMPLETED in kinds
    assert [o.Name for o in client.document.Objects] == ["Box"]
    client.close()


def test_an_empty_document_gets_a_client_and_ends_with_a_saved_fcstd(tmp_path, kernel):
    from copilot.sdk import Client

    client = Client(kernel=kernel, llm=_a_box_run())
    result = client.send("Add a 10mm cube")
    assert result.ok, result.reason

    out = str(tmp_path / "made.FCStd")
    assert not os.path.exists(out)
    client.save(out)

    assert os.path.exists(out)
    client.close()


class _NoKernel:
    """A worker never asked to run anything, for a route that never reaches Design."""

    def check(self, code, document=None, render_path=None, timeout=None, names=None):
        raise AssertionError("a route that isn't design must never reach the kernel")

    def kill(self):
        pass


def test_run_question_answers_through_the_session_without_touching_the_document():
    from copilot.sdk import Client

    llm = ScriptedLLMClient(["run_question", "It finished after one Step."])
    client = Client(kernel=_NoKernel(), llm=llm)

    answer = client.send("what happened to the last run?")

    assert answer == "It finished after one Step."
    assert client.document.Objects == []
    client.close()


def test_control_stops_a_run_already_in_flight():
    from copilot.sdk import Client

    llm = ScriptedLLMClient(["control"])
    client = Client(kernel=_NoKernel(), llm=llm)

    client.send("stop")

    assert client._stop and not client._abort
    client.close()


def test_control_names_abort_separately_from_stop():
    from copilot.sdk import Client

    llm = ScriptedLLMClient(["control"])
    client = Client(kernel=_NoKernel(), llm=llm)

    client.send("abort now")

    assert client._stop and client._abort
    client.close()


def _a_question_then_a_box_run():
    """design asks once, then plan -> execute -> review, once it is answered."""
    return ScriptedLLMClient([
        "design",
        json.dumps({"design": {}, "question": "solid or hollow?"}),
        json.dumps({"design": {"solid": True}, "question": ""}),
        json.dumps({"steps": ["Add a 10mm cube"]}),
        json.dumps({"steps": [
            {"step": 1, "code": "doc.addObject('Part::Box', 'Box')",
             "label": "Add the box"},
        ]}),
        json.dumps({"suspected": []}),
        json.dumps({"verdict": "pass", "reason": "A 10mm cube."}),
    ])


def test_no_answer_callback_pauses_in_a_resumable_needs_answer_state(kernel):
    from copilot.sdk import Client

    llm = ScriptedLLMClient(["design", json.dumps({"design": {}, "question": "solid or hollow?"})])
    client = Client(kernel=kernel, llm=llm)
    seen = []
    client.watch(seen.append)

    paused = client.send("Add a box")

    assert paused.blocked and paused.awaiting_user
    kinds = [event.kind for event in seen]
    assert kinds.count(QUESTION) == 1
    assert kinds.count(ANSWER) == 0

    llm.turns.extend([
        "design",
        json.dumps({"design": {}, "question": "solid or hollow?"}),
        json.dumps({"design": {"solid": True}, "question": ""}),
        json.dumps({"steps": ["Add a 10mm cube"]}),
        json.dumps({"steps": [
            {"step": 1, "code": "doc.addObject('Part::Box', 'Box')",
             "label": "Add the box"},
        ]}),
        json.dumps({"suspected": []}),
        json.dumps({"verdict": "pass", "reason": "A 10mm cube."}),
    ])
    result = client.resume("solid")

    assert result.ok, result.reason
    assert result.steps_applied == 1
    client.close()


def test_an_answer_callback_receives_the_question_without_pausing(kernel):
    from copilot.sdk import Client

    llm = _a_question_then_a_box_run()
    answered = []

    def answer(question):
        answered.append(question)
        return "solid"

    client = Client(kernel=kernel, llm=llm, answer=answer)
    seen = []
    client.watch(seen.append)

    result = client.send("Add a box")

    assert result.ok, result.reason
    assert answered == ["solid or hollow?"]
    kinds = [event.kind for event in seen]
    assert kinds.count(QUESTION) == 1
    assert kinds.count(ANSWER) == 1
    client.close()


def test_a_declared_auto_answer_policy_is_its_own_distinct_event(kernel):
    from copilot.sdk import Client
    from core.loop import nobody_to_ask

    llm = _a_question_then_a_box_run()
    client = Client(kernel=kernel, llm=llm, answer=nobody_to_ask)
    seen = []
    client.watch(seen.append)

    client.send("Add a box")

    answers = [event for event in seen if event.kind == ANSWER]
    assert len(answers) == 1
    assert answers[0].data["by"] == "nobody"
    client.close()


def test_send_cancelled_stops_the_run_before_it_starts():
    """
    A caller's own `cancelled` reaches `core/loop.py :: run` unchanged, the
    shape the per-run cost ceiling depends on: a `cancelled` that is already
    `True` never lets Design plan anything (FCC-77), the same boundary
    `test_loop_outcomes.py` proves for `run()` directly.
    """
    from copilot.sdk import Client
    from core.events import STOPPED

    llm = ScriptedLLMClient(["design"])
    client = Client(kernel=_NoKernel(), llm=llm)
    seen = []
    client.watch(seen.append)

    result = client.send("Add a box", cancelled=lambda: True)

    assert not result.ok
    assert result.blocked
    assert STOPPED in [event.kind for event in seen]
    client.close()
