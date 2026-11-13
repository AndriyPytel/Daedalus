"""
Level 2 -- Preflight refuses a Step that leaves a Body's Shape invalid or
inverted.

The pieces check is the only shape check Preflight runs today,
and it only counts solids per `PartDesign::Body`: it says nothing about
whether the solid it counted is itself valid, or whether it came out
inverted. Both defects recompute clean -- FreeCAD reports the feature
up-to-date and valid -- so nothing short of a dedicated check ever sees
them.

Run against a real headless FreeCAD through the real Preflight Worker, same
reasoning as `test_preflight_lost_solids_level2.py`: what is being checked
is what FreeCAD's own `Shape.isValid()` and `Shape.Volume` say, not what a
fake would agree to.

SELF_TOUCHING pads an asymmetric figure-eight profile -- two triangular
lobes of different size sharing one vertex -- which recomputes to a single
solid of substantial, nonzero volume whose `isValid()` is `False`; the
asymmetry keeps the idle-feature check (net volume near zero) from
being the one that fires instead.
INVERTED_VOLUME is a `PartDesign::FeaturePython` Tip whose `execute` builds a
box and reverses it, standing in for "a boss cut instead of added, a profile
wound the wrong way" (the Solution Notes' own example) without depending on
PartDesign's sketch/pad pipeline to ever actually produce one -- it does not,
by design, which is exactly why this defect needs its own check rather than
relying on Pad or Pocket to refuse it first.
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


SELF_TOUCHING = """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
points = [(0, 0), (-20, 10), (-20, -10), (0, 0), (5, 3), (5, -3), (0, 0)]
for i in range(len(points) - 1):
    x1, y1 = points[i]
    x2, y2 = points[i + 1]
    sketch.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y2, 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = sketch
pad.Length = 10
"""

INVERTED_VOLUME = """
class Inverted:
    def __init__(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        box = Part.makeBox(10, 10, 10)
        box.reverse()
        obj.Shape = box

body = doc.addObject('PartDesign::Body', 'Body')
feat = doc.addObject('PartDesign::FeaturePython', 'InvertedBox')
Inverted(feat)
body.addObject(feat)
body.Tip = feat
"""

VALID_BOX = """
body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = sketch
pad.Length = 10
"""


def test_a_body_with_an_invalid_shape_is_refused(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(SELF_TOUCHING)

    assert not result.ok, "a Body whose Shape.isValid() is False was passed to the Document"


def test_the_invalid_shape_failure_names_the_body(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(SELF_TOUCHING)

    said = result.traceback
    assert "Body" in said, said
    assert "invalid" in said.lower(), "the failure does not say what is wrong: %s" % said


def test_a_body_with_an_inverted_solid_is_refused(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(INVERTED_VOLUME)

    assert not result.ok, "a Body with negative signed volume was passed to the Document"


def test_the_inverted_solid_failure_names_the_body(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(INVERTED_VOLUME)

    said = result.traceback
    assert "Body" in said, said
    assert "invert" in said.lower() or "volume" in said.lower(), (
        "the failure does not say what is wrong: %s" % said)


def test_a_valid_upright_body_still_passes(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(VALID_BOX)

    assert result.ok, result.traceback
