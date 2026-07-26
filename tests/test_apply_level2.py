"""
Level 2 — the apply pipeline against a real Document and a real Preflight Worker.

The two claims a fake cannot make: one undo takes a whole Step back, and code
that crashes the C++ core leaves this process — standing in for the user's
FreeCAD — alive.
"""
import pytest

from core.apply import ApplyPipeline, run_in_transaction
from core.freecad_tools import build_registry
from core.preflight import PreflightWorker, find_freecadcmd

pytestmark = pytest.mark.freecad

CRASHING_CODE = "import ctypes; ctypes.string_at(0)"

BOX = "box = doc.addObject('Part::Box', 'Box')\nbox.Length = 10\n"


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with PreflightWorker() as running:
        yield running


@pytest.fixture
def doc():
    import FreeCAD

    document = FreeCAD.newDocument("ApplyLevel2")
    document.UndoMode = 1          # the GUI turns this on; headless does not
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


@pytest.fixture
def pipeline(doc, worker, tmp_path):
    # Already on the main thread here, so the dispatcher is a straight call.
    return ApplyPipeline(document=doc, worker=worker,
                         dispatch=lambda func, timeout=None: func(),
                         render_dir=str(tmp_path))


# ── a Step lands ──────────────────────────────────────────────────────────────


def test_a_step_lands_in_the_document(pipeline, doc):
    result = pipeline.apply(BOX, label="Add the box")

    assert result.ok, result.traceback
    assert result.created == ["Box"]
    assert doc.getObject("Box").Length.Value == 10


def test_a_step_brings_back_the_render_review_will_judge_by(pipeline):
    import os

    result = pipeline.apply(BOX)

    assert os.path.getsize(result.render) > 1000


# ── one undo takes the whole Step back ────────────────────────────────────────


def test_a_single_undo_takes_a_whole_step_back(pipeline, doc):
    """Several objects, one Step, one Ctrl+Z."""
    result = pipeline.apply(
        "box = doc.addObject('Part::Box', 'Box')\n"
        "cyl = doc.addObject('Part::Cylinder', 'Cyl')\n"
        "cut = doc.addObject('Part::Cut', 'Cut')\n"
        "cut.Base, cut.Tool = box, cyl\n",
        label="Box minus cylinder",
    )
    assert result.ok, result.traceback
    assert len(doc.Objects) == 3

    doc.undo()

    assert doc.Objects == [], "one undo did not take the whole Step back"


def test_two_steps_undo_one_at_a_time(pipeline, doc):
    pipeline.apply(BOX, label="Add the box")
    pipeline.apply("cyl = doc.addObject('Part::Cylinder', 'Cyl')", label="Add the cylinder")
    assert sorted(o.Name for o in doc.Objects) == ["Box", "Cyl"]

    doc.undo()

    assert [o.Name for o in doc.Objects] == ["Box"]


def test_the_undo_entry_is_named_after_the_step(pipeline, doc):
    pipeline.apply(BOX, label="Add the base box")

    assert "Add the base box" in doc.UndoNames


def test_a_step_that_throws_leaves_the_document_untouched(doc, worker, tmp_path):
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func(),
                             render_dir=str(tmp_path))
    # Passes lint and Preflight, then fails for real: the object already exists.
    pipeline.apply(BOX)
    before = [o.Name for o in doc.Objects]

    with pytest.raises(BaseException):
        run_in_transaction(doc, "doc.addObject('Part::Box', 'B')\nraise RuntimeError('halfway')")

    assert [o.Name for o in doc.Objects] == before, "an aborted transaction left debris"


# ── crashing code leaves FreeCAD alive ────────────────────────────────────────


def test_code_that_crashes_the_core_never_reaches_the_document(doc, tmp_path):
    with PreflightWorker() as own:
        pipeline = ApplyPipeline(document=doc, worker=own,
                                 dispatch=lambda func, timeout=None: func(),
                                 render_dir=str(tmp_path))

        result = pipeline.apply(CRASHING_CODE)

        assert not result.ok
        assert "died" in result.traceback
        assert doc.Objects == []
    # And this process — which is standing in for the user's FreeCAD — is
    # still here to make the assertion.
    import FreeCAD
    assert FreeCAD.getDocument("ApplyLevel2") is doc


def test_an_unbounded_loop_never_reaches_the_document(doc, tmp_path):
    with PreflightWorker(timeout=3) as own:
        pipeline = ApplyPipeline(document=doc, worker=own,
                                 dispatch=lambda func, timeout=None: func(),
                                 render_dir=str(tmp_path))

        result = pipeline.apply("while True:\n    pass")

        assert not result.ok
        assert "timed out" in result.traceback
        assert doc.Objects == []


# ── through the Tool, which is the only way the loop will see it ──────────────


def test_apply_step_is_a_tool_and_hides_preflight_behind_it(doc, worker, tmp_path):
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func(),
                             render_dir=str(tmp_path))
    tools = build_registry(doc, pipeline=pipeline)

    assert "apply_step" in tools

    result = tools.call("apply_step", {"code": BOX, "label": "Add the box"})

    assert result["status"] == "applied"
    assert result["created"] == ["Box"]


def test_apply_step_returns_the_traceback_for_the_assistant_to_correct_against(doc, worker, tmp_path):
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func(),
                             render_dir=str(tmp_path))
    tools = build_registry(doc, pipeline=pipeline)

    result = tools.call("apply_step", {"code": "doc.addObject('Part::Nonexistent', 'X')"})

    assert result["status"] == "failed"
    assert "Nonexistent" in result["traceback"]
    assert doc.Objects == []


def test_the_read_only_registry_has_no_apply_step(doc):
    assert "apply_step" not in build_registry(doc)
