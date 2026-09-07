"""
Level 1 -- the replay signature contract (FCC-302), the ApplyPipeline half:
how the allocated-name map crosses from one Step's Preflight into the next
Step's replay. Everything injected, same as `test_apply_pipeline.py` --
`run_in_transaction` itself needs a real FreeCAD Document
(`tests/test_replay_signature_level2.py`).
"""
import pytest

from core.freecad.apply import ApplyPipeline
from core.kernel import KernelResult


class FakeObject:
    def __init__(self, name):
        self.Name = name
        self.Label = name
        self.TypeId = "Part::Box"


class FakeDocument:
    def __init__(self, names=()):
        self.Objects = [FakeObject(n) for n in names]

    def saveCopy(self, path):
        with open(path, "w") as handle:
            handle.write("not really an FCStd")


class FakeMainWindow:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, value):
        self.enabled = value


class FakeWorker:
    """Records what it was asked, including the name map (FCC-302)."""

    def __init__(self, result=None):
        self.result = result
        self.checked = []

    def check(self, code, document=None, render_path=None, timeout=None, names=None):
        self.checked.append({"code": code, "document": document, "names": names})
        return self.result


@pytest.fixture
def trace():
    return []


def _pipeline(trace, result):
    doc = FakeDocument()
    worker = FakeWorker(result)

    def transaction(document, code, label, names=None, expected_signature=None):
        trace.append(("transaction", label, dict(names or {})))
        document.Objects.append(FakeObject("Box001"))

    def dispatch(func, timeout=None):
        return func()

    built = ApplyPipeline(document=doc, worker=worker, dispatch=dispatch,
                          transaction=transaction)
    built.fake_worker = worker
    return built


def test_apply_hands_the_accumulated_names_to_preflight(trace):
    result = KernelResult(ok=True, created=["Box001"], names={"Box": "Box001"})
    pipeline = _pipeline(trace, result)
    pipeline._names = {"Earlier": "Earlier001"}

    pipeline.apply("box = doc.addObject(\'Part::Box\', \'Box\')")

    assert pipeline.fake_worker.checked[0]["names"] == {"Earlier": "Earlier001"}


def test_a_successful_apply_updates_the_accumulator_from_preflight(trace):
    result = KernelResult(ok=True, created=["Box001"], names={"Box": "Box001"})
    pipeline = _pipeline(trace, result)

    outcome = pipeline.apply("box = doc.addObject(\'Part::Box\', \'Box\')")

    assert outcome.ok, outcome.traceback
    assert pipeline._names == {"Box": "Box001"}
    assert trace[0][2] == {"Box": "Box001"}, \
        "the replay\'s own namespace did not see what Preflight allocated"


def test_start_run_resets_the_accumulated_names():
    doc = FakeDocument()
    worker = FakeWorker(KernelResult(ok=True))

    def dispatch(func, timeout=None):
        return func()

    pipeline = ApplyPipeline(document=doc, worker=worker, dispatch=dispatch)
    pipeline._main_window = FakeMainWindow()
    pipeline._names = {"Box": "Box001"}

    pipeline.start_run()

    assert pipeline._names == {}
