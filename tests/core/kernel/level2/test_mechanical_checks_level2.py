"""
Level 2 -- the generated checks and the helpers of the check namespace, run in the real kernel against a 60 x 40 x 10 plate
with a \u00d86 bore along z at x=20 and a \u00d810 bore along z at x=45, and against plates that break each entry.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd
from core.loop.assertion import the_envelope_measured
from core.loop.mechanical import covered_entries, the_sizes_measured
from tests.core.kernel import part_202

pytestmark = pytest.mark.freecad

_PLATE = """
plate = Part.makeBox(60, 40, 10)
plate = plate.cut(Part.makeCylinder(3, 10, App.Vector(20, 20, 0)))
plate = plate.cut(Part.makeCylinder(5, 10, App.Vector(45, 20, 0)))
%s
obj = doc.addObject('Part::Feature', 'Plate')
obj.Shape = plate
doc.recompute()
"""

_DATUMS = {"datums": [{"name": "the height", "fixes": "the height", "is": "z"},
                      {"name": "the left end face", "fixes": "the length", "is": "the plane x = 0, facing -x"}]}


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _plate(worker, extra=""):
    result = worker.check(_PLATE % extra, None, design=_DATUMS)
    assert result.ok, result.traceback


def _generated(entries):
    description = {"datums": [
        {"name": "the length", "fixes": "the length", "is": "the plane x = 0"},
        {"name": "the height", "fixes": "the height", "is": "the plane z = 10"}]}
    assert covered_entries(entries, description, None) == entries
    return the_envelope_measured(entries) + the_sizes_measured(entries, description)


@pytest.mark.parametrize("entries", [
    ["features \u2192 bore \u2192 diameter: \u00d86"],
    ["features \u2192 bore \u2192 diameter: 10 mm"],
    ["features \u2192 bore \u2192 diameter: \u00d86.3"],
    ["features \u2192 round \u2192 radius: R3"],
    ["features \u2192 bore \u2192 diameter: \u00d86", "features \u2192 bore \u2192 axis: parallel to the height"],
    ["features \u2192 bore \u2192 diameter: \u00d86", "features \u2192 bore \u2192 count: 1"],
    ["envelope \u2192 length: 60 mm", "envelope \u2192 width: 40 mm", "envelope \u2192 height: 10 mm"],
    ["envelope \u2192 length: 60 mm", "envelope \u2192 depth front to rear: 40 mm",
     "envelope \u2192 width: 40 mm", "envelope \u2192 height: 10 mm"],
    ["features \u2192 bore \u2192 diameter: \u00d86 bore through (was 5)", "features \u2192 bore \u2192 axis: along the height"],
])
def test_a_part_that_meets_the_entries_passes_the_generated_check(worker, entries):
    _plate(worker)
    result = worker.check(_generated(entries))
    assert result.ok, result.traceback


@pytest.mark.parametrize("entries,broken", [
    (["features \u2192 bore \u2192 diameter: \u00d88"], 0),
    (["features \u2192 bore \u2192 diameter: \u00d86", "features \u2192 bore \u2192 count: 2"], 1),
    (["features \u2192 bore \u2192 diameter: \u00d86", "features \u2192 bore \u2192 axis: parallel to the length"], 1),
    (["features \u2192 round \u2192 radius: R7"], 0),
    (["envelope \u2192 length: 60 mm", "envelope \u2192 width: 40 mm", "envelope \u2192 height: 25 mm"], None),
    (["envelope \u2192 length: 60 mm", "envelope \u2192 depth front to rear: 40 mm",
      "envelope \u2192 width: 40 mm", "envelope \u2192 height: 25 mm"], None),
])
def test_a_part_that_breaks_an_entry_fails_naming_that_entry(worker, entries, broken):
    _plate(worker)
    result = worker.check(_generated(entries))
    assert not result.ok
    assert "these did not hold" in result.traceback
    if broken is not None:
        assert entries[broken] in result.traceback
        assert [e for i, e in enumerate(entries) if i != broken and e in result.traceback] == []


def test_a_rounded_straight_edge_meets_a_radius_entry(worker):
    _plate(worker, "plate = plate.makeFillet(1.5, [plate.Edges[0]])")
    assert worker.check(_generated(["features \u2192 round \u2192 radius: R1.5"])).ok


def test_a_rounded_circular_edge_meets_a_radius_entry(worker):
    rim = "plate = plate.makeFillet(1.5, [next(e for e in plate.Edges if e.Curve.__class__.__name__ == 'Circle' and abs(e.Curve.Radius - 3) < 1e-6 and e.Vertexes[0].Point.z > 5)])"
    _plate(worker, rim)
    assert worker.check("assert any(s.__class__.__name__ == 'Toroid' for s in (f.Surface for f in _faces.shape.Faces))").ok
    result = worker.check(_generated(["features \u2192 round \u2192 radius: R1.5"]))
    assert result.ok, result.traceback


def test_the_generated_check_is_cheap_on_a_part_of_two_thousand_faces(worker):
    worker.check(_PLATE % "plate = Part.makeCompound([Part.makeCylinder(0.2, 1, App.Vector(2 * i, 0, 0)) for i in range(700)])", None)
    import time
    start = time.time()
    result = worker.check(_generated(["features \u2192 bore \u2192 diameter: 0.4 mm"]))
    assert result.ok, result.traceback
    assert time.time() - start < 5


_HELPERS = """
held = []
failed = []
left = face_at('-x')
right = face_at('+x')
bore = cylinder(6, 'z')
wide = cylinder(10, 'z')
facts = [
    ("-x end face is the plane x=0", abs(left.Surface.Position.x) < 1e-6 and abs(left.Area - 400) < 1e-6),
    ("+x end face is the plane x=60", abs(right.Surface.Position.x - 60) < 1e-6),
    ("the plate is 60 long", abs(distance(left, right) - 60) < 1e-6),
    ("the bore axis is 20 from the left end", abs(distance(bore, left) - 20) < 1e-6),
    ("the bores are 25 apart", abs(distance(bore, wide) - 25) < 1e-6),
    ("a named datum is the left end", face_at('the left end face').isSame(left)),
]
for name, ok in facts:
    (held if ok else failed).append(name)
