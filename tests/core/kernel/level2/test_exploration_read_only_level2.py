"""
Level 2 — exploration is for looking, never for building.

The Document a `Kernel.check(exploring=True)` call leaves behind, if it
created, deleted or modified an object, is verified by reopening the
checkpoint from disk, not by reading the kernel's own claim about itself.
`exploring=False` (the default) is the ordinary Step path, left alone here.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _objects_in(path):
    import FreeCAD

    doc = FreeCAD.openDocument(path)
    try:
        return sorted(o.Name for o in doc.Objects)
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_an_exploration_call_that_adds_an_object_is_rolled_back(kernel):
    """AC1: the next call's Document does not carry the object, and the
    result names what was rolled back."""
    baseline = kernel.check("pass")
    assert baseline.ok, baseline.traceback

    explored = kernel.check(
        "doc.addObject('Sketcher::SketchObject', 'CavityProfile')", exploring=True)

    assert explored.ok, explored.traceback
    assert explored.rolled_back == ["CavityProfile"]
    assert _objects_in(explored.checkpoint) == []

    following = kernel.check("pass")
    assert following.ok, following.traceback
    assert _objects_in(following.checkpoint) == []


def test_a_step_after_an_exploration_that_added_the_same_object_gets_its_name(kernel):
    """AC2: `CavityProfile` from an exploration that added one leaves the
    name free for the Step that declares it for real."""
    explored = kernel.check(
        "doc.addObject('Sketcher::SketchObject', 'CavityProfile')", exploring=True)
    assert explored.ok, explored.traceback

    step = kernel.check(
        "doc.addObject('Sketcher::SketchObject', 'CavityProfile')")

    assert step.ok, step.traceback
    assert _objects_in(step.checkpoint) == ["CavityProfile"]


def test_a_read_only_exploration_call_writes_no_checkpoint(kernel):
    """A call that only reads stays cheap: no reload, and the checkpoint the
    Document already stood on is left untouched."""
    baseline = kernel.check("pass")
    assert baseline.ok, baseline.traceback

    explored = kernel.check("print(len(doc.Objects))", exploring=True)

    assert explored.ok, explored.traceback
    assert explored.rolled_back == []
    assert explored.checkpoint == baseline.checkpoint


def test_an_exploration_calls_own_stdout_and_created_names_still_come_back(kernel):
    """A rolled-back call still reports what its own code did -- the
    rollback is what the Document keeps, not what the result says happened."""
    explored = kernel.check(
        "doc.addObject('Part::Box', 'Box')\nprint('made a box')", exploring=True)

    assert explored.ok, explored.traceback
    assert explored.created == ["Box"]
    assert explored.stdout.strip() == "made a box"
    assert explored.rolled_back == ["Box"]
