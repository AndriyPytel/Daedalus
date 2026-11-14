"""
Level 1 -- the replay signature contract, the ApplyPipeline half:
how the allocated-name map crosses from one Step's Preflight into the next
Step's replay. Everything injected, same as `test_apply_pipeline.py` --
`run_in_transaction` itself needs a real FreeCAD Document
(`tests/test_replay_signature_level2.py`).
"""
import pytest

from core.freecad_apply import ApplyPipeline
from core.kernel import KernelResult
from tests.fake_kernel import FakeKernel


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
    """A window with nothing on it: this file is about the name map, not the lock."""

    def children(self):
        return []


@pytest.fixture
def trace():
    return []


def _pipeline(trace, result):
    doc = FakeDocument()
    worker = FakeKernel(result)

    def transaction(document, code, label, names=None, expected_signature=None):
        trace.append(("transaction", label, dict(names or {})))
        document.Objects.append(FakeObject("Box001"))

    def dispatch(func, timeout=None):
        return func()

    built = ApplyPipeline(document=doc, worker=worker, dispatch=dispatch,
                          transaction=transaction)
    built.fake_worker = worker
    return built


def test_apply_hands_the_accumulated_names_to_the_transaction(trace):
    pipeline = _pipeline(trace, KernelResult(ok=True, created=["Box001"], names={"Box": "Box001"}))
    pipeline._names = {"Earlier": "Earlier001"}

    pipeline.apply("box = doc.addObject('Part::Box', 'Box')")

    assert trace[0][2] == {"Earlier": "Earlier001"}, \
        "the replay's own namespace did not see the accumulator's own names"


def test_a_successful_apply_updates_the_accumulator_from_the_rehearsal(trace):
    pipeline = _pipeline(trace, KernelResult(ok=True, created=["Box001"], names={"Box": "Box001"}))

    outcome = pipeline.apply("box = doc.addObject('Part::Box', 'Box')")

    assert outcome.ok, outcome.traceback
    assert pipeline._names == {"Box": "Box001"}
    assert trace[0][2] == {}, \
        "the replay's own namespace did not see what the kernel allocated"


def test_start_run_resets_the_accumulated_names():
    doc = FakeDocument()
    worker = FakeKernel(KernelResult(ok=True))

    def dispatch(func, timeout=None):
        return func()

    pipeline = ApplyPipeline(document=doc, worker=worker, dispatch=dispatch)
    pipeline._main_window = FakeMainWindow()
    pipeline._names = {"Box": "Box001"}

    pipeline.start_run()

    assert pipeline._names == {}
