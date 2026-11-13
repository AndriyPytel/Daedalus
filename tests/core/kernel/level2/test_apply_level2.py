"""
Level 2 — the apply pipeline against a real Document and a real Preflight Worker.

The two claims a fake cannot make: one undo takes a whole Step back, and code
that crashes the C++ core leaves this process — standing in for the user's
FreeCAD — alive.
"""
import os

import pytest

from core.document_tools import build_registry
from core.freecad_apply import ApplyPipeline, run_in_transaction
from core.kernel import Kernel, copy_of, find_freecadcmd


pytestmark = pytest.mark.freecad


BOX = "box = doc.addObject('Part::Box', 'Box')\nbox.Length = 10\n"


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
def pipeline(doc, worker):
    """
    An ApplyPipeline for testing. Already on the main thread here, so the
    dispatcher is a straight call.
    """
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func())
    pipeline.start_run()
    return pipeline


def test_a_step_brings_back_a_description_of_what_is_now_there(pipeline, doc, worker):
    """
    An Apply now answers with the part rather than a picture of it:
    the feature with its parameters, and the geometry read off the Shape.
    """
    result = pipeline.apply(BOX)

    assert result.ok, result.traceback
    described = {f["name"]: f for f in result.description["features"]}
    assert described["Box"]["parameters"]["Length"] == "10.0 mm"
    assert result.description["geometry"] == {"solids": 1, "volume_mm3": 1000.0,
                                              "surfaces": [{"kind": "Plane", "count": 6}]}


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

    result = pipeline.apply(BOX)

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
    result = pipeline.apply(code)

    assert result.ok, result.traceback
    assert "alumin" in doc.getObject("Body").ShapeMaterial.Name.lower()


def test_two_steps_undo_one_at_a_time(pipeline, doc, worker):
    pipeline.apply(BOX)
    cylinder_code = "cyl = doc.addObject('Part::Cylinder', 'Cyl')"
    pipeline.apply(cylinder_code)
    assert sorted(o.Name for o in doc.Objects) == ["Box", "Cyl"]

    doc.undo()

    assert [o.Name for o in doc.Objects] == ["Box"]


def test_a_step_that_throws_leaves_the_document_untouched(doc, worker):
    """
    A Step that passes lint and Preflight but then fails for real (the object
    already exists) leaves the document untouched.
    """
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func())
    pipeline.start_run()
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


def test_apply_step_is_a_tool_that_takes_the_code_alone(doc, worker):
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func())
    pipeline.start_run()
    tools = build_registry(doc, pipeline=pipeline)

    assert "apply_step" in tools
    [spec] = [spec for spec in tools.describe() if spec["name"] == "apply_step"]
    assert list(spec["parameters"]["properties"]) == ["code"]

    result = tools.call("apply_step", {"code": BOX})

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
    baseline = worker.rehearse("doc.addObject('Part::Box', 'Baseline')")
    assert baseline.ok, baseline.traceback
    before = os.path.getmtime(baseline.checkpoint)

    result = pipeline.apply("import Gui\nGui.Selection")

    assert not result.ok
    assert os.path.getmtime(baseline.checkpoint) == before, \
        "a lint-rejected Step still wrote a new checkpoint"


def test_preflight_reports_what_the_step_actually_created(worker):
    assert worker.seed().ok
    result = worker.rehearse("doc.addObject('Part::Box', 'Box')")

    assert result.ok, result.traceback
    assert result.created == ["Box"]
    assert result.modified == []
    assert result.deleted == []


def test_preflight_reports_a_modified_object_the_step_did_not_create(worker, saved_document):
    assert worker.seed(saved_document).ok
    result = worker.rehearse("doc.getObject('ExistingBox').Length = 99")

    assert result.ok, result.traceback
    assert result.modified == ["ExistingBox"]
    assert result.created == []


def test_preflight_reports_a_deleted_object(worker, saved_document):
    assert worker.seed(saved_document).ok
    result = worker.rehearse("doc.removeObject('ExistingBox')")

    assert result.ok, result.traceback
    assert result.deleted == ["ExistingBox"]


def test_a_dependent_recompute_is_not_reported_as_a_modification(worker, document_with_a_cut):
    """
    The Step changes ExistingBox and recomputes, which changes Cut's Shape.
    Only ExistingBox was modified; reporting Cut as well would make the Owned
    Object rule fire on almost every Step that touches anything upstream.
    """
    assert worker.seed(document_with_a_cut).ok
    result = worker.rehearse("doc.getObject('ExistingBox').Length = 99\ndoc.recompute()")

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


def test_the_users_own_object_is_protected_end_to_end(doc, worker):
    """The whole rule, against a real Document: refuse a deletion, then allow it."""
    users_box = doc.addObject("Part::Box", "UsersBox")
    users_box.Length = 12
    doc.recompute()

    answers = []
    pipeline = ApplyPipeline(document=doc, worker=worker,
                            dispatch=lambda func, timeout=None: func(),
                            confirm=lambda kinds: answers.pop(0))
    pipeline.start_run()

    delete_code = "doc.removeObject('UsersBox')"
    answers.append(False)
    refused = pipeline.apply(delete_code)
    assert not refused.ok
    assert "UsersBox" in refused.traceback
    assert doc.getObject("UsersBox") is not None, "removed it anyway"

    answers.append(True)
    allowed = pipeline.apply(delete_code)
    assert allowed.ok, allowed.traceback
    assert doc.getObject("UsersBox") is None


