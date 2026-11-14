"""
Level 2 — Preflight says why a fillet failed, where FreeCAD says nothing.

A PartDesign dressup OCC refuses is Invalid with an empty `getStatusString()`,
so the failure reached Execute as `Fillet (PartDesign::Fillet): ` and the only
thing left to change was how the selection was written
(`logs/sessions/069d21dbac10416392533fe7c06b62d4.jsonl`).
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
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(rounding_the_top_face_at(2.0))

    assert not result.ok, "a fillet OCC refused was passed to the Document"


def test_the_failure_says_what_freecad_would_not(worker):
    assert worker.seed(rules=part_rules).ok
    said = worker.rehearse(rounding_the_top_face_at(2.0)).traceback

    assert "Fillet (PartDesign::Fillet): " in said, said
    assert "is refused with" in said, "the OCC refusal is missing: %s" % said
    assert "Edge" in said, "no edge of the selection is named: %s" % said
    assert "goes through at 0.5 mm" in said, "the size that fits is not given: %s" % said


def test_the_same_fillet_small_enough_passes(worker):
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(rounding_the_top_face_at(0.4))

    assert result.ok, result.traceback


A_RIB_4_MM_THICK = """
import math
import Part

body = doc.addObject('PartDesign::Body', 'Body')
profile = doc.addObject('Sketcher::SketchObject', 'Profile')
body.addObject(profile)
profile.Placement = App.Placement(App.Vector(0, 0, 0),
                                  App.Rotation(App.Vector(1, 0, 0), 90))
for a, b in (((5, 0), (50, 0)), ((50, 0), (50, 15)), ((50, 15), (5, 0))):
    profile.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0),
                                         App.Vector(b[0], b[1], 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = profile
pad.Length = 4
doc.recompute()

along = App.Vector(45.0, 0.0, 15.0)
along = along * (1.0 / along.Length)
sloping = []
for at, edge in enumerate(pad.Shape.Edges):
    if not isinstance(edge.Curve, Part.Line):
        continue
    if abs(edge.Length - math.hypot(45.0, 15.0)) > 1e-6:
        continue
    ends = [vertex.Point for vertex in edge.Vertexes]
    run = ends[1] - ends[0]
    if abs(abs((run * (1.0 / run.Length)).dot(along)) - 1.0) > 1e-6:
        continue
    sloping.append(at)
if len(sloping) != 2:
    raise RuntimeError('the rib has 2 sloping top edges, found %d' % len(sloping))
fillet = doc.addObject('PartDesign::Fillet', 'Fillet')
body.addObject(fillet)
fillet.Base = (pad, ['Edge%d' % (at + 1) for at in sloping])
"""


def rounding_the_rib_top_at(radius):
    """The rib 4 mm thick, both edges of its sloping top rounded at once."""
    return A_RIB_4_MM_THICK + "fillet.Radius = %s\ndoc.recompute()\n" % radius


def test_a_full_round_at_half_the_thickness_is_refused(worker):
    """
    The radius at which the two rounds meet is the one OCC will not build: the
    face between them has no width left at the moment they touch, so a full
    round has to be built as the shape it is and cannot be filleted to.
    """
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(rounding_the_rib_top_at(2.0))

    assert not result.ok, "a full round OCC refused was passed to the Document"


def test_the_same_round_a_shade_under_half_the_thickness_builds(worker):
    """
    What separates the two is the degeneracy alone. The round that stops
    0.001 mm short of meeting leaves a face between, and builds.
    """
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(rounding_the_rib_top_at(1.999))

    assert result.ok, result.traceback
