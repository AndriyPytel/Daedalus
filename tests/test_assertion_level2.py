"""
Level 2 — the Assertion phase against a real headless FreeCAD (FCC-76).

A fake would agree with any measurement, which is exactly the thing being
checked here: that a distance taken between two antiparallel planar faces is
the number on the drawing however the part is turned, while the axis-aligned
bounding box is not (ADR-0007).

The harness is the Preflight Worker, because that is what actually runs the
checks: it already `exec`s code in a namespace with `doc`, `App` and `Part`,
already returns any `BaseException` as a traceback, and already opens a copy
from disk rather than the live Document.

NUT is an ISO 4032 M8 nut, near enough: 13 across the flats, 6.5 tall, 4.0 bore
radius, and turned so that nothing here is axis-aligned.

ACROSS_THE_FLATS is what the phase is meant to write: the distance between two
faces that face each other, taken off the Shape rather than off the box around
it.
"""
import math

import pytest

from core.freecad_apply import ApplyPipeline
from core.loop.assertion import _the_model_failed
from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

NUT = """
import math
hexagon = doc.addObject('Part::Prism', 'Nut')
hexagon.Polygon = 6
hexagon.Circumradius = 13.0 / math.sqrt(3)
hexagon.Height = 6.5
bore = doc.addObject('Part::Cylinder', 'Bore')
bore.Radius = 4.0
bore.Height = 20
bore.Placement.Base.z = -5
cut = doc.addObject('Part::Cut', 'NutBlank')
cut.Base = hexagon
cut.Tool = bore
cut.Placement.Rotation = App.Rotation(App.Vector(0, 0, 1), %s)
doc.recompute()
"""

ACROSS_THE_FLATS = """
faces = [f for f in doc.getObject('NutBlank').Shape.Faces
         if f.Surface.__class__.__name__ == 'Plane']
upright = [f for f in faces if abs(f.Surface.Axis.z) < 1e-6]
gaps = [a.Surface.Position.distanceToPlane(b.Surface.Position, b.Surface.Axis)
        for a in upright for b in upright
        if a.Surface.Axis.dot(b.Surface.Axis) < -0.999]
across = max(abs(gap) for gap in gaps)
assert abs(across - 13.0) < 1e-6, "13 mm across the flats"
"""

BORE_RADIUS = """
cylinders = [f for f in doc.getObject('NutBlank').Shape.Faces
             if f.Surface.__class__.__name__ == 'Cylinder']
radius = min(f.Surface.Radius for f in cylinders)
assert abs(radius - 4.0) < 1e-6, "M8 through hole, 4.0 mm bore radius"
"""


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def check(worker, code, angle=0):
    return worker.check(NUT % angle + code, None)


def test_a_check_that_holds_comes_back_ok(worker):
    assert check(worker, ACROSS_THE_FLATS).ok


def test_a_check_that_does_not_hold_names_the_entry_it_came_from(worker):
    """
    The assert message is the acceptance entry, because a failure Plan cannot
    place is a failure Plan cannot fix.
    """
    result = check(worker, ACROSS_THE_FLATS.replace("13.0)", "17.0)")
                  .replace("13 mm", "17 mm"))

    assert not result.ok
    assert "AssertionError" in result.traceback
    assert "17 mm across the flats" in result.traceback


def test_an_entry_that_fails_does_not_hide_the_entries_under_it(worker):
    """
    Every entry is measured before anything is reported (FCC-103).

    A recorded run wrote one assert per entry, put the material third, got it
    wrong, and stopped there — the forty geometric entries below it were never
    measured, so Plan revised the material and sent back a part whose five
    bores had cut nothing. Both entries here are wrong, and both have to be
    named, or the second failure is invisible behind the first.

    A message this long does not stay on one line, and a failure of the part
    misread as a broken check would spend the phase's attempts rewriting it.
    """
    both_wrong = """
faces = [f for f in doc.getObject('NutBlank').Shape.Faces
         if f.Surface.__class__.__name__ == 'Plane']
upright = [f for f in faces if abs(f.Surface.Axis.z) < 1e-6]
gaps = [a.Surface.Position.distanceToPlane(b.Surface.Position, b.Surface.Axis)
        for a in upright for b in upright
        if a.Surface.Axis.dot(b.Surface.Axis) < -0.999]
across = max(abs(gap) for gap in gaps)
cylinders = [f for f in doc.getObject('NutBlank').Shape.Faces
             if f.Surface.__class__.__name__ == 'Cylinder']
radius = min(f.Surface.Radius for f in cylinders)

failed = []
if not abs(across - 17.0) < 1e-6:
    failed.append("17 mm across the flats")
if not abs(radius - 9.0) < 1e-6:
    failed.append("9.0 mm bore radius")
assert not failed, "these did not hold:\\n" + "\\n".join(failed)
"""

    result = check(worker, both_wrong)

    assert not result.ok
    assert "17 mm across the flats" in result.traceback
    assert "9.0 mm bore radius" in result.traceback, "the second entry went unmeasured"
    assert _the_model_failed(result.traceback)


