"""
Level 2 -- ownership observed by a PropertiesList diff, against a real kernel
and a real Document.

Complements `tests/test_kernel_worker_ownership_diff.py`'s Level 1 diff-shape
tests and `tests/test_kernel_open_namespace.py`'s AC1 (a raw FreeCAD call, no
Tool, still lands and is reported created). This file pins the two claims
those do not: a property change during a Step is captured (AC2), and the
Owned Object rule itself -- `core/apply.py`'s `ApplyPipeline.owned` -- still
attributes a Tool-free creation to the assistant (AC1, at the rule's own
home).
"""
import pytest

from core.freecad_apply import ApplyPipeline, run_in_transaction
from core.kernel import Kernel, find_freecadcmd


pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_property_change_is_captured_and_attributed_to_its_step(kernel):
    """AC2: a modification, not just a creation, is captured by the diff."""
    created = kernel.rehearse("doc.addObject('Part::Box', 'Box')")
    assert created.ok, created.traceback
    assert created.modified == []

    changed = kernel.rehearse("doc.getObject('Box').Length = 20")

    assert changed.ok, changed.traceback
    assert changed.modified == ["Box"]
    assert changed.created == []


@pytest.fixture
def doc():
    """A Document with undo on, which the GUI turns on and headless does not."""
    import FreeCAD

    document = FreeCAD.newDocument("OwnershipDiffLevel2")
    document.UndoMode = 1
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


def test_a_step_that_bypasses_every_tool_is_still_recorded_as_owned(doc, kernel):
    """
    AC1 at the Owned Object rule's own home: `ApplyPipeline` updates `.owned`
    off the real Document's own before/after state, so an object a Step
    created with a raw FreeCAD call -- no registered Tool -- is owned exactly
    as if a Tool had made it.
    """
    pipeline = ApplyPipeline(document=doc, worker=kernel,
                             dispatch=lambda func, timeout=None: func(),
                             transaction=run_in_transaction)
    pipeline.start_run()

    code = "doc.addObject('Part::Box', 'RawBox')"
    result = pipeline.apply(code)

    assert result.ok, result.traceback
    assert "RawBox" in pipeline.owned
