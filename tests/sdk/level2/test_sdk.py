"""
Level 2 -- `sdk.Client` end to end, against a real headless kernel.

No FreeCAD GUI, no Qt application: `Client` wires `ApplyPipeline`'s `dispatch`
straight through (the same pattern `run.py` uses), so a script can
create one, send a prompt, and watch Steps and events land exactly as
`ui/daedalus_panel.py`'s own worker thread does, minus the thread.
"""
import json

import pytest

from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad


class _NoKernel:
    """A worker never asked to run anything, for a route that never reaches Design."""

    def rehearse(self, code, timeout=None):
        raise AssertionError("a route that isn't design must never reach the kernel")

    def kill(self):
        pass


def test_run_question_answers_through_the_session_without_touching_the_document():
    from sdk import Client

    llm = ScriptedLLMClient(["run_question", "It finished after one Step."])
    client = Client(kernel=_NoKernel(), llm=llm)

    answer = client.send("what happened to the last run?")

    assert answer == "It finished after one Step."
    assert client.document.Objects == []
    client.close()


def test_control_stops_a_run_already_in_flight():
    from sdk import Client

    llm = ScriptedLLMClient(["control"])
    client = Client(kernel=_NoKernel(), llm=llm)

    client.send("stop")

    assert client._stop
    client.close()


def test_stop_cancels_the_run_and_kills_this_clients_own_llm(monkeypatch):
    """
    What `Mod/ui/daedalus_panel.py`'s `LoopWorker.stop`/`abort` need of
    `core.llm.claude.kill`, reached through the SDK instead of a second
    `core.llm.claude` import outside `bridge/`.
    """
    import sdk

    calls = []
    monkeypatch.setattr("core.llm.claude.kill", lambda client: calls.append(client))
    client = sdk.Client.__new__(sdk.Client)
    client._live = object()

    client.stop()

    assert calls == [client._live]
    assert client._stop


def test_a_fresh_document_is_created_through_dispatch_not_on_this_thread():
    """
    With nothing open, the panel's worker thread is where `Client` is built,
    and `FreeCAD.newDocument` there makes the GUI build an MDI window off the
    main thread: macOS answers that with `NSWindow should only be instantiated
    on the main thread!` and aborts FreeCAD outright.
    """
    from sdk import Client

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


