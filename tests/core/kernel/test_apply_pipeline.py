"""
Level 1 — the order the apply pipeline does things in, and what the caller sees.

Everything is injected: no Qt, no FreeCAD, no worker. What matters here is that
lint runs before the kernel's rehearsal, the transaction never opens on a Step
that changed nothing, and none of that is visible from outside — the caller
gets "applied" or "failed". `FakeKernel` is the kernel: it answers the
rehearsal and the seed with whatever result the test sets.
"""
import pytest

from core.agent import part_rules
from core.freecad_apply import CHAT_DOCK, ApplyPipeline, Closing, SignatureMismatch
from core.kernel import KernelResult
from tests.fake_kernel import FakeKernel


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


def _kernel_result(created=("Box",), modified=(), deleted=(), names=None, signature=None):
    made = list(created)
    return KernelResult(ok=True, created=made, deleted=list(deleted), modified=list(modified),
                        objects=[{"name": name, "type": "Part::Box"} for name in made],
                        names=names or {}, signature=signature or {})


def applying(pipeline, code, rehearsal=None, **kwargs):
    if rehearsal is not None:
        pipeline.worker.rehearsal = rehearsal
    return pipeline.apply(code, **kwargs)


@pytest.fixture
def trace():
    return []


@pytest.fixture
def pipeline(trace):
    doc = FakeDocument()
    worker = FakeKernel()

    def transaction(document, code, label, **_):
        trace.append(("transaction", label))
        document.Objects.append(FakeObject("Box"))

    def dispatch(func, timeout=None):
        return func()

    built = ApplyPipeline(document=doc, worker=worker, dispatch=dispatch,
                          transaction=transaction)
    built.document, built.fake_worker, built.trace = doc, worker, trace
    return built


def test_a_clean_step_lands_and_reports_what_it_created(pipeline):
    """
    An Apply lands a Step and describes what is now there. It takes no picture.
    """
    result = applying(pipeline, "box = doc.addObject('Part::Box', 'Box')", rehearsal=_kernel_result())

    assert result.ok
    assert result.created == ["Box"]
    assert [what for what, _ in pipeline.trace] == ["transaction"]


def test_a_step_that_lands_puts_the_part_back_in_the_viewport(pipeline, monkeypatch):
    """
    The camera keeps whatever it was on before the run, so a part modelled at
    the drawing's own scale lands a few pixels across or outside the viewport
    altogether, and the user watching the Plan go by cannot see the Step they
    are being asked to approve.
    """
    fitted = []
    monkeypatch.setattr("core.freecad_apply.fit_view", lambda doc: fitted.append(doc))

    assert applying(pipeline, "doc.recompute()", rehearsal=_kernel_result()).ok
    assert fitted == [pipeline.document]


def test_a_step_that_lands_shows_the_built_result(pipeline, monkeypatch):
    """
    A run cut off mid-Step can leave the built geometry hidden behind
    PartDesign's own consumption behaviour, with nothing to correct it —
    `apply()` shows the landed Step's own result every time, not only on the
    run's last Step.
    """
    shown = []
    monkeypatch.setattr("core.freecad_apply._show_tip", lambda doc: shown.append(doc))

    assert applying(pipeline, "doc.recompute()", rehearsal=_kernel_result()).ok
    assert shown == [pipeline.document]


def test_a_step_the_kernel_says_changed_nothing_leaves_the_camera_alone(pipeline, monkeypatch):
    """
    Seen in the wild: a Step that defined a function to build the sketch and
    never called it. It raised nothing, so it scored as applied, and the Plan
    moved on to a sketch that did not exist. `apply()` now reads this off
    what the kernel already found, not off a rehearsal of its own.
    """
    fitted = []
    monkeypatch.setattr("core.freecad_apply.fit_view", lambda doc: fitted.append(doc))

    result = applying(pipeline, "def build(doc):\n    doc.addObject('Part::Box', 'Box')\n", rehearsal=_kernel_result(created=[]))

    assert not result.ok
    assert "changed nothing" in result.traceback
    assert result.gate == "changed_nothing"
    assert pipeline.trace == [], "a Step that does nothing still opened a transaction"
    assert fitted == []


def test_a_step_that_only_rewrites_a_sketchs_geometry_still_lands(pipeline):
    """
    Seen in the wild: a Step deleted a sketch's geometry and drew
    it again as a clean capsule. `signature_of()` does not see into a
    Sketch's geometry, so it reads unchanged even though the rewrite is
    real. The kernel's own `modified` is enough on its own -- it is not
    second-guessed against a signature that cannot see this kind of change.
    """
    result = applying(pipeline, "sk = doc.getObject('Sketch')\n", rehearsal=_kernel_result(created=[], modified=["Sketch"]))

    assert result.ok, result.traceback


def test_lint_runs_before_the_transaction_so_a_bad_step_costs_nothing(pipeline):
    result = applying(pipeline, "fillet.Base = (box, ['Edge12'])", rehearsal=_kernel_result())

    assert not result.ok
    assert "literal element name" in result.traceback
    assert result.gate == "lint"
    assert pipeline.trace == []


