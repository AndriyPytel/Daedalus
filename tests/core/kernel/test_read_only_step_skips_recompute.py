"""A read-only Assertion turn runs no document-wide recompute."""
import pytest

from core.kernel import kernel_worker

pytestmark = pytest.mark.freecad


class _CountingDoc:
    """
    Just enough of `FreeCAD.Document` for `run_step` to run a Step against:
    an empty feature tree, so every scan `_run_step_body` runs after the
    recompute (in-error objects, mixed-concavity fillets, idle features,
    split or invalid Bodies) is a no-op over `[]` rather than a claim about
    real geometry -- what this counts is whether `recompute` was called at
    all, not what it would have found.
    """

    def __init__(self):
        self.Objects = []
        self.recomputes = 0

    def getObject(self, name):
        return None

    def recompute(self, scope=None):
        self.recomputes += 1
        return 0


@pytest.fixture
def counting_session(monkeypatch):
    doc = _CountingDoc()
    monkeypatch.setitem(kernel_worker._SESSION, "doc", doc)
    monkeypatch.setitem(kernel_worker._SESSION, "names", {})
    monkeypatch.setitem(kernel_worker._SESSION, "checkpoint", None)
    monkeypatch.setattr(kernel_worker, "_save_checkpoint", lambda doc: None)
    return doc


def test_a_step_that_creates_and_modifies_nothing_runs_no_recompute(counting_session):
    response = kernel_worker.run_step({"code": "pass"})

    assert response["ok"], response.get("traceback")
    assert counting_session.recomputes == 0


def test_a_step_that_only_prints_still_runs_no_recompute(counting_session):
    response = kernel_worker.run_step(
        {"code": "print(len(doc.Objects))"})

    assert response["ok"], response.get("traceback")
    assert response["stdout"].strip() == "0"
    assert counting_session.recomputes == 0


def test_a_step_that_deletes_something_still_gets_the_full_recompute(counting_session):
    """
    `_recompute_scope` has nothing to point `doc.recompute` at once an object
    is gone -- the deletion is exactly the case the full recompute stays for,
    so `touched()` being true from a deletion alone must still run it.
    """
    counting_session.Objects = [type("Obj", (), {"Name": "Gone"})()]

    code = "doc.Objects.pop()"
    response = kernel_worker.run_step({"code": code})

    assert response["ok"], response.get("traceback")
    assert counting_session.recomputes == 1
