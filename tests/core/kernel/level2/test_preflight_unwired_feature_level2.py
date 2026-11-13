"""
Level 2 -- Preflight refuses a Step that leaves a PartDesign feature no
Body claims (the sonnet sweep's 15-repeat failure).

`doc.addObject('PartDesign::Pad', ...)` gives the feature its own valid
Shape -- Pad computes from its Profile regardless of who owns it -- but
never `body.addObject`ing it leaves the Body's own Shape null: reading
`Body.Shape.Volume` raises `RuntimeError('shape is invalid')` while the
feature's own Shape looks correct and FreeCAD calls it Up-to-date.
Verified against a real freecadcmd session before this check was written.
"""
import pytest

from core.agent import part_rules
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
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(UNWIRED_PAD)

    assert not result.ok, "a Pad no Body's Group claims was passed to the Document"


def test_the_unwired_feature_failure_names_it_and_how_to_fix_it(worker):
    assert worker.seed(rules=part_rules).ok
    said = worker.rehearse(UNWIRED_PAD).traceback

    assert "Pad" in said, "the failure does not name the feature: %s" % said
    assert "body.addObject" in said, "the failure does not say how to fix it: %s" % said


def test_a_feature_added_to_its_body_passes(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(WIRED_PAD)

    assert result.ok, result.traceback


REFUSAL = (
    "this Step created a feature no Body claims:\nPad\n"
    "A feature added with doc.addObject computes its own Shape from its Profile and looks correct on its own, "
    "but the Body it was meant to extend still has none -- its own Shape.Volume raises 'shape is invalid' "
    "until the feature is wired in with body.addObject(feature) and body.Tip = feature, "
    "the same way the Body's other features were added.\n")


def test_part_rules_refuse_through_seed(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(UNWIRED_PAD)

    assert not result.ok
    assert result.traceback.endswith("RuntimeError: " + REFUSAL)


def test_a_kernel_seeded_without_rules_lands_the_same_step(worker):
    assert worker.seed().ok
    result = worker.rehearse(UNWIRED_PAD)

    assert result.ok, result.traceback
    assert "Pad" in result.created


def test_a_restarted_kernel_keeps_its_rules(worker):
    assert worker.seed(rules=part_rules).ok
    worker.restart()

    assert not worker.rehearse(UNWIRED_PAD).ok