def test_the_caller_sees_applied_or_failed_and_nothing_else(pipeline):
    applied = applying(pipeline, "doc.recompute()", rehearsal=_kernel_result()).as_tool_result()
    failed = applying(pipeline, "doc.recompute()", rehearsal=_kernel_result(created=[])).as_tool_result()

    assert applied["status"] == "applied"
    assert failed["status"] == "failed"
    assert "changed nothing" in failed["traceback"]
    assert failed["gate"] == "changed_nothing"
    assert "preflight" not in repr(applied).lower()
    assert set(applied) == {"status", "created", "modified", "description", "deleted", "geometry"}


def test_lint_runs_once_with_the_selection(pipeline, monkeypatch):
    import core.freecad_apply as module

    linted = []
    real = module.lint

    def counting(code, selection=None):
        linted.append(selection)
        return real(code, selection)

    monkeypatch.setattr(module, "lint", counting)
    selection = [{"object": "Box", "elements": ["Edge3"]}]

    result = applying(pipeline, "fillet.Base = (box, ['Edge3'])", selection=selection,
                      rehearsal=_kernel_result(created=["Fillet"]))

    assert result.ok, result.traceback
    assert linted == [selection]


def test_the_kernel_is_asked_to_rehearse_only_what_lint_let_through(pipeline):
    applying(pipeline, "fillet.Base = (box, ['Edge12'])", rehearsal=_kernel_result())

    assert pipeline.fake_worker.rehearsed == []


def test_a_step_the_kernel_refuses_never_reaches_the_document(pipeline):
    refusal = KernelResult(ok=False, traceback="boom", volume={"before": 1.0, "after": 0.0},
                           stdout="said")

    result = applying(pipeline, "box.fail()", rehearsal=refusal)

    assert not result.ok and result.gate == ""
    assert result.as_tool_result()["traceback"] == "boom"
    assert result.as_tool_result()["volume"] == {"before": 1.0, "after": 0.0}
    assert pipeline.trace == []
    assert pipeline.fake_worker.rehearsed == ["box.fail()"]


def test_a_step_that_changes_nothing_is_refused_with_the_prompt_message(pipeline):
    from core.prompts import read_prompt

    result = applying(pipeline, "print(1)", rehearsal=_kernel_result(created=[]))

    assert result.traceback == read_prompt("changed_nothing.md")
    assert pipeline.fake_worker.rehearsed == ["print(1)"]


def test_a_refusal_resyncs_the_kernel(trace):
    def transaction(document, code, label, **_):
        raise RuntimeError("the live Document disagreed")

    worker = FakeKernel()
    pipeline = ApplyPipeline(document=FakeDocument(), worker=worker,
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)

    result = pipeline.apply("doc.recompute()")

    assert not result.ok
    assert worker.rehearsed == ["doc.recompute()"]
    assert worker.seeded[-1] is not None


def test_a_resync_keeps_the_rules_the_run_started_under(trace):
    def transaction(document, code, label, **_):
        raise RuntimeError("the live Document disagreed")

    worker = FakeKernel()
    pipeline = ApplyPipeline(document=FakeDocument(), worker=worker,
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)
    pipeline.start_run(part_rules)

    pipeline.apply("doc.recompute()")

    assert worker.rules == [part_rules, part_rules]


def test_a_step_that_lands_does_not_resync_the_kernel(pipeline):
    assert pipeline.apply("doc.recompute()").ok

    assert pipeline.fake_worker.rehearsed == ["doc.recompute()"]


def test_a_step_that_the_kernel_ran_but_fails_for_real_is_reported_as_such(trace):
    def transaction(document, code, label, **_):
        raise RuntimeError("the live Document disagreed")

    pipeline = ApplyPipeline(document=FakeDocument(), worker=FakeKernel(),
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)

    result = applying(pipeline, "doc.recompute()", rehearsal=_kernel_result())

    assert not result.ok
    assert "rolled back" in result.traceback
    assert "the live Document disagreed" in result.traceback
    assert result.gate == "rollback"


def test_a_replay_that_drifts_names_drift_as_its_cause(trace):
    def transaction(document, code, label, **_):
        raise SignatureMismatch({"volume": 2.0}, {"volume": 1.0})

    pipeline = ApplyPipeline(document=FakeDocument(), worker=FakeKernel(),
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)

    result = applying(pipeline, "doc.recompute()", rehearsal=_kernel_result())

    assert result.drift
    assert result.gate == "drift"


def test_closing_during_the_transaction_is_a_clean_stop_not_a_failed_step(trace):
    def transaction(document, code, label, **_):
        raise Closing("shutting down")

    pipeline = ApplyPipeline(document=FakeDocument(), worker=FakeKernel(),
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)

    with pytest.raises(Closing):
        applying(pipeline, "doc.recompute()", rehearsal=_kernel_result())


def test_no_open_document_is_a_reason_not_a_crash():
    pipeline = ApplyPipeline(document=lambda: None, worker=FakeKernel(),
                             dispatch=lambda f, timeout=None: f())

    result = applying(pipeline, "doc.recompute()", rehearsal=_kernel_result())

    assert not result.ok
    assert "no open Document" in result.traceback
    assert result.gate == "no_document"


