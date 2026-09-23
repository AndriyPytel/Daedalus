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

from core.events import ANSWER, COMPLETED, INTENT, QUESTION, ROUTED, replay
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad


def _a_box_run():
    """design -> plan -> execute (code, declare) -> review, the smallest Plan that lands."""
    return ScriptedLLMClient([
        "design",
        json.dumps({"declare": {"design": {}, "question": ""}}),
        json.dumps({"declare": {"steps": ["Add a 10mm cube"]}}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1,
                    "label": "Add the box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Add the box"}]}}),
        json.dumps({"declare": {"suspected": []}}),
        json.dumps({"declare": {"verdict": "pass", "reason": "A 10mm cube."}}),
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


@pytest.mark.cadgenbench
def test_a_run_s_save_path_ends_in_step_and_the_scorer_can_read_it(tmp_path, kernel):
    """The seam a headless run hands the scorer -- `client.export_step(path)`
    writes real STEP geometry, not a Document FreeCAD would otherwise
    rename to `<path>.FCStd` the way `client.save(path)` would."""
    import sys

    from copilot.sdk import Client

    client = Client(kernel=kernel, llm=_a_box_run())
    result = client.send("Add a 10mm cube")
    assert result.ok, result.reason

    out = str(tmp_path / "made.step")
    client.export_step(out)
    client.close()

    assert os.path.exists(out)
    assert not os.path.exists(out + ".FCStd")

    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    sys.path.insert(0, os.path.join(ROOT, "src", "tools"))
    import score as score_module

    scored = score_module.score(out, out)

    assert scored["score"] == pytest.approx(1.0, abs=0.0005)
    assert scored["is_valid"] is True


class _NoKernel:
    """A worker never asked to run anything, for a route that never reaches Design."""

    def check(self, code, document=None, render_path=None, timeout=None, names=None, ephemeral=None):
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


def test_the_role_table_client_routes_and_answers_from_the_orchestrator_row():
    from copilot.sdk import Client
    from core.llm import ORCHESTRATOR, RoleClient

    llm = RoleClient(roles={ORCHESTRATOR: ScriptedLLMClient(
        ["run_question", "It finished after one Step."])},
        caller=ScriptedLLMClient([]))
    client = Client(kernel=_NoKernel(), llm=llm)

    assert client.send("what happened to the last run?") == "It finished after one Step."
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
    """design asks once, then plan -> execute (code, declare) -> review, once it is answered."""
    return ScriptedLLMClient([
        "design",
        json.dumps({"declare": {"design": {}, "question": "solid or hollow?"}}),
        json.dumps({"declare": {"design": {"solid": True}, "question": ""}}),
        json.dumps({"declare": {"steps": ["Add a 10mm cube"]}}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1,
                    "label": "Add the box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Add the box"}]}}),
        json.dumps({"declare": {"suspected": []}}),
        json.dumps({"declare": {"verdict": "pass", "reason": "A 10mm cube."}}),
    ])


def test_no_answer_callback_pauses_in_a_resumable_needs_answer_state(kernel):
    from copilot.sdk import Client

    llm = ScriptedLLMClient(["design", json.dumps({"declare": {"design": {}, "question": "solid or hollow?"}})])
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
        json.dumps({"declare": {"design": {}, "question": "solid or hollow?"}}),
        json.dumps({"declare": {"design": {"solid": True}, "question": ""}}),
        json.dumps({"declare": {"steps": ["Add a 10mm cube"]}}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1,
                    "label": "Add the box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Add the box"}]}}),
        json.dumps({"declare": {"suspected": []}}),
        json.dumps({"declare": {"verdict": "pass", "reason": "A 10mm cube."}}),
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
    from core.loop.gate import nobody_to_ask

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
    A caller's own `cancelled` is the automated dollar/time ceiling, not a
    human's Cancel: a `cancelled` that is already `True` never lets Design
    plan anything, and the run ends `budget_exhausted`, not `stopped`, the
    same boundary `test_loop_outcomes.py` proves for `run()` directly.
    """
    from copilot.sdk import Client
    from core.events import BUDGET_EXHAUSTED, STOPPED

    llm = ScriptedLLMClient(["design"])
    client = Client(kernel=_NoKernel(), llm=llm)
    seen = []
    client.watch(seen.append)

    result = client.send("Add a box", cancelled=lambda: True)

    assert not result.ok
    assert result.blocked
    assert "Stopped by you" not in result.reason
    kinds = [event.kind for event in seen]
    assert BUDGET_EXHAUSTED in kinds
    assert STOPPED not in kinds
    client.close()


def test_kill_provider_reaches_this_clients_own_llm(monkeypatch):
    """
    What `Mod/ui/copilot_panel.py`'s `LoopWorker.stop`/`abort` need of
    `core.llm.claude.kill`, reached through the SDK instead of a second
    `core.llm.claude` import outside `bridge/`.
    """
    from copilot import sdk

    calls = []
    monkeypatch.setattr("core.llm.claude.kill", lambda client: calls.append(client))
    client = sdk.Client.__new__(sdk.Client)
    client._llm = object()

    client.kill_provider()

    assert calls == [client._llm]


def test_a_real_runs_transcript_replays_as_the_run_that_wrote_it(tmp_path, kernel):
    """
    Earlier replay tests all wrote their own transcript by hand, so a bug in
    `Client.send`'s own path to disk -- as distinct from `EventStream.log_to`
    called directly -- would have passed every one of them. This drives an
    actual headless run and reads back the file `Client` itself wrote.
    """
    from copilot.sdk import Client

    path = str(tmp_path / "session.jsonl")
    client = Client(kernel=kernel, llm=_a_box_run())
    client.events.log_to(path)

    result = client.send("Add a 10mm cube")
    assert result.ok, result.reason

    live_kinds = [event.kind for event in client.events]
    restored, truncated = replay(path)

    assert not truncated
    assert [event.kind for event in restored] == live_kinds
    assert COMPLETED in live_kinds
    client.close()


def test_a_fresh_document_is_created_through_dispatch_not_on_this_thread():
    """
    With nothing open, the panel's worker thread is where `Client` is built,
    and `FreeCAD.newDocument` there makes the GUI build an MDI window off the
    main thread: macOS answers that with `NSWindow should only be instantiated
    on the main thread!` and aborts FreeCAD outright.
    """
    from copilot.sdk import Client

    marshalled = []

    def dispatch(func, timeout=None):
        marshalled.append(func)
        return func()

    client = Client(kernel=_NoKernel(), llm=ScriptedLLMClient(["control"]), dispatch=dispatch)
    try:
        assert client.document is not None
        assert marshalled, "the new Document was built without going through dispatch"
    finally:
        client.close()