def test_each_applied_step_lands_as_exactly_one_undo_transaction(pipeline, doc, worker):
    """
    A revision that drops a Step undoes it by calling `doc.undo()` once per
    Step dropped, which is only correct if nothing besides `apply()`'s own
    transaction ever lands on the stack -- not the recompute inside it, and
    not `_show_tip`'s ViewObject changes after it.
    """
    CYLINDER = "cyl = doc.addObject('Part::Cylinder', 'Cyl')\n"

    first = pipeline.apply(BOX)
    assert first.ok, first.traceback
    second = pipeline.apply(CYLINDER)
    assert second.ok, second.traceback

    assert len(doc.UndoNames) == 2, (
        "two Steps landed but the undo stack holds %r -- something other "
        "than a Step's own transaction is on it" % list(doc.UndoNames))


_A_BLOCK_WITH_A_HOLE = """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
outer = doc.addObject('Sketcher::SketchObject', 'Outer')
body.addObject(outer)
for a, b in (((0, 0), (20, 0)), ((20, 0), (20, 20)), ((20, 20), (0, 20)), ((0, 20), (0, 0))):
    outer.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0),
                                       App.Vector(b[0], b[1], 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = outer
pad.Length = 10
doc.recompute()

hole_sketch = doc.addObject('Sketcher::SketchObject', 'HoleSketch')
body.addObject(hole_sketch)
hole_sketch.Placement = App.Placement(App.Vector(0, 0, 10), App.Rotation())
hole_sketch.addGeometry(Part.Circle(App.Vector(10, 10, 0), App.Vector(0, 0, 1), 4))
pocket = doc.addObject('PartDesign::Pocket', 'Pocket')
body.addObject(pocket)
pocket.Profile = hole_sketch
pocket.Length = 10
doc.recompute()
"""


def _outer_corner_edge_name(shape):
    """The vertical edge at the block's (0, 0) corner, untouched by the interior hole."""
    for at, edge in enumerate(shape.Edges):
        if len(edge.Vertexes) != 2:
            continue
        p0, p1 = (v.Point for v in edge.Vertexes)
        if abs(p0.x) < 1e-6 and abs(p0.y) < 1e-6 and abs(p1.x) < 1e-6 and abs(p1.y) < 1e-6:
            return "Edge%d" % (at + 1)
    raise RuntimeError("no outer corner edge found on the padded block")


def test_a_fillets_base_set_to_the_body_is_refused_before_it_reaches_freecad(pipeline, doc, worker):
    setup = pipeline.apply(_A_BLOCK_WITH_A_HOLE)
    assert setup.ok, setup.traceback
    before = sorted(o.Name for o in doc.Objects)

    code = ("body = doc.getObject('Body')\n"
           "fillet = doc.addObject('PartDesign::Fillet', 'Fillet')\n"
           "body.addObject(fillet)\n"
           "fillet.Base = (body, ['Edge1'])\n")

    result = pipeline.apply(code)

    assert not result.ok
    assert result.gate == "lint"
    assert "Tip" in result.traceback
    assert sorted(o.Name for o in doc.Objects) == before, (
        "a Step lint refused still reached the real Document")


def test_the_same_fillet_based_on_the_previous_tip_builds(pipeline, doc, worker):
    setup = pipeline.apply(_A_BLOCK_WITH_A_HOLE)
    assert setup.ok, setup.traceback
    edge = _outer_corner_edge_name(doc.getObject("Pocket").Shape)

    code = ("body = doc.getObject('Body')\n"
           "tip = body.Tip\n"
           "fillet = doc.addObject('PartDesign::Fillet', 'Fillet')\n"
           "body.addObject(fillet)\n"
           "fillet.Base = (tip, [%r])\n"
           "fillet.Radius = 1.0\n"
           "doc.recompute()\n" % edge)

    result = pipeline.apply(code, selection=[{"object": "Pocket", "elements": [edge]}])

    assert result.ok, result.traceback
    assert doc.getObject("Fillet").isValid()


def test_a_step_that_changes_nothing_is_refused(pipeline, doc):
    from core.prompts import read_prompt

    pipeline.apply(BOX)
    before = [o.Name for o in doc.Objects]
    undone = list(doc.UndoNames)

    result = pipeline.apply("print(doc.Objects)")

    assert not result.ok
    assert result.gate == "changed_nothing"
    assert result.traceback == read_prompt("changed_nothing.md")
    assert [o.Name for o in doc.Objects] == before
    assert list(doc.UndoNames) == undone


def test_a_step_the_document_refuses_is_resynced_so_the_next_step_runs_on_what_is_there(
        pipeline, doc):
    users_box = doc.addObject("Part::Box", "UsersBox")
    doc.recompute()
    pipeline.start_run()

    refused = pipeline.apply("doc.removeObject('UsersBox')")
    assert not refused.ok and refused.gate == "ownership"

    after = pipeline.apply("doc.getObject('UsersBox').Length = 99")

    assert after.ok, after.traceback
    assert doc.getObject("UsersBox") is users_box
