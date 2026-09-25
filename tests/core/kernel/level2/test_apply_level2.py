"""
Level 2 — the apply pipeline against a real Document and a real Preflight Worker.

The two claims a fake cannot make: one undo takes a whole Step back, and code
that crashes the C++ core leaves this process — standing in for the user's
FreeCAD — alive.
"""
import os

import pytest

from core.freecad_apply import ApplyPipeline, build_registry, run_in_transaction
from core.kernel import Kernel, copy_of, find_freecadcmd


def _kernel_result(worker, doc, code, names=None, design=None):
    """What `apply()` now expects a caller to already have from the kernel's
    own run of *code*, rather than a rehearsal `apply()` runs itself
    (FCC-A-53)."""
    copy = copy_of(doc)
    try:
        result = worker.check(code, document=copy, names=names or {}, ephemeral=True,
                              design=design)
    finally:
        os.remove(copy)
    assert result.ok, result.traceback
    return {"created": result.created, "deleted": result.deleted,
            "modified": result.modified, "objects": result.objects,
            "names": result.names, "signature": result.signature}

pytestmark = pytest.mark.freecad


A_DESIGN = {"features": {"Box": {"length": 10, "resized_length": 25},
                        "ExistingBox": {"length": 99}, "UsersBox": {"length": 99}}}
BOX = "box = doc.addObject('Part::Box', 'Box')\nbox.Length = design['features']['Box']['length']\n"


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


@pytest.fixture
def doc():
    """A Document with undo on, which the GUI turns on and headless does not."""
    import FreeCAD

    document = FreeCAD.newDocument("ApplyLevel2")
    document.UndoMode = 1
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


@pytest.fixture
def pipeline(doc, worker, tmp_path):
    """
    An ApplyPipeline for testing. Already on the main thread here, so the
    dispatcher is a straight call.
    """
    return ApplyPipeline(document=doc, worker=worker,
                         dispatch=lambda func, timeout=None: func(),
                         render_dir=str(tmp_path))


def test_a_step_lands_in_the_document(pipeline, doc, worker):
    result = pipeline.apply(BOX, label="Add the box", design=A_DESIGN,
                            kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))

    assert result.ok, result.traceback
    assert result.created == ["Box"]
    assert doc.getObject("Box").Length.Value == 10


def test_a_step_brings_back_a_description_of_what_is_now_there(pipeline, doc, worker):
    """
    An Apply now answers with the part rather than a picture of it:
    the feature with its parameters, and the geometry read off the Shape.
    """
    result = pipeline.apply(BOX, design=A_DESIGN,
                            kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))

    assert result.ok, result.traceback
    described = {f["name"]: f for f in result.description["features"]}
    assert described["Box"]["parameters"]["Length"] == "10.0 mm"
    assert result.description["geometry"] == {"solids": 1, "volume_mm3": 1000.0,
                                              "surfaces": [{"kind": "Plane", "count": 6}]}


def test_the_headless_fallback_still_draws_the_document_once(pipeline, doc, worker):
    """
    The Worker's rasteriser survives as the fallback, so the render's per-pixel
    depth test and edges are not thrown away — called once at the end of a run
    rather than once per Step.
    """
    pipeline.apply(BOX, design=A_DESIGN,
                   kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))
    views = pipeline.review_views()

    assert len(views) == 1, "the headless fallback is one isometry, not four"
    assert os.path.getsize(views[0]) > 1000


def test_a_provider_that_answers_none_beats_freecads_active_document(doc, worker):
    """
    A provider that answers `None` means no open Document, whatever
    `FreeCAD.ActiveDocument` holds -- proven here with a real Document open
    and active in the process.
    """
    import FreeCAD

    assert FreeCAD.ActiveDocument is doc
    pipeline = ApplyPipeline(document=lambda: None, worker=worker,
                             dispatch=lambda func, timeout=None: func())

    result = pipeline.apply(BOX, design=A_DESIGN,
                            kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))

    assert not result.ok
    assert "no open Document" in result.traceback


def test_a_step_can_assign_a_material_the_way_the_trap_describes(pipeline, doc, worker):
    """
    The Step that burned all three attempts of the CSWA-bracket model run. It
    has to survive Preflight — a separate process building its own namespace —
    and Apply, or the two are running different programs.
    """
    registry = build_registry(doc)
    aluminium = [card for card in registry.call("materials", {"name": "alumin"})["materials"]
                 if card["density"] and "2.7" in card["density"]][0]

    code = ("body = doc.addObject('PartDesign::Body', 'Body')\n"
           "body.ShapeMaterial = Materials.MaterialManager().getMaterial(%r)\n" % aluminium["uuid"])
    result = pipeline.apply(code, label="Assign aluminium",
                            kernel_result=_kernel_result(worker, doc, code))

    assert result.ok, result.traceback
    assert "alumin" in doc.getObject("Body").ShapeMaterial.Name.lower()


