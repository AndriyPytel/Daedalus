"""
Level 2 — Preflight refuses a Step that leaves a Body in pieces.

A sketch of two disjoint profiles pads into two separate lumps and nothing
raises: the feature reports itself up-to-date and valid. Preflight judged by
whether the Worker raised, so it passed, and the only phase that objected was
Review — at the end of the Plan, from a render.

Run against a real headless FreeCAD through the real Preflight Worker, because
what is being checked is what FreeCAD does rather than what a fake would agree
to.

DISJOINT pads two circles 30 mm apart, which never meet; OVERLAPPING pads two
that fuse into one solid; SINGLE pads one.
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


def _pad_of(*circles):
    """A Body, one sketch of the given circles, one Pad."""
    return """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')
body.addObject(sketch)
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
for x, radius in %s:
    sketch.addGeometry(
        Part.Circle(App.Vector(x, 0, 0), App.Vector(0, 0, 1), radius), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = sketch
pad.Length = 10
""" % (list(circles),)


DISJOINT = _pad_of((0, 5), (30, 5))
OVERLAPPING = _pad_of((0, 5), (6, 5))
SINGLE = _pad_of((0, 5))


def test_a_pad_of_two_disjoint_profiles_fails_preflight(worker):
    result = worker.check(DISJOINT, None)

    assert not result.ok, "a Body left in two pieces was passed to the Document"


def test_the_failure_says_what_happened_rather_than_that_it_failed(worker):
    result = worker.check(DISJOINT, None)

    said = result.traceback
    assert "more than one piece" in said, said
    assert "2 separate solids" in said, "the failure does not say how many: %s" % said
    assert "have to meet" in said, "the failure does not say what to do about it: %s" % said


def test_profiles_that_meet_are_one_solid_and_still_pass(worker):
    """The overlapping case is legitimate — they fuse — and must not be refused."""
    result = worker.check(OVERLAPPING, None)

    assert result.ok, result.traceback


def test_one_profile_still_passes(worker):
    result = worker.check(SINGLE, None)

    assert result.ok, result.traceback


def test_several_unrelated_objects_are_not_a_body_in_pieces(worker):
    """
    The count only means something inside a Body. A Document holding two
    Part primitives is two solids on purpose.
    """
    result = worker.check(
        "a = doc.addObject('Part::Box', 'A')\n"
        "b = doc.addObject('Part::Box', 'B')\n"
        "b.Placement.Base.x = 50\n", None)

    assert result.ok, result.traceback
