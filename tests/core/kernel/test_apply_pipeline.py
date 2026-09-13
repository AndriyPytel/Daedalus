"""
Level 1 — the order the apply pipeline does things in, and what the caller sees.

Everything is injected: no Qt, no FreeCAD, no worker. What matters here is that
lint runs before the transaction, the transaction never opens on a Step that
changed nothing, and none of that is visible from outside — the caller gets
"applied" or "failed". There is no rehearsal here any more: the caller hands
`apply()` `kernel_result`, what
a real kernel already found running the Step for real, and `FakeWorker` here
is only `start_run`'s own seed call, untouched by this ticket.
"""
import pytest

from core.freecad_apply import CHAT_DOCK, ApplyPipeline, Closing
from core.kernel import KernelResult


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
    """Answers `start_run`'s own seed call however the test says."""

    def __init__(self, result=None):
        self.result = result or KernelResult(ok=True, render="/tmp/step.png",
                                                created=["Box"])
        self.checked = []

    def check(self, code, document=None, render_path=None, timeout=None, names=None,
              ephemeral=False):
        self.checked.append({"code": code, "document": document,
                             "render_path": render_path})
        return self.result


def _kernel_result(created=("Box",), modified=(), objects=None, signature=None, **rest):
    """
    A `kernel_result` shaped like what `apply()` now expects a caller to have
    from the kernel's own run of the Step, rather than a rehearsal `apply()`
    runs itself.
    """
    made = list(created)
    return dict(created=made, deleted=[], modified=list(modified),
               objects=(objects if objects is not None
                        else [{"name": name, "type": "Part::Box"} for name in made]),
               names={}, signature=(signature if signature is not None else {}), **rest)


@pytest.fixture
def trace():
    return []


@pytest.fixture
def pipeline(trace):
    doc = FakeDocument()
    worker = FakeWorker()

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
    result = pipeline.apply("box = doc.addObject('Part::Box', 'Box')", label="Add the box",
                            kernel_result=_kernel_result())

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

    assert pipeline.apply("doc.recompute()", kernel_result=_kernel_result()).ok
    assert fitted == [pipeline.document]


def test_a_step_the_kernel_says_changed_nothing_leaves_the_camera_alone(pipeline, monkeypatch):
    """
    Seen in the wild: a Step that defined a function to build the sketch and
    never called it. It raised nothing, so it scored as applied, and the Plan
    moved on to a sketch that did not exist. `apply()` now reads this off
    what the kernel already found, not off a rehearsal of its own.
    """
    fitted = []
    monkeypatch.setattr("core.freecad_apply.fit_view", lambda doc: fitted.append(doc))

    result = pipeline.apply("def build(doc):\n    doc.addObject('Part::Box', 'Box')\n",
                            kernel_result=_kernel_result(created=[]))

    assert not result.ok
    assert "changed nothing" in result.traceback
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
    result = pipeline.apply(
        "sk = doc.getObject('Sketch')\n",
        kernel_result=_kernel_result(created=[], modified=["Sketch"]),
    )

    assert result.ok, result.traceback


def test_lint_runs_before_the_transaction_so_a_bad_step_costs_nothing(pipeline):
    result = pipeline.apply("fillet.Base = (box, ['Edge12'])", kernel_result=_kernel_result())

    assert not result.ok
    assert "literal element name" in result.traceback
    assert pipeline.trace == []


def test_the_caller_sees_applied_or_failed_and_nothing_else(pipeline):
    applied = pipeline.apply("doc.recompute()", kernel_result=_kernel_result()).as_tool_result()
    failed = pipeline.apply("doc.recompute()", kernel_result=_kernel_result(created=[])).as_tool_result()

    assert applied["status"] == "applied"
    assert failed["status"] == "failed"
    assert "changed nothing" in failed["traceback"]
    assert "preflight" not in repr(applied).lower()
    assert set(applied) == {"status", "created", "modified", "description"}


def test_a_step_that_the_kernel_ran_but_fails_for_real_is_reported_as_such(trace):
    def transaction(document, code, label, **_):
        raise RuntimeError("the live Document disagreed")

    pipeline = ApplyPipeline(document=FakeDocument(), worker=FakeWorker(),
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)

    result = pipeline.apply("doc.recompute()", kernel_result=_kernel_result())

    assert not result.ok
    assert "rolled back" in result.traceback
    assert "the live Document disagreed" in result.traceback


def test_closing_during_the_transaction_is_a_clean_stop_not_a_failed_step(trace):
    def transaction(document, code, label, **_):
        raise Closing("shutting down")

    pipeline = ApplyPipeline(document=FakeDocument(), worker=FakeWorker(),
                             dispatch=lambda f, timeout=None: f(), transaction=transaction)

    with pytest.raises(Closing):
        pipeline.apply("doc.recompute()", kernel_result=_kernel_result())


def test_no_open_document_is_a_reason_not_a_crash():
    pipeline = ApplyPipeline(document=lambda: None, worker=FakeWorker(),
                             dispatch=lambda f, timeout=None: f())

    result = pipeline.apply("doc.recompute()", kernel_result=_kernel_result())

    assert not result.ok
    assert "no open Document" in result.traceback