assert not failed, "these did not hold: " + "; ".join(failed)
"""


def test_a_model_written_check_can_use_face_at_distance_and_cylinder(worker):
    _plate(worker)
    result = worker.check(_HELPERS)
    assert result.ok, result.traceback
    assert len(result.held) == 6


@pytest.mark.parametrize("call,said", [
    ("cylinder(7, 'z')", "found 0"),
    ("cylinder(6, 'x')", "found 0"),
    ("face_at('the lowest shelf')", "the lowest shelf"),
    ("face_at('+q')", "+q"),
    ("distance(face_at('-x'), face_at('+z'))", "parallel"),
])
def test_a_helper_that_cannot_answer_raises_with_what_it_found(worker, call, said):
    _plate(worker)
    result = worker.check(call)
    assert not result.ok
    assert said in result.traceback


_TWO_PLANES = {"datums": [
    {"name": "the end face", "fixes": "the length", "is": "the plane x = 0 before the edit, x = 60 after it"},
    {"name": "the stale face", "fixes": "the length", "is": "the plane x = 0 before the edit, x = 70 after it"},
    {"name": "the bore axis", "fixes": "the width and the height", "is": "the axis at y = 20, z = 5"},
]}


def test_a_datum_named_by_two_planes_resolves_to_the_one_with_a_face_and_is_ambiguous_when_both_have_one(worker):
    worker.check(_PLATE % "", None, design=_TWO_PLANES)
    assert worker.check("assert abs(face_at('the stale face').Surface.Position.x) < 1e-6").ok
    both = worker.check("face_at('the end face')")
    assert not both.ok and "ambiguous" in both.traceback and "x = 0" in both.traceback and "x = 60" in both.traceback


def test_a_datum_that_is_an_axis_is_refused_with_a_pointer_to_cylinder_and_distance(worker):
    worker.check(_PLATE % "", None, design=_TWO_PLANES)
    result = worker.check("face_at('the bore axis')")
    assert not result.ok and "axis" in result.traceback and "cylinder" in result.traceback


@pytest.fixture(scope="module")
def part():
    import FreeCAD

    document = part_202.opened()
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


def _the_commonest_cylinder(shape):
    from collections import Counter

    seen = Counter()
    for face in shape.Faces:
        surface = face.Surface
        if surface.__class__.__name__ == "Cylinder":
            along = (surface.Axis.x, surface.Axis.y, surface.Axis.z)
            axis = max(range(3), key=lambda at: abs(along[at]))
            seen[(round(surface.Radius * 2, 3), "xyz"[axis])] += 1
    (diameter, letter), count = seen.most_common(1)[0]
    return diameter, letter, count


def _the_biggest_plane(shape):
    face = max((f for f in shape.Faces if f.Surface.__class__.__name__ == "Plane"), key=lambda f: f.Area)
    normal, position = face.Surface.Axis, face.Surface.Position
    letter = max("xyz", key=lambda one: abs(getattr(normal, one)))
    return letter, getattr(position, letter)


def test_the_generated_checks_and_each_helper_take_five_seconds_at_most_on_part_202(part):
    import time

    from core.kernel import kernel_worker

    shape = max((o for o in part.Objects if getattr(o, "Shape", None) is not None and o.Shape.Solids
                 and getattr(o.getParentGeoFeatureGroup(), "TypeId", "") != "PartDesign::Body"),
                key=lambda o: o.Shape.Volume).Shape
    diameter, letter, count = _the_commonest_cylinder(shape)
    plane, at = _the_biggest_plane(shape)
    design = {"datums": [{"name": "the big face", "fixes": "the length", "is": "the plane %s = %r" % (plane, at)}]}
    entries = ["features \u2192 round \u2192 diameter: %s mm" % diameter,
               "features \u2192 round \u2192 axis: along the Document's %s axis" % letter.upper(),
               "features \u2192 round \u2192 count: %d" % count]
    code = the_sizes_measured(entries, {}) + """
cylinder(%s, %r, expect=%d)
a = face_at('the big face')
distance(a, a)
""" % (diameter, letter, count)
    took = float("inf")
    for _ in range(3):
        namespace = kernel_worker.step_namespace(part, design=design)
        started = time.perf_counter()
        exec(compile(code, "<generated>", "exec"), namespace)  # noqa: S102
        took = min(took, time.perf_counter() - started)
    assert took <= 5.0, "the generated checks and helpers took %.1f s on part 202, the threshold is 5.0 s" % took
