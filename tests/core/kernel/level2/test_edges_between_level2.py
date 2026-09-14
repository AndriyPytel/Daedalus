"""
Level 2 -- `edges_between` answers "the edges where face A meets face B" by
predicate, not by a literal `Edge12`/`Face3` name the lint refuses and a
recompute can renumber.

`A_PLATE_WITH_TWO_BOSSES` pads a plate and welds two cylindrical bosses to
its top face, standing in for the arm/collar story
(`logs/sessions/fb37f67515d646e2a887f714b81cd5db.jsonl`): the arm's top face
meets the collar's cylinder in exactly 2 arcs, one full circle per boss.
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


A_PLATE_WITH_TWO_BOSSES = """
import Part

box = Part.makeBox(80, 40, 15)
boss_a = Part.makeCylinder(12, 10, App.Vector(20, 20, 15))
boss_b = Part.makeCylinder(12, 10, App.Vector(60, 20, 15))
result = box.fuse(boss_a).fuse(boss_b)
obj = doc.addObject('Part::Feature', 'Result')
obj.Shape = result
doc.recompute()

def is_arm_top(face):
    return (face.Surface.__class__.__name__ == 'Plane'
            and abs(face.Surface.Axis.z) > 0.99
            and abs(face.CenterOfMass.z - 15.0) < 1e-6)

def is_collar(face):
    return (face.Surface.__class__.__name__ == 'Cylinder'
            and abs(face.Surface.Radius - 12.0) < 1e-6)
"""


def test_it_finds_exactly_the_edges_where_the_two_selections_meet(worker):
    code = A_PLATE_WITH_TWO_BOSSES + "held = edges_between(obj.Shape, is_arm_top, is_collar)\n"

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert len(result.held) == 2, "expected one full circle per boss, got %r" % result.held


def test_the_edges_it_finds_are_the_two_full_circles_by_isSame(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "names = edges_between(obj.Shape, is_arm_top, is_collar)\n"
        "collar_faces = [f for f in obj.Shape.Faces if is_collar(f)]\n"
        "found = obj.Shape.getElement(names[0])\n"
        "held = [found.Length, len(collar_faces)]\n"
    )

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert result.held[0] == pytest.approx(2 * 3.141592653589793 * 12, rel=1e-6)
    assert result.held[1] == 2


def test_a_selection_that_matches_no_face_raises_with_what_it_wanted(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "edges_between(obj.Shape, lambda face: False, is_collar)\n"
    )

    result = worker.check(code, None)

    assert not result.ok
    assert "matched no face" in result.traceback


def test_a_non_predicate_argument_raises_immediately(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "edges_between(obj.Shape, ['Face1'], is_collar)\n"
    )

    result = worker.check(code, None)

    assert not result.ok
    assert "predicates" in result.traceback


def test_a_face_matching_both_predicates_does_not_pair_with_itself(worker):
    """
    A face satisfying both *a* and *b* must not make its own boundary read
    as "where a meets b" -- only an edge with one adjacent face in each set
    counts, never one face standing in for both sides of its own edge.
    """
    code = A_PLATE_WITH_TWO_BOSSES + (
        "held = edges_between(obj.Shape, is_arm_top, is_arm_top)\n"
    )

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert result.held == []
