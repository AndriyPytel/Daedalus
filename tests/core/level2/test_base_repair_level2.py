"""
Level 2 -- the repair of an imported editing base that OCC calls invalid, or that fails the official mesh gate.

Each shape is a 10 mm cube whose top face is split by a needle triangle: height 1e-9 mm leaves a face OCC
calls valid and the mesher cannot triangulate, height 0 leaves a face OCC calls invalid.
"""
import pytest

pytestmark = pytest.mark.freecad

Part = pytest.importorskip("FreeCAD") and pytest.importorskip("Part")
V = pytest.importorskip("FreeCAD").Vector


def _polygon(points):
    return Part.Face(Part.makePolygon([V(*p) for p in points + [points[0]]]))


def _cube_with_a_needle(height):
    top = 10
    needle = [(0, 0, top), (10, 0, top), (5, height, top)]
    rest = [(0, 0, top), (5, height, top), (10, 0, top), (10, 10, top), (0, 10, top)]
    faces = [_polygon([(0, 0, 0), (0, 10, 0), (10, 10, 0), (10, 0, 0)]),
             _polygon([(0, 0, 0), (10, 0, 0), (10, 0, top), (0, 0, top)]),
             _polygon([(10, 0, 0), (10, 10, 0), (10, 10, top), (10, 0, top)]),
             _polygon([(10, 10, 0), (0, 10, 0), (0, 10, top), (10, 10, top)]),
             _polygon([(0, 10, 0), (0, 0, 0), (0, 0, top), (0, 10, top)]),
             _polygon(rest), _polygon(needle)]
    return Part.Solid(Part.Shell(faces))


def test_a_valid_manifold_base_is_returned_untouched():
    from core.manifold import repair_base

    box = Part.makeBox(40, 30, 10).cut(Part.makeCylinder(5, 10, V(20, 15, 0)))

    fixed, note = repair_base(box)

    assert fixed is box
    assert note is None


def test_the_needle_that_occ_accepts_fails_the_gate_before_the_repair():
    from core.manifold import check_fc

    solid = _cube_with_a_needle(1e-9)

    assert solid.isValid()
    assert check_fc(solid) is not None


@pytest.mark.parametrize("height", [1e-9, 0])
def test_a_needle_face_is_dropped_and_the_hole_refilled(height):
    from core.manifold import check_fc, repair_base

    solid = _cube_with_a_needle(height)

    fixed, note = repair_base(solid)

    assert fixed is not solid, note
    assert len(fixed.Solids) == 1
    assert fixed.isValid()
    assert check_fc(fixed) is None
    assert abs(fixed.Volume - solid.Volume) / solid.Volume < 0.005
    assert "dropped 1 face" in note


def test_a_repair_that_moves_the_volume_too_far_leaves_the_base_as_it_was(monkeypatch):
    from core import manifold

    solid = _cube_with_a_needle(0)
    monkeypatch.setattr(manifold, "MAX_VOLUME_CHANGE", -1)

    fixed, note = manifold.repair_base(solid)

    assert fixed is solid
    assert "volume" in note