def test_a_mixed_check_reports_what_held_beside_what_failed(worker):
    """
    The whole mechanism this ticket adds: `held` and `failed` are plain lists
    the script itself builds, still readable out of the worker's namespace
    after the single assert at the end raises — nothing here is inferred or
    parsed back out of the traceback string.
    """
    one_right_one_wrong = """
faces = [f for f in doc.getObject('NutBlank').Shape.Faces
         if f.Surface.__class__.__name__ == 'Plane']
upright = [f for f in faces if abs(f.Surface.Axis.z) < 1e-6]
gaps = [a.Surface.Position.distanceToPlane(b.Surface.Position, b.Surface.Axis)
        for a in upright for b in upright
        if a.Surface.Axis.dot(b.Surface.Axis) < -0.999]
across = max(abs(gap) for gap in gaps)
cylinders = [f for f in doc.getObject('NutBlank').Shape.Faces
             if f.Surface.__class__.__name__ == 'Cylinder']
radius = min(f.Surface.Radius for f in cylinders)

held = []
failed = []
if abs(across - 13.0) < 1e-6:
    held.append("13 mm across the flats")
else:
    failed.append("13 mm across the flats")
if abs(radius - 9.0) < 1e-6:
    held.append("9.0 mm bore radius")
else:
    failed.append("9.0 mm bore radius")
assert not failed, "these did not hold: " + "; ".join(failed)
"""

    result = check(worker, one_right_one_wrong)

    assert not result.ok
    assert result.held == ["13 mm across the flats"]
    assert result.failed == ["9.0 mm bore radius"]
    assert "9.0 mm bore radius" in result.traceback
    assert _the_model_failed(result.traceback)


@pytest.mark.parametrize("angle", [0, 17, 30, 45, 137])
def test_the_flats_measure_13_however_the_nut_is_turned(worker, angle):
    """
    The test this whole phase exists for. The same nut, turned, measured two
    ways: on its faces it is 13.0 at every angle; through the bounding box it
    is a number that is on no drawing.
    """
    assert check(worker, ACROSS_THE_FLATS, angle=angle).ok
    assert check(worker, BORE_RADIUS, angle=angle).ok


def test_the_bounding_box_is_the_measurement_this_replaces(worker):
    """
    Not a check of our code — a check of the premise. If the envelope of a
    turned nut did carry the drawing's numbers, the phase would not be needed.
    """
    reports_the_drawing = """
box = doc.getObject('NutBlank').Shape.BoundBox
sides = [box.XLength, box.YLength]
assert any(abs(side - 13.0) < 1e-6 for side in sides), "the bounding box carries 13 mm"
"""

    assert check(worker, reports_the_drawing, angle=0).ok, \
        "square on, one side of the box happens to be the 13 mm"

    turned = check(worker, reports_the_drawing, angle=17)
    assert not turned.ok, "a turned nut's bounding box must not report 13 mm"
    assert "AssertionError" in turned.traceback


def test_a_broken_check_is_not_read_as_a_failed_model(worker):
    """
    An `AttributeError` in the checking code says nothing about the nut. The
    traceback is real here rather than hand-written, because what is being
    checked is how the classification reads what FreeCAD actually produces.
    """
    result = check(worker, "assert doc.getObject('NutBlank').Flats == 13.0, 'flats'\n")

    assert not result.ok
    assert "AttributeError" in result.traceback
    assert not _the_model_failed(result.traceback)


def test_an_assertion_error_is_read_as_a_failed_model(worker):
    result = check(worker, ACROSS_THE_FLATS.replace("13.0)", "17.0)"))

    assert _the_model_failed(result.traceback)


def test_a_document_that_will_not_recompute_is_read_as_a_failed_model(worker):
    """
    A part already in error before anything was measured. It fails the run the
    same way — but the feedback has to say so, not claim a dimension is wrong.
    """
    broken = ("body = doc.addObject('PartDesign::Body', 'Body')\n"
              "sketch = doc.addObject('Sketcher::SketchObject', 'Open')\n"
              "body.addObject(sketch)\n"
              "sketch.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), "
              "App.Vector(10, 0, 0)), False)\n"
              "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
              "body.addObject(pad)\n"
              "pad.Profile = sketch\n"
              "pad.Length = 5\n")

    result = worker.check(broken, None)

    assert not result.ok
    assert _the_model_failed(result.traceback)


def test_the_phase_leaves_the_live_document_exactly_as_it_found_it():
    """
    Read-only by construction: the checks run in the Worker, against a copy
    opened from disk, so the live Document is not reachable from where they
    run. Asserted rather than assumed, including the undo stack — a check that
    left an undo entry would be a Step the user could unwittingly undo.
    """
    import FreeCAD

    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    doc = FreeCAD.newDocument("AssertionLevel2")
    doc.UndoMode = 1
    try:
        box = doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 10, 20, 5
        doc.recompute()
        doc.clearUndos()

        before = [(o.Name, o.Shape.Volume) for o in doc.Objects]

        with Kernel() as running:
            pipeline = ApplyPipeline(document=doc, worker=running,
                                     dispatch=lambda func, timeout=None: func())
            failed = pipeline.check_assertions(
                "doc.getObject('Box').Length = 999\n"
                "assert doc.getObject('Box').Shape.Volume > 1e9, 'volume'\n")

        assert not failed["ok"]
        assert [(o.Name, o.Shape.Volume) for o in doc.Objects] == before
        assert doc.UndoNames == [], "the checks left an undo entry"
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_passing_check_reaches_the_loop_as_ok():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    import FreeCAD

    doc = FreeCAD.newDocument("AssertionLevel2Ok")
    try:
        box = doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 10, 20, 5
        doc.recompute()

        with Kernel() as running:
            pipeline = ApplyPipeline(document=doc, worker=running,
                                     dispatch=lambda func, timeout=None: func())
            result = pipeline.check_assertions(
                "assert abs(doc.getObject('Box').Shape.Volume - 1000.0) < 1e-6, 'volume'\n")

        assert result["ok"], result["traceback"]
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_math_is_available_to_the_checks(worker):
    """The phase computes; a check that cannot take a square root is no check."""
    assert worker.check("import math\nassert math.sqrt(4) == 2.0, 'math'\n", None).ok
    assert math.sqrt(4) == 2.0
