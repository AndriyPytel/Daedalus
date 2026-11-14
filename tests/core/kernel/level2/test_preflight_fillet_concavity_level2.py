"""
Level 2 -- Preflight refuses a PartDesign::Fillet whose Base mixes concave
and convex edges.

`A_PLATE_WITH_A_SLOT` pads a plate and pockets a through slot in its centre.
The slot's own corner edges -- where two of its wall faces meet -- are
concave (material wraps 270 degrees around each one, the same as an L
bracket's inner corner); the plate's own corner edges are convex (90
degrees), the ordinary case. Both sets are vertical edges of equal length
in the same two planes (XZ and YZ), so length, plane and z tell them apart
from nothing -- only the two adjacent faces' own normals do, which is what
the guard reads.
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


A_PLATE_WITH_A_SLOT = """
import Part

body = doc.addObject('PartDesign::Body', 'Body')
outer = doc.addObject('Sketcher::SketchObject', 'Outer')
body.addObject(outer)
for a, b in (((-20, -20), (20, -20)), ((20, -20), (20, 20)),
             ((20, 20), (-20, 20)), ((-20, 20), (-20, -20))):
    outer.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0),
                                       App.Vector(b[0], b[1], 0)), False)
pad = doc.addObject('PartDesign::Pad', 'Pad')
body.addObject(pad)
pad.Profile = outer
pad.Length = 10
doc.recompute()

slot = doc.addObject('Sketcher::SketchObject', 'Slot')
body.addObject(slot)
slot.Placement = App.Placement(App.Vector(0, 0, 10), App.Rotation())
for a, b in (((-5, -5), (5, -5)), ((5, -5), (5, 5)),
             ((5, 5), (-5, 5)), ((-5, 5), (-5, -5))):
    slot.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0),
                                      App.Vector(b[0], b[1], 0)), False)
pocket = doc.addObject('PartDesign::Pocket', 'Pocket')
body.addObject(pocket)
pocket.Profile = slot
pocket.Type = 'ThroughAll'
doc.recompute()

top = body.Tip
plate_corners = []
slot_corners = []
for at, edge in enumerate(top.Shape.Edges):
    if not isinstance(edge.Curve, Part.Line):
        continue
    mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2.0)
    if abs(mid.z - 5.0) > 1e-6:
        continue
    if abs(abs(mid.x) - 20.0) < 1e-6 and abs(abs(mid.y) - 20.0) < 1e-6:
        plate_corners.append(at)
    elif abs(abs(mid.x) - 5.0) < 1e-6 and abs(abs(mid.y) - 5.0) < 1e-6:
        slot_corners.append(at)
if len(plate_corners) != 4 or len(slot_corners) != 4:
    raise RuntimeError('expected 4 plate corners and 4 slot corners, got %d/%d'
                        % (len(plate_corners), len(slot_corners)))
"""


def a_fillet_on(names):
    return A_PLATE_WITH_A_SLOT + (
        "fillet = doc.addObject('PartDesign::Fillet', 'Fillet')\n"
        "body.addObject(fillet)\n"
        "fillet.Base = (top, %r)\n"
        "fillet.Radius = 1.0\n"
        "doc.recompute()\n" % (names,)
    )


def held_names(worker, expression):
    assert worker.seed(rules=part_rules).ok
    names = worker.measure(
        A_PLATE_WITH_A_SLOT + "import json\nprint('FACTS ' + json.dumps(%s))\n" % expression)
    assert names is not None
    return names


def test_a_fillet_mixing_a_plate_corner_and_a_slot_corner_is_refused(worker):
    mixed_names = held_names(
        worker, "['Edge%d' % (plate_corners[0] + 1), 'Edge%d' % (slot_corners[0] + 1)]")

    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(a_fillet_on(mixed_names))

    assert not result.ok, "a fillet mixing concave and convex Base edges was passed to the Document"


def test_the_mixed_concavity_failure_names_the_edges(worker):
    mixed_names = held_names(
        worker, "['Edge%d' % (plate_corners[0] + 1), 'Edge%d' % (slot_corners[0] + 1)]")

    assert worker.seed(rules=part_rules).ok
    said = worker.rehearse(a_fillet_on(mixed_names)).traceback

    assert "Fillet" in said, said
    assert all(name in said for name in mixed_names), (
        "the failure does not name the mixed edges: %s" % said)


def test_a_fillet_on_the_plates_own_corners_is_accepted(worker):
    plate_names = held_names(worker, "['Edge%d' % (at + 1) for at in plate_corners]")

    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(a_fillet_on(plate_names))

    assert result.ok, result.traceback


def test_a_fillet_on_the_slots_own_corners_is_accepted(worker):
    slot_names = held_names(worker, "['Edge%d' % (at + 1) for at in slot_corners]")

    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(a_fillet_on(slot_names))

    assert result.ok, result.traceback


def test_the_guards_own_cost_stays_under_the_round_trip_it_rides_on(worker):
    """
    Measured against `A_PLATE_WITH_A_SLOT`'s own Fillet, since the session's
    checkpoint the Acceptance Criteria names (`kernel_checkpoint_wggws3z7.FCStd`)
    is a temp file and does not outlive the session that wrote it.
    """
    import time

    plate_names = held_names(worker, "['Edge%d' % (at + 1) for at in plate_corners]")
    code = a_fillet_on(plate_names)
    started = time.perf_counter()
    assert worker.seed(rules=part_rules).ok
    result = worker.rehearse(code)
    round_trip = time.perf_counter() - started
    assert result.ok, result.traceback

    probe = code + (
        "import time\n"
        "started = time.perf_counter()\n"
        "for _ in range(20):\n"
        "    part_rules._fillets_with_mixed_concavity(doc)\n"
        "guard_cost = (time.perf_counter() - started) / 20.0\n"
        "import json\n"
        "print('FACTS ' + json.dumps(guard_cost))\n"
    )
    assert worker.seed(rules=part_rules).ok
    guard_cost = worker.measure("from core.agent import part_rules\n" + probe)
    assert guard_cost is not None

    assert guard_cost < round_trip, (
        "the guard (%.6fs) costs more than the round trip it rides on (%.6fs)"
        % (guard_cost, round_trip))
