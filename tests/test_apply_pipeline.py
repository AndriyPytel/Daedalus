"""
Level 1 — the order the apply pipeline does things in, and what the caller sees.

Everything is injected: no Qt, no FreeCAD, no worker. What matters here is that
lint runs before Preflight, Preflight runs before the Document, and none of
that is visible from outside — the caller gets "applied" or "failed".
"""
import pytest

from core.apply import ApplyPipeline
from core.preflight import PreflightResult


class FakeDocument:
    def __init__(self, names=()):
        self.Objects = [FakeObject(n) for n in names]
        self.saved_copies = []

    def saveCopy(self, path):
        self.saved_copies.append(path)
        with open(path, "w") as handle:
            handle.write("not really an FCStd")


class FakeObject:
    def __init__(self, name):
        self.Name = name
        self.Label = name
        self.TypeId = "Part::Box"


class FakeWorker:
    """Records what it was asked to Preflight, answers however the test says."""

    def __init__(self, result=None):
        # A real worker reports what the Step touched, and a Step that touched
        # nothing is not a success — so the default here has to say it made one.
        self.result = result or PreflightResult(ok=True, render="/tmp/step.png",
                                                created=["Box"])
        self.checked = []

    def check(self, code, document=None, render_path=None, timeout=None):
        self.checked.append({"code": code, "document": document,
                             "render_path": render_path})
        return self.result


@pytest.fixture
def trace():
    return []


@pytest.fixture
def pipeline(trace):
    doc = FakeDocument()
    worker = FakeWorker()

    def transaction(document, code, label):
        trace.append(("transaction", label))
        document.Objects.append(FakeObject("Box"))

    def dispatch(func, timeout=None):
        return func()

    def view_capture(path):
        # The viewport, which Level 1 has none of. Injected the way `dispatch`
        # and `transaction` already are (ADR-0015).
        trace.append(("capture", path))
        return "/tmp/step.png"

    built = ApplyPipeline(document=doc, worker=worker, dispatch=dispatch,
                          transaction=transaction, view_capture=view_capture)
    built.document, built.fake_worker, built.trace = doc, worker, trace
    return built


# ── the order ─────────────────────────────────────────────────────────────────


def test_a_clean_step_lands_and_reports_what_it_created(pipeline):
    result = pipeline.apply("box = doc.addObject('Part::Box', 'Box')", label="Add the box")

    assert result.ok
    assert result.created == ["Box"]
    assert result.render == "/tmp/step.png"
    # The picture is taken after the transaction, from the viewport the Step
    # has already changed — not from the throwaway copy (ADR-0015).
    assert [what for what, _ in pipeline.trace] == ["transaction", "capture"]


def test_lint_runs_before_preflight_so_a_bad_step_costs_nothing(pipeline):
    result = pipeline.apply("fillet.Base = (box, ['Edge12'])")

    assert not result.ok
    assert "literal element name" in result.traceback
    assert pipeline.fake_worker.checked == [], "Preflight ran on code lint had already rejected"
    assert pipeline.trace == []


def test_a_step_that_changes_nothing_is_not_a_success(pipeline):
    """
    Seen in the wild: a Step that defined a function to build the sketch and
    never called it. It raised nothing, so it scored as applied, and the Plan
    moved on to a sketch that did not exist.
    """
    pipeline.fake_worker.result = PreflightResult(ok=True, render="/tmp/step.png")

    result = pipeline.apply("def build(doc):\n    doc.addObject('Part::Box', 'Box')\n")

    assert not result.ok
    assert "changed nothing" in result.traceback
    assert pipeline.trace == [], "a Step that does nothing still opened a transaction"


def test_preflight_runs_before_the_document_sees_anything(pipeline):
    pipeline.fake_worker.result = PreflightResult(ok=False, traceback="ValueError: no such property")

    result = pipeline.apply("box.Nonsense = 1")

    assert not result.ok
    assert result.traceback == "ValueError: no such property"
    assert pipeline.fake_worker.checked, "Preflight was skipped"
    assert pipeline.trace == [], "the Document was touched despite a failed Preflight"


def test_preflight_gets_a_throwaway_copy_not_the_document(pipeline):
    pipeline.apply("doc.recompute()")

    checked = pipeline.fake_worker.checked[0]
    assert pipeline.document.saved_copies == [checked["document"]]
    # Preflight no longer renders: the panel's picture is the viewport (ADR-0015).
    assert checked["render_path"] is None


def test_the_throwaway_copy_is_deleted_afterwards(pipeline):
    import os

    pipeline.apply("doc.recompute()")

    assert not os.path.exists(pipeline.fake_worker.checked[0]["document"])


# ── Preflight is behind the Tool, not beside it ───────────────────────────────


def test_the_caller_sees_applied_or_failed_and_nothing_else(pipeline):
    applied = pipeline.apply("doc.recompute()").as_tool_result()
    pipeline.fake_worker.result = PreflightResult(ok=False, traceback="boom")
    failed = pipeline.apply("doc.recompute()").as_tool_result()

    assert applied["status"] == "applied"
    assert failed == {"status": "failed", "traceback": "boom"}
    # Nothing in either result names Preflight, the worker, or a copy.
    assert "preflight" not in repr(applied).lower()
    assert set(applied) == {"status", "created", "objects", "render"}


def test_a_worker_that_died_reads_as_a_failed_step_not_an_incident(pipeline):
    pipeline.fake_worker.result = PreflightResult(
        ok=False, traceback="The Preflight Worker died while running the Step, ...")

    result = pipeline.apply("import ctypes; ctypes.string_at(0)")

    assert not result.ok
    assert pipeline.trace == []


# ── failure against the real Document ─────────────────────────────────────────


def test_a_step_that_passes_preflight_but_fails_for_real_is_reported_as_such(trace):
    def transaction(document, code, label):
        raise RuntimeError("the live Document disagreed")

    pipeline = ApplyPipeline(document=FakeDocument(), worker=FakeWorker(),
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)

    result = pipeline.apply("doc.recompute()")

    assert not result.ok
    assert "rolled back" in result.traceback
    assert "the live Document disagreed" in result.traceback


def test_no_open_document_is_a_reason_not_a_crash():
    pipeline = ApplyPipeline(document=lambda: None, worker=FakeWorker(),
                             dispatch=lambda f, timeout=None: f())

    result = pipeline.apply("doc.recompute()")

    assert not result.ok
    assert "no open Document" in result.traceback


# ── the selection reaches the lint ────────────────────────────────────────────


def test_an_element_the_user_selected_survives_the_lint(pipeline):
    result = pipeline.apply("fillet.Base = (box, ['Edge3'])",
                            selection=[{"object": "Box", "elements": ["Edge3"]}])

    assert result.ok, result.traceback


# ── everything is marshalled ──────────────────────────────────────────────────


def test_every_document_touch_goes_through_the_dispatcher():
    dispatched = []

    def dispatch(func, timeout=None):
        dispatched.append(func)
        return func()

    doc = FakeDocument()
    pipeline = ApplyPipeline(document=doc, worker=FakeWorker(), dispatch=dispatch,
                             transaction=lambda *a: None)

    pipeline.apply("doc.recompute()")

    # resolve the Document, read it, copy it, run the transaction, read it
    # again, capture the viewport — which is Qt's thread too (ADR-0015).
    assert len(dispatched) == 6
