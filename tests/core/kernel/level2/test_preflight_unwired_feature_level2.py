"""
Level 2 -- Preflight refuses a Step that leaves a PartDesign feature no
Body claims (FCC-253 Q4 follow-up: the sonnet sweep's 15-repeat failure).

`doc.addObject('PartDesign::Pad', ...)` gives the feature its own valid
Shape -- Pad computes from its Profile regardless of who owns it -- but
never `body.addObject`ing it leaves the Body's own Shape null: reading
`Body.Shape.Volume` raises `RuntimeError('shape is invalid')` while the
feature's own Shape looks correct and FreeCAD calls it Up-to-date.
Verified against a real freecadcmd session before this check was written.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


UNWIRED_PAD = """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
sketch.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 10, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(10, 10, 0), App.Vector(0, 10, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(0, 10, 0), App.Vector(0, 0, 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 10
doc.recompute()
"""

WIRED_PAD = """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
sketch.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 10, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(10, 10, 0), App.Vector(0, 10, 0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(0, 10, 0), App.Vector(0, 0, 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = sketch
pad.Length = 10
doc.recompute()
"""


def test_a_feature_no_body_claims_is_refused(worker):
    result = worker.check(UNWIRED_PAD, None)

    assert not result.ok, "a Pad no Body's Group claims was passed to the Document"


def test_the_unwired_feature_failure_names_it_and_how_to_fix_it(worker):
    said = worker.check(UNWIRED_PAD, None).traceback

    assert "Pad" in said, "the failure does not name the feature: %s" % said
    assert "body.addObject" in said, "the failure does not say how to fix it: %s" % said


def test_a_feature_added_to_its_body_passes(worker):
    result = worker.check(WIRED_PAD, None)

    assert result.ok, result.traceback
