"""
Level 2 -- the replay signature contract against a real headless
Preflight worker and a real FreeCAD Document: `run_in_transaction` aborts
before it commits when what landed does not match what Preflight verified,
and the allocated-name map crosses from one Step's Preflight into the
next Step's replay of a real Document.
"""
import functools

import pytest

from core.freecad_apply import ApplyPipeline, SignatureMismatch, run_in_transaction
from core.kernel import Kernel, copy_of, find_freecadcmd
from core.kernel.kernel_worker import signature_of


def _kernel_result(worker, doc, code, names=None):
    """What `apply()` now expects a caller to already have from the kernel's own
    run of *code*, rather than a rehearsal `apply()` runs itself (DAE-A-53)."""
    import os

    copy = copy_of(doc)
    try:
        result = worker.check(code, document=copy, names=names or {}, ephemeral=True)
    finally:
        os.remove(copy)
    assert result.ok, result.traceback
    return {"created": result.created, "deleted": result.deleted,
            "modified": result.modified, "objects": result.objects,
            "names": result.names, "signature": result.signature}

pytestmark = pytest.mark.freecad


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
    import FreeCAD

    document = FreeCAD.newDocument("ReplaySignatureLevel2")
    document.UndoMode = 1
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


@pytest.fixture
def pipeline(doc, worker, tmp_path):
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func(),
                             render_dir=str(tmp_path))
    pipeline.apply = functools.partial(pipeline.apply, design_model=False)
    return pipeline


def test_a_step_that_matches_its_own_signature_commits(doc):
    doc.addObject("Part::Box", "Box")
    doc.recompute()

    run_in_transaction(doc, "pass", names={}, expected_signature=signature_of(doc))

    assert len(doc.Objects) == 1


def test_a_forged_signature_aborts_before_it_commits(doc):
    doc.UndoMode = 1

    with pytest.raises(SignatureMismatch):
        run_in_transaction(
            doc, "doc.addObject('Part::Box', 'Box')", names={},
            expected_signature={"count": 5, "names": ["nothing", "here"],
                               "volume": 0.0, "links": {}},
        )

    assert doc.Objects == []


def test_a_step_addresses_an_earlier_steps_object_through_the_pipelines_own_name_map(pipeline, doc, worker):
    """
    AC4, through the full apply() pipeline rather than the kernel alone: a
    stray pre-existing object stands in for a hand-edit, Step 1's preferred
    name collides with it, and Step 2 -- unchanged code, `names['Box']` --
    still reaches the object Step 1 created, both in Preflight and in the
    real Document's replay.
    """
    doc.addObject("Part::Box", "Box")
    doc.recompute()

    first_code = "box = doc.addObject('Part::Box', 'Box')\nbox.Length = 42\n"
    first = pipeline.apply(first_code, label="Add the box",
                           kernel_result=_kernel_result(worker, doc, first_code))
    assert first.ok, first.traceback
    assert first.created == ["Box001"], \
        "FreeCAD did not rename the colliding object the way this test assumes"

    second_code = ("target = doc.getObject(names['Box'])\n"
                   "assert target.Length.Value == 42, 'reached the wrong Box'\n"
                   "target.Width = 7\n")
    second = pipeline.apply(second_code, label="Read the box back",
                            kernel_result=_kernel_result(worker, doc, second_code,
                                                         names=pipeline._names))

    assert second.ok, second.traceback
    assert doc.getObject("Box001").Width.Value == 7


def test_a_link_target_mismatch_is_caught_even_though_count_names_and_volume_all_match():
    """
    AC5: two Documents with the same object count, the same names and the
    same total volume, but an `App::Link` pointing somewhere different --
    the exact failure the epic's doc-diff research reproduced (count, names
    and volume all matched, only the object was wrong).
    """
    import FreeCAD

    left = FreeCAD.newDocument("LinkSignatureLeft")
    right = FreeCAD.newDocument("LinkSignatureRight")
    try:
        for document in (left, right):
            document.addObject("Part::Box", "BoxA")
            document.addObject("Part::Box", "BoxB")

        left_link = left.addObject("App::Link", "Link")
        left_link.LinkedObject = left.getObject("BoxA")
        right_link = right.addObject("App::Link", "Link")
        right_link.LinkedObject = right.getObject("BoxB")

        left.recompute()
        right.recompute()

        left_signature = signature_of(left)
        right_signature = signature_of(right)

        assert left_signature["count"] == right_signature["count"]
        assert left_signature["names"] == right_signature["names"]
        assert left_signature["volume"] == right_signature["volume"]
        assert left_signature["links"] != right_signature["links"]
    finally:
        FreeCAD.closeDocument(left.Name)
        FreeCAD.closeDocument(right.Name)
