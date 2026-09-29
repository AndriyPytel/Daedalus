"""
Level 2 — the Assertion phase's self-intersection check against a real headless
FreeCAD.

`BOPAlgo_SelfIntersect` is too expensive to run after every Step,
so it runs once here, against the finished Document, the same way the
envelope check already does (`core/loop.py:the_envelope_measured`). The
harness is the Preflight Worker for the same reason `test_assertion_level2.py`
uses it: it already `exec`s code in a namespace with `doc`, `App` and `Part`,
and already opens a copy from disk rather than the live Document.

TWISTED_SOLID folds a bowtie profile back on itself and caps it into a single
solid — self-intersecting geometry `BOPAlgo_SelfIntersect` finds and names by
the failing object (`isValid()` also returns `False` on this fixture;
`isValid()`/volume-sign stay in Preflight because they are cheap, and
`BOPAlgo_SelfIntersect` is reserved for Assertion because it is not).
"""
import pytest

from core.loop.assertion import SELF_INTERSECTION_CHECK
from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

CLEAN_BOX = """
box = doc.addObject('Part::Box', 'Box')
box.Length = box.Width = box.Height = 10.0
doc.recompute()
"""

TWISTED_SOLID = """
import Part, FreeCAD
V = FreeCAD.Vector
bowtie = Part.makePolygon([V(0, 0, 0), V(20, 20, 0), V(20, 0, 0), V(0, 20, 0), V(0, 0, 0)])
shell = bowtie.extrude(V(0, 0, 10))
twist = doc.addObject('Part::Feature', 'Twist')
twist.Shape = Part.makeSolid(shell)
doc.recompute()
"""

TWO_OVERLAPPING_SOLIDS = """
first = doc.addObject('Part::Box', 'First')
first.Length = first.Width = first.Height = 10.0
second = doc.addObject('Part::Box', 'Second')
second.Length = second.Width = second.Height = 10.0
second.Placement.Base = FreeCAD.Vector(5, 0, 0)
doc.recompute()
"""


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_clean_solid_holds(worker):
    """A Box passes: nothing named `failed`."""
    checked = worker.check(CLEAN_BOX + SELF_INTERSECTION_CHECK, None)
    assert checked.ok, checked.traceback
    assert checked.held == ["Box"]
    assert not checked.failed


def test_a_self_intersecting_solid_is_named_by_the_check(worker):
    """A solid folded back on itself fails, and is named by its own object name."""
    checked = worker.check(TWISTED_SOLID + SELF_INTERSECTION_CHECK, None)
    assert not checked.ok
    assert "Twist" in checked.traceback
    assert "BOPAlgo_SelfIntersect" in checked.traceback


def test_two_separately_valid_solids_that_overlap_each_other_still_hold(worker):
    """
    Body-to-body (pairwise) intersection is out of scope for this
    epic. Two Boxes that overlap in space are still each, individually, a
    valid solid — this check names neither of them.
    """
    checked = worker.check(TWO_OVERLAPPING_SOLIDS + SELF_INTERSECTION_CHECK, None)
    assert checked.ok, checked.traceback
    assert sorted(checked.held) == ["First", "Second"]
    assert not checked.failed


PADDED_BODY = """
import Part, FreeCAD
body = doc.addObject('PartDesign::Body', 'Body')
sketch = body.newObject('Sketcher::SketchObject', 'Sketch')
sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]
sketch.MapMode = 'FlatFace'
sketch.addGeometry(Part.Circle(FreeCAD.Vector(), FreeCAD.Vector(0, 0, 1), 5))
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 5
doc.recompute()
"""


def test_a_body_is_checked_as_the_part_and_not_feature_by_feature(worker):
    """A feature's Shape is the Body as it stood at that feature; a later feature may repair it, and only the Body is the part."""
    checked = worker.check(PADDED_BODY + SELF_INTERSECTION_CHECK, None)
    assert checked.ok, checked.traceback
    assert checked.held == ["Body"]
