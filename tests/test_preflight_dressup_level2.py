"""
Level 2 — Preflight says why a fillet failed, where FreeCAD says nothing.

A PartDesign dressup OCC refuses is Invalid with an empty `getStatusString()`,
so the failure reached Execute as `Fillet (PartDesign::Fillet): ` and the only
thing left to change was how the selection was written
(`logs/sessions/069d21dbac10416392533fe7c06b62d4.jsonl`).
"""
import pytest

from core.preflight import PreflightWorker, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with PreflightWorker() as running:
        yield running


A_LAND_2_MM_WIDE = """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
outer = doc.addObject('Sketcher::SketchObject', 'Outer')
body.addObject(outer)
for a, b in (((0, 0), (30, 0)), ((30, 0), (30, 30)), ((30, 30), (0, 30)), ((0, 30), (0, 0))):
    outer.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0),
                                       App.Vector(b[0], b[1], 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = outer
pad.Length = 10
doc.recompute()

for at, (low, high) in enumerate(((5.0, 14.0), (16.0, 25.0))):
    sketch = doc.addObject('Sketcher::SketchObject', 'Cut' + str(at))
    body.addObject(sketch)
    sketch.Placement = App.Placement(App.Vector(0, 0, 10), App.Rotation())
    for a, b in (((5.0, low), (25.0, low)), ((25.0, low), (25.0, high)),
                 ((25.0, high), (5.0, high)), ((5.0, high), (5.0, low))):
        sketch.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0),
                                            App.Vector(b[0], b[1], 0)), False)
    pocket = doc.addObject('PartDesign::Pocket', 'Pocket' + str(at))
    body.addObject(pocket)
    pocket.Profile = sketch
    pocket.Length = 5
    doc.recompute()

top = body.Tip
face = max(range(len(top.Shape.Faces)),
           key=lambda at: top.Shape.Faces[at].Area
           if abs(top.Shape.Faces[at].CenterOfMass.z - 10.0) < 1e-6 else -1.0)
fillet = doc.addObject('PartDesign::Fillet', 'Fillet')
body.addObject(fillet)
fillet.Base = (top, ['Face' + str(face + 1)])
"""


def rounding_the_top_face_at(radius):
    """The 2 mm land between two pockets, with its whole top face rounded."""
    return A_LAND_2_MM_WIDE + "fillet.Radius = %s\ndoc.recompute()\n" % radius


def test_a_fillet_that_will_not_fit_fails_preflight(worker):
    result = worker.check(rounding_the_top_face_at(2.0), None)

    assert not result.ok, "a fillet OCC refused was passed to the Document"


def test_the_failure_says_what_freecad_would_not(worker):
    said = worker.check(rounding_the_top_face_at(2.0), None).traceback

    assert "Fillet (PartDesign::Fillet): " in said, said
    assert "is refused with" in said, "the OCC refusal is missing: %s" % said
    assert "Edge" in said, "no edge of the selection is named: %s" % said
    assert "builds at 0.5 mm" in said, "the size that fits is not given: %s" % said


def test_the_same_fillet_small_enough_passes(worker):
    result = worker.check(rounding_the_top_face_at(0.4), None)

    assert result.ok, result.traceback
