import sheet_dimensions as sd
from conftest import box, drill
from test_dimensions import by_kind


def holes(part, points, radius=3.0):
    for x, y in points:
        part = drill(part, x, y, radius)
    return part


def linear(dims, axis):
    return sorted(round(d.value, 6) for d in by_kind(dims, "linear") if d.axis == axis)


def test_mirrored_holes_get_a_centre_plane_one_side_and_the_pair_distance():
    part = holes(box(100, 60, 20), [(30, 15), (30, 45)])
    dims = sd.dimensions(part)
    symmetry = by_kind(dims, "symmetry")
    assert [(d.axis, [round(c[1], 2) for c in d.covers]) for d in symmetry] == [(1, [45.0])]
    assert linear(dims, 1) == [15.0, 30.0]
    assert sd.missing(part, dims) == []
    assert sd.missing(part, [d for d in dims if d.kind != "symmetry"])


def test_holes_at_equal_pitch_are_one_eq_spacing_dim():
    part = holes(box(100, 60, 20), [(10, 30), (25, 30), (40, 30), (55, 30)])
    dims = sd.dimensions(part)
    spacing = by_kind(dims, "eq_spacing")
    assert [(d.axis, round(d.value, 6), d.count) for d in spacing] == [(0, 15.0, 3)]
    assert linear(dims, 0) == [10.0]
    assert sd.missing(part, dims) == []
    assert sd.missing(part, [d for d in dims if d.kind != "eq_spacing"])


def test_holes_on_a_bolt_circle_are_one_eq_spacing_dim_with_the_pcd():
    import math

    points = [(50 + 20 * math.cos(math.radians(60 * i + 10)), 30 + 20 * math.sin(math.radians(60 * i + 10))) for i in range(6)]
    part = holes(box(100, 60, 20), points)
    dims = sd.dimensions(part)
    spacing = by_kind(dims, "eq_spacing")
    assert len(spacing) == 1 and spacing[0].count == 6 and "PCD" in spacing[0].label and "40" in spacing[0].label
    assert len(by_kind(dims, "linear")) <= 4
    assert sd.missing(part, dims) == []
    assert sd.missing(part, [d for d in dims if d.kind != "eq_spacing"])


def test_concentric_holes_share_one_location():
    part = holes(drill(box(100, 60, 20), 30, 30, 6, z=10.0), [(30, 30)], 3.0)
    dims = sd.dimensions(part)
    assert len(by_kind(dims, "concentric")) == 1
    assert linear(dims, 0) == [30.0] and linear(dims, 1) == [30.0]
    assert sd.missing(part, dims) == []


def test_fillets_and_chamfers_below_the_threshold_are_noted_not_dimensioned():
    from conftest import fillet_circles

    part = fillet_circles(holes(box(100, 60, 20), [(30, 30)], 8.0), 0.2)
    dims = sd.dimensions(part)
    notes = by_kind(dims, "note")
    assert [n.label for n in notes] == ["UNSPECIFIED FILLETS R0.2"]
    assert 0.2 not in [round(d.value, 6) for d in by_kind(dims, "radius")]
    assert sd.missing(part, dims) == []
    assert sd.missing(part, [d for d in dims if d.kind != "note"])