def test_a_single_undo_takes_a_whole_step_back(pipeline, doc, worker):
    """Several objects, one Step, one Ctrl+Z."""
    code = ("box = doc.addObject('Part::Box', 'Box')\n"
           "cyl = doc.addObject('Part::Cylinder', 'Cyl')\n"
           "cut = doc.addObject('Part::Cut', 'Cut')\n"
           "cut.Base, cut.Tool = box, cyl\n")
    result = pipeline.apply(code, label="Box minus cylinder",
                            kernel_result=_kernel_result(worker, doc, code))
    assert result.ok, result.traceback
    assert len(doc.Objects) == 3

    doc.undo()

    assert doc.Objects == [], "one undo did not take the whole Step back"


def test_two_steps_undo_one_at_a_time(pipeline, doc, worker):
    pipeline.apply(BOX, label="Add the box", design=A_DESIGN,
                   kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))
    cylinder_code = "cyl = doc.addObject('Part::Cylinder', 'Cyl')"
    pipeline.apply(cylinder_code, label="Add the cylinder",
                   kernel_result=_kernel_result(worker, doc, cylinder_code,
                                                names=pipeline._names))
    assert sorted(o.Name for o in doc.Objects) == ["Box", "Cyl"]

    doc.undo()

    assert [o.Name for o in doc.Objects] == ["Box"]


def test_the_undo_entry_is_named_after_the_step(pipeline, doc, worker):
    pipeline.apply(BOX, label="Add the base box", design=A_DESIGN,
                   kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))

    assert "Add the base box" in doc.UndoNames


def test_a_step_that_throws_leaves_the_document_untouched(doc, worker, tmp_path):
    """
    A Step that passes lint and Preflight but then fails for real (the object
    already exists) leaves the document untouched.
    """
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func(),
                             render_dir=str(tmp_path))
    pipeline.apply(BOX)
    before = [o.Name for o in doc.Objects]

    with pytest.raises(BaseException):
        run_in_transaction(doc, "doc.addObject('Part::Box', 'B')\nraise RuntimeError('halfway')")

    assert [o.Name for o in doc.Objects] == before, "an aborted transaction left debris"


def test_a_retried_step_after_an_abort_allocates_the_original_name(doc):
    """
    FreeCAD's own name allocator only appends a suffix when the name is
    already taken; an aborted transaction that actually cleared its objects
    leaves the retried Step free to claim the same name outright.
    """
    code = "doc.addObject('Part::Box', 'Box')"

    with pytest.raises(BaseException):
        run_in_transaction(doc, code + "\nraise RuntimeError('halfway')")

    run_in_transaction(doc, code)

    assert [o.Name for o in doc.Objects] == ["Box"], \
        "the retry collided with debris the abort should have removed"


def test_run_in_transactions_own_exec_frame_is_cut_from_the_traceback(doc):
    """The frame that actually failed is the Step's, not `run_in_transaction`'s."""
    from core.kernel.kernel_worker import trimmed_traceback

    try:
        run_in_transaction(doc, "doc.addObject('Part::Box', 'B')\nraise RuntimeError('halfway')\n")
    except RuntimeError as exc:
        formatted = trimmed_traceback(exc)
    else:
        raise AssertionError("run_in_transaction did not raise")

    assert 'File "<step>", line 2' in formatted
    assert "raise RuntimeError('halfway')" in formatted
    assert "run_in_transaction" not in formatted


def test_apply_step_is_a_tool_and_carries_the_kernels_own_result(doc, worker, tmp_path):
    """
    There is no rehearsal behind this Tool any more: the loop already ran the
    Step through the kernel and hands its result straight through.
    """
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func(),
                             render_dir=str(tmp_path))
    tools = build_registry(doc, pipeline=pipeline)

    assert "apply_step" in tools

    result = tools.call("apply_step", {"code": BOX, "label": "Add the box", "design": A_DESIGN,
                                       "kernel_result": _kernel_result(worker, doc, BOX, design=A_DESIGN)})

    assert result["status"] == "applied"
    assert result["created"] == ["Box"]


def test_the_read_only_registry_has_no_apply_step(doc):
    assert "apply_step" not in build_registry(doc)


def test_a_lint_rejected_step_never_reaches_the_kernels_checkpoint(pipeline, worker):
    """
    A Step that fails one of lint's rules never spends a
    checkpoint -- it never reaches the kernel at all. Established against a
    real checkpoint file's mtime, not the kernel's own claim about itself.
    """
    baseline = worker.check("doc.addObject('Part::Box', 'Baseline')")
    assert baseline.ok, baseline.traceback
    before = os.path.getmtime(baseline.checkpoint)

    result = pipeline.apply("import Gui\nGui.Selection")

    assert not result.ok
    assert os.path.getmtime(baseline.checkpoint) == before, \
        "a lint-rejected Step still wrote a new checkpoint"


def test_preflight_reports_what_the_step_actually_created(worker):
    result = worker.check("doc.addObject('Part::Box', 'Box')", document=None)

    assert result.ok, result.traceback
    assert result.created == ["Box"]
    assert result.modified == []
    assert result.deleted == []