def test_an_element_the_user_selected_survives_the_lint(pipeline):
    result = applying(pipeline, "fillet.Base = (box, ['Edge3'])",
                      selection=[{"object": "Box", "elements": ["Edge3"]}],
                      rehearsal=_kernel_result(created=["Fillet"]))

    assert result.ok, result.traceback


def test_a_fillet_that_never_says_how_many_edges_never_reaches_the_document(pipeline):
    """
    A filter that finds eight of the marked edges rounds eight of them and
    reports the Step applied. It fails before the Document sees it.
    """
    result = applying(pipeline, "refs = []\n"
        "for i, e in enumerate(base.Shape.Edges, 1):\n"
        "    if abs(e.Vertexes[0].Point.x - e.Vertexes[1].Point.x) < 1e-7:\n"
        "        refs.append('Edge%d' % i)\n"
        "fillet.Base = (base, refs)\n", rehearsal=_kernel_result())

    assert not result.ok
    assert "how many" in result.traceback
    assert pipeline.trace == []


def test_every_document_touch_goes_through_the_dispatcher():
    """
    Every document touch goes through the dispatcher: resolve the Document,
    read what is there before, run the transaction, show the landed Step's
    result, fit the view, describe what is there now. There is no rehearsal
    copy any more to dispatch a sixth time.

    The viewport is one of them. A camera moved off the main thread is a Coin
    scene graph touched from a worker, which is the crash the dispatcher exists
    to prevent.
    """
    dispatched = []

    def dispatch(func, timeout=None):
        dispatched.append(func)
        return func()

    doc = FakeDocument()
    pipeline = ApplyPipeline(document=doc, worker=FakeKernel(), dispatch=dispatch,
                             transaction=lambda *a, **kw: None)

    applying(pipeline, "doc.recompute()", rehearsal=_kernel_result())

    assert len(dispatched) == 6


class FakeWidget:
    """One of the main window's own widgets: a toolbar, the 3D view, a dock."""

    def __init__(self, name=""):
        self.name = name
        self.enabled = True

    def isWidgetType(self):
        return True

    def objectName(self):
        return self.name

    def setEnabled(self, value):
        self.enabled = value


class FakeAction(FakeWidget):
    """A `QAction` is a child of the window too, and is not a widget."""

    def isWidgetType(self):
        return False


class FakeMainWindow:
    """
    Stands in for `Gui.getMainWindow()`: what a run's lock reaches through.

    Carries the chat dock among its children because the exemption is the
    point of the lock — a window locked whole takes the Plan Gate's own
    buttons with it.
    """

    def __init__(self):
        self.chat = FakeWidget(CHAT_DOCK)
        self.viewport = FakeWidget("3d")
        self.action = FakeAction("undo")

    def children(self):
        return [self.viewport, self.chat, self.action]


def test_start_run_snapshots_the_document_into_the_kernel(pipeline):
    """
    AC1: the kernel-side object count matches the GUI Document at run start.
    """
    pipeline.document.Objects = [FakeObject("Box"), FakeObject("Cylinder")]
    pipeline.fake_worker.rehearsal = KernelResult(ok=True, objects=[{"name": "Box"},
                                                                   {"name": "Cylinder"}])
    seeded = pipeline.start_run()

    assert len(seeded.objects) == len(pipeline.document.Objects)
    assert pipeline.fake_worker.seeded[-1] is not None


def test_a_run_in_flight_locks_the_gui(pipeline):
    """AC2: the GUI is locked, not silently left open, while a run is in flight."""
    window = FakeMainWindow()
    pipeline._main_window = window

    pipeline.lock()

    assert pipeline.locked is True
    assert window.viewport.enabled is False


def test_the_lock_leaves_the_chat_alone(pipeline):
    """
    The Plan Gate's Build button, Stop and the composer are all in the chat
    dock. Locked with the rest of the window, the run waits at the Gate for an
    answer the user has no way left to give.
    """
    window = FakeMainWindow()
    pipeline._main_window = window

    pipeline.lock()

    assert window.chat.enabled is True


def test_the_lock_leaves_the_windows_actions_to_the_application(pipeline):
    """A `QAction` is not a widget, and re-enabling one is not the lock's to do."""
    window = FakeMainWindow()
    pipeline._main_window = window
    window.action.enabled = False

    pipeline.lock()
    pipeline.end_run()

    assert window.action.enabled is False


def test_a_run_that_ends_unlocks_the_gui(pipeline):
    """AC3: after the run ends, the GUI is free again."""
    window = FakeMainWindow()
    pipeline._main_window = window
    pipeline.lock()

    pipeline.end_run()

    assert pipeline.locked is False
    assert window.viewport.enabled is True


def test_start_run_with_no_open_document_seeds_an_empty_one(pipeline):
    pipeline._document = lambda: None

    seeded = pipeline.start_run()

    assert seeded.objects == []
    assert pipeline.fake_worker.seeded[-1] is None