def test_an_element_the_user_selected_survives_the_lint(pipeline):
    result = pipeline.apply("fillet.Base = (box, ['Edge3'])",
                            selection=[{"object": "Box", "elements": ["Edge3"]}],
                            kernel_result=_kernel_result(created=["Fillet"]))

    assert result.ok, result.traceback


def test_a_fillet_that_never_says_how_many_edges_never_reaches_the_document(pipeline):
    """
    A filter that finds eight of the marked edges rounds eight of them and
    reports the Step applied. It fails before the Document sees it.
    """
    result = pipeline.apply(
        "refs = []\n"
        "for i, e in enumerate(base.Shape.Edges, 1):\n"
        "    if abs(e.Vertexes[0].Point.x - e.Vertexes[1].Point.x) < 1e-7:\n"
        "        refs.append('Edge%d' % i)\n"
        "fillet.Base = (base, refs)\n",
        kernel_result=_kernel_result())

    assert not result.ok
    assert "how many" in result.traceback
    assert pipeline.trace == []


def test_review_views_reads_the_pipelines_own_document(pipeline, monkeypatch):
    """
    Review captures the Session's own bound Document, not whichever
    tab is in front — the same Document `apply` just landed the Step on.
    """
    seen = []
    monkeypatch.setattr("core.freecad_apply.capture_review_views",
                        lambda paths, doc, dispatch: seen.append(doc) or paths)

    pipeline.review_views()

    assert seen == [pipeline.document]


def test_every_document_touch_goes_through_the_dispatcher():
    """
    Every document touch goes through the dispatcher: resolve the Document,
    read what is there before, run the transaction, fit the view, describe
    what is there now. There is no rehearsal copy any more to dispatch a
    sixth time.

    The viewport is one of them. A camera moved off the main thread is a Coin
    scene graph touched from a worker, which is the crash the dispatcher exists
    to prevent.
    """
    dispatched = []

    def dispatch(func, timeout=None):
        dispatched.append(func)
        return func()

    doc = FakeDocument()
    pipeline = ApplyPipeline(document=doc, worker=FakeWorker(), dispatch=dispatch,
                             transaction=lambda *a, **kw: None)

    pipeline.apply("doc.recompute()", kernel_result=_kernel_result())

    assert len(dispatched) == 5


def test_an_op_mismatch_reaches_apply_the_same_way_a_lint_rejection_does(pipeline):
    """
    Whether a Step built the TypeId its `op` promises is read off
    `kernel_result`'s own `created`/`objects`, checked before the
    transaction ever sees the code.
    """
    result = pipeline.apply(
        "doc.addObject('Part::Extrusion', 'Pad')", op="pad",
        kernel_result=_kernel_result(created=["Pad"],
                                     objects=[{"name": "Pad", "type": "Part::Extrusion"}]))

    assert not result.ok
    assert "pad" in result.traceback
    assert pipeline.trace == []


def test_an_op_none_never_blocks_a_step_the_old_pipeline_would_have_applied(pipeline):
    result = pipeline.apply(
        "doc.addObject('PartDesign::Pad', 'Pad')",
        kernel_result=_kernel_result(created=["Pad"],
                                     objects=[{"name": "Pad", "type": "PartDesign::Pad"}]))

    assert result.ok


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
    pipeline.fake_worker.result = KernelResult(ok=True, objects=[{"name": "Box"},
                                                                   {"name": "Cylinder"}])
    window = FakeMainWindow()
    pipeline._main_window = window

    seeded = pipeline.start_run()

    assert len(seeded.objects) == len(pipeline.document.Objects)
    assert pipeline.fake_worker.checked[-1]["document"] is not None


def test_a_run_in_flight_locks_the_gui(pipeline):
    """AC2: the GUI is locked, not silently left open, while a run is in flight."""
    window = FakeMainWindow()
    pipeline._main_window = window

    pipeline.start_run()

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

    pipeline.start_run()

    assert window.chat.enabled is True


def test_the_lock_leaves_the_windows_actions_to_the_application(pipeline):
    """A `QAction` is not a widget, and re-enabling one is not the lock's to do."""
    window = FakeMainWindow()
    pipeline._main_window = window
    window.action.enabled = False

    pipeline.start_run()
    pipeline.end_run()

    assert window.action.enabled is False


def test_a_run_that_ends_unlocks_the_gui(pipeline):
    """AC3: after the run ends, the GUI is free again."""
    window = FakeMainWindow()
    pipeline._main_window = window
    pipeline.start_run()

    pipeline.end_run()

    assert pipeline.locked is False
    assert window.viewport.enabled is True


def test_start_run_with_no_open_document_still_locks(pipeline):
    """No Document open is not a reason to leave the GUI unlocked or crash."""
    pipeline._document = lambda: None
    window = FakeMainWindow()
    pipeline._main_window = window

    seeded = pipeline.start_run()

    assert pipeline.locked is True
    assert seeded.objects == []