def test_preflight_reports_a_modified_object_the_step_did_not_create(worker, saved_document):
    result = worker.check("doc.getObject('ExistingBox').Length = design['features']['ExistingBox']['length']",
                          document=saved_document, design=A_DESIGN)

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
        "doc.getObject('ExistingBox').Length = design['features']['ExistingBox']['length']\ndoc.recompute()",
        document=document_with_a_cut, design=A_DESIGN,
    )

    assert result.ok, result.traceback
    assert result.modified == ["ExistingBox"], "a recompute cascade looked like a modification"


@pytest.fixture
def saved_document(tmp_path):
    import FreeCAD

    from core.kernel import copy_of

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

    from core.kernel import copy_of

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
    """The whole rule, against a real Document: refuse a deletion, then allow it."""
    users_box = doc.addObject("Part::Box", "UsersBox")
    users_box.Length = 12
    doc.recompute()

    answers = []
    pipeline = ApplyPipeline(document=doc, worker=worker,
                            dispatch=lambda func, timeout=None: func(),
                            render_dir=str(tmp_path),
                            confirm=lambda kinds: answers.pop(0))

    delete_code = "doc.removeObject('UsersBox')"
    answers.append(False)
    refused = pipeline.apply(delete_code,
                             kernel_result=_kernel_result(worker, doc, delete_code))
    assert not refused.ok
    assert "UsersBox" in refused.traceback
    assert doc.getObject("UsersBox") is not None, "removed it anyway"

    answers.append(True)
    allowed = pipeline.apply(delete_code,
                             kernel_result=_kernel_result(worker, doc, delete_code))
    assert allowed.ok, allowed.traceback
    assert doc.getObject("UsersBox") is None


def test_modifying_the_users_own_object_needs_no_permission(doc, worker, tmp_path):
    """The Plan Gate approved the Step that says the object changes."""
    users_box = doc.addObject("Part::Box", "UsersBox")
    users_box.Length = 12
    doc.recompute()

    asks = []
    pipeline = ApplyPipeline(document=doc, worker=worker,
                            dispatch=lambda func, timeout=None: func(),
                            render_dir=str(tmp_path),
                            confirm=lambda kinds: asks.append(kinds) or True)

    modify_code = "doc.getObject('UsersBox').Length = design['features']['UsersBox']['length']"
    result = pipeline.apply(modify_code, design=A_DESIGN,
                            kernel_result=_kernel_result(worker, doc, modify_code, design=A_DESIGN))

    assert result.ok, result.traceback
    assert asks == []
    assert doc.getObject("UsersBox").Length.Value == 99


def test_what_the_assistant_created_needs_no_permission(doc, worker, tmp_path):
    asks = []
    pipeline = ApplyPipeline(document=doc, worker=worker,
                            dispatch=lambda func, timeout=None: func(),
                            render_dir=str(tmp_path),
                            confirm=lambda kinds: asks.append(kinds) or True)

    assert pipeline.apply(BOX, design=A_DESIGN,
                          kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN)).ok
    assert pipeline.owned == {"Box"}

    modify_code = "doc.getObject('Box').Length = design['features']['Box']['resized_length']"
    assert pipeline.apply(modify_code, design=A_DESIGN,
                          kernel_result=_kernel_result(worker, doc, modify_code, design=A_DESIGN)).ok
    assert asks == [], "asked permission for its own object"
    assert doc.getObject("Box").Length.Value == 25


def test_each_applied_step_lands_as_exactly_one_undo_transaction(pipeline, doc, worker):
    """
    A revision that drops a Step undoes it by calling `doc.undo()` once per
    Step dropped, which is only correct if nothing besides `apply()`'s own
    transaction ever lands on the stack -- not the recompute inside it, and
    not `_show_tip`'s ViewObject changes after it.
    """
    CYLINDER = "cyl = doc.addObject('Part::Cylinder', 'Cyl')\n"

    first = pipeline.apply(BOX, label="Add the box", design=A_DESIGN,
                           kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))
    assert first.ok, first.traceback
    second = pipeline.apply(CYLINDER, label="Add the cylinder",
                            kernel_result=_kernel_result(worker, doc, CYLINDER))
    assert second.ok, second.traceback

    assert len(doc.UndoNames) == 2, (
        "two Steps landed but the undo stack holds %r -- something other "
        "than a Step's own transaction is on it" % list(doc.UndoNames))


def test_undo_step_takes_back_exactly_the_last_steps_objects(pipeline, doc, worker):
    CYLINDER = "cyl = doc.addObject('Part::Cylinder', 'Cyl')\n"

    pipeline.apply(BOX, label="Add the box", design=A_DESIGN,
                  kernel_result=_kernel_result(worker, doc, BOX, design=A_DESIGN))
    pipeline.apply(CYLINDER, label="Add the cylinder",
                  kernel_result=_kernel_result(worker, doc, CYLINDER))

    pipeline.undo_step()

    assert [o.Name for o in doc.Objects] == ["Box"]
    assert len(doc.UndoNames) == 1
