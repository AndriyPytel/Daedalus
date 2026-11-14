"""
Level 1 -- what `preflight()`'s own recompute call touches, argument-free.

A bare `doc.recompute()` costs more with every prior Step already in the
Document; `_recompute_scope` is what lets `preflight()` pass a bounded object
list to `doc.recompute([...])` instead, mirroring the
`obj.Document.recompute([obj])` this file already uses in
`_diagnose_idle_feature`.

`doc.recompute(objs)` recomputes exactly *objs* and their own dependencies --
it does not walk `InList` the way the argument-free form does, so a Step that
edits an existing feature rather than only appending a new one leaves every
downstream feature `Touched` with a stale `Shape`, not `Invalid`: the failure
scan right after (`"Invalid" in o.State`) would not see it, and Preflight
would pass a Step silently on a wrong shape. `_recompute_scope` closes over
each touched object's `InList` so the scope always includes what depends on
it. No FreeCAD needed for the fakes below: they only have to answer
`getObject`, `.Name` and `.InList`.

The Level 2 test at the bottom pins the same claim against a real FreeCAD
Document, across two separate Preflight calls the way `apply.py` actually
drives Preflight -- the first Step builds a Fillet on a Box, the second only
edits the Box, so Fillet is never itself created or written by a Step and can
only reach the recompute scope through its dependency on Box.
"""
from core.kernel.kernel_worker import _recompute_scope


class _FakeTouched:
    def __init__(self, created=(), modified=()):
        self.created = set(created)
        self._modified = set(modified)

    def modified(self):
        return self._modified


class _FakeObj:
    def __init__(self, name, in_list=()):
        self.Name = name
        self.InList = list(in_list)


class _FakeDoc:
    def __init__(self, objects):
        self._objects = objects

    def getObject(self, name):
        return self._objects.get(name)


def test_created_and_modified_objects_are_the_scope():
    box, pad, other = _FakeObj("Box"), _FakeObj("Pad"), _FakeObj("Other")
    doc = _FakeDoc({"Box": box, "Pad": pad, "Other": other})
    touched = _FakeTouched(created=["Box"], modified=["Pad"])

    assert {o.Name for o in _recompute_scope(doc, touched)} == {"Box", "Pad"}


def test_a_step_that_only_deleted_something_asks_for_the_full_recompute():
    doc = _FakeDoc({})
    touched = _FakeTouched(created=[], modified=[])

    assert _recompute_scope(doc, touched) is None


def test_a_name_the_document_no_longer_has_is_left_out():
    box = _FakeObj("Box")
    doc = _FakeDoc({"Box": box})
    touched = _FakeTouched(created=["Box", "GoneBeforeRecompute"])

    assert [o.Name for o in _recompute_scope(doc, touched)] == ["Box"]


def test_downstream_dependents_are_pulled_into_the_scope():
    tip = _FakeObj("Pad3")
    mid = _FakeObj("Pad2", in_list=[tip])
    near = _FakeObj("Pad1", in_list=[mid])
    sketch = _FakeObj("Sketch", in_list=[near])
    doc = _FakeDoc({"Sketch": sketch})
    touched = _FakeTouched(modified=["Sketch"])

    assert {o.Name for o in _recompute_scope(doc, touched)} == \
        {"Sketch", "Pad1", "Pad2", "Pad3"}


def test_a_dependency_cycle_does_not_loop_forever():
    a = _FakeObj("A")
    b = _FakeObj("B", in_list=[a])
    a.InList = [b]
    doc = _FakeDoc({"A": a})
    touched = _FakeTouched(modified=["A"])

    assert {o.Name for o in _recompute_scope(doc, touched)} == {"A", "B"}


import pytest

from core.agent import part_rules
from core.kernel import Kernel, copy_of, find_freecadcmd

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
def a_box_with_a_fillet_that_no_longer_fits_once_shrunk():
    """A Fillet built valid on a 20 mm Box, saved so a later Step never touches it directly."""
    import FreeCAD

    doc = FreeCAD.newDocument("RecomputeScopeFixture")
    box = doc.addObject("Part::Box", "Box")
    box.Length = box.Width = box.Height = 20
    fillet = doc.addObject("Part::Fillet", "Fillet")
    fillet.Base = box
    fillet.Edges = [(1, 9.0, 9.0)]
    doc.recompute()
    try:
        yield copy_of(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_feature_left_stale_by_a_scoped_recompute_still_fails_preflight(
    worker, a_box_with_a_fillet_that_no_longer_fits_once_shrunk
):
    assert worker.seed(a_box_with_a_fillet_that_no_longer_fits_once_shrunk, rules=part_rules).ok
    result = worker.rehearse(
        "box = doc.getObject('Box')\n"
        "box.Length = 5\n"
        "box.Width = 5\n"
        "box.Height = 5\n"
    )

    assert not result.ok, "Fillet was left Touched instead of recomputed and caught"
    assert "Fillet" in result.traceback, result.traceback
