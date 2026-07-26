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


# ── what a Step touches, observed rather than inferred ────────────────────────


def test_preflight_reports_what_the_step_actually_created(worker):
    result = worker.check("doc.addObject('Part::Box', 'Box')")

    assert result.ok, result.traceback
    assert result.created == ["Box"]
    assert result.modified == []
    assert result.deleted == []


def test_preflight_reports_a_modified_object_the_step_did_not_create(worker, saved_document):
    result = worker.check("doc.getObject('ExistingBox').Length = 99",
                          document=saved_document)

    assert result.ok, result.traceback
    assert result.modified == ["ExistingBox"]
    assert result.created == []


def test_preflight_reports_a_deleted_object(worker, saved_document):
    result = worker.check("doc.removeObject('ExistingBox')", document=saved_document)

    assert result.ok, result.traceback
    assert result.deleted == ["ExistingBox"]


def test_a_dependent_recompute_is_not_reported_as_a_modification(worker, document_with_a_cut):
    """
    The Step changes ExistingBox and recomputes, which changes Cut's Shape.
    Only ExistingBox was modified; reporting Cut as well would make the Owned
    Object rule fire on almost every Step that touches anything upstream.
    """
    result = worker.check(
        "doc.getObject('ExistingBox').Length = 99\ndoc.recompute()",
        document=document_with_a_cut,
    )

    assert result.ok, result.traceback
    assert result.modified == ["ExistingBox"], "a recompute cascade looked like a modification"


@pytest.fixture
def saved_document(tmp_path):
    import FreeCAD

    from core.preflight import copy_of

    document = FreeCAD.newDocument("OwnedFixture")
    box = document.addObject("Part::Box", "ExistingBox")
    box.Length = 12
    document.recompute()
    try:
        yield copy_of(document)
    finally:
        FreeCAD.closeDocument(document.Name)


@pytest.fixture
def document_with_a_cut(tmp_path):
    """ExistingBox with something downstream of it that recomputes cleanly."""
    import FreeCAD

    from core.preflight import copy_of

    document = FreeCAD.newDocument("CutFixture")
    box = document.addObject("Part::Box", "ExistingBox")
    box.Length, box.Width, box.Height = 12, 12, 12
    cylinder = document.addObject("Part::Cylinder", "Hole")
    cylinder.Radius, cylinder.Height = 3, 40
    cut = document.addObject("Part::Cut", "Cut")
    cut.Base, cut.Tool = box, cylinder
    document.recompute()
    try:
        yield copy_of(document)
    finally:
        FreeCAD.closeDocument(document.Name)


def test_the_users_own_object_is_protected_end_to_end(doc, worker, tmp_path):
    """The whole rule, against a real Document: refuse, then allow."""
    users_box = doc.addObject("Part::Box", "UsersBox")
    users_box.Length = 12
    doc.recompute()

    answers = []
    pipeline = ApplyPipeline(document=doc, worker=worker,
                            dispatch=lambda func, timeout=None: func(),
                            render_dir=str(tmp_path),
                            confirm=lambda kinds: answers.pop(0))

    answers.append(False)
    refused = pipeline.apply("doc.getObject('UsersBox').Length = 99")
    assert not refused.ok
    assert "UsersBox" in refused.traceback
    assert doc.getObject("UsersBox").Length.Value == 12, "changed it anyway"

    answers.append(True)
    allowed = pipeline.apply("doc.getObject('UsersBox').Length = 99")
    assert allowed.ok, allowed.traceback
    assert doc.getObject("UsersBox").Length.Value == 99


def test_what_the_assistant_created_needs_no_permission(doc, worker, tmp_path):
    asks = []
    pipeline = ApplyPipeline(document=doc, worker=worker,
                            dispatch=lambda func, timeout=None: func(),
                            render_dir=str(tmp_path),
                            confirm=lambda kinds: asks.append(kinds) or True)

    assert pipeline.apply(BOX).ok
    assert pipeline.owned == {"Box"}

    assert pipeline.apply("doc.getObject('Box').Length = 25").ok
    assert asks == [], "asked permission for its own object"
    assert doc.getObject("Box").Length.Value == 25
