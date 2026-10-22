import sheet_dimensions as sd
from conftest import box, wedge_cut


def by_kind(dims, kind):
    return [d for d in dims if d.kind == kind]


def test_envelope_has_one_dim_per_axis(plate):
    dims = sd.dimensions(plate)
    assert sorted(round(d.value, 6) for d in by_kind(dims, "envelope")) == [20.0, 60.0, 100.0]


def test_equal_holes_are_one_counted_group_on_their_face_on_view(plate):
    dims = by_kind(sd.dimensions(plate), "diameter")
    groups = {round(d.value, 6): (d.count, d.view) for d in dims}
    assert groups == {10.0: (1, "Top"), 6.0: (2, "Top")}


def test_hole_centres_are_located_from_the_datum_by_chained_levels(plate):
    dims = by_kind(sd.dimensions(plate), "linear")
    x = sorted(round(d.value, 6) for d in dims if d.axis == 0)
    y = sorted(round(d.value, 6) for d in dims if d.axis == 1)
    assert x == [30.0, 40.0]
    assert y == [15.0, 15.0, 30.0]


def test_a_complete_set_misses_nothing(plate):
    assert sd.missing(plate, sd.dimensions(plate)) == []


def test_a_dropped_dim_is_reported(plate):
    dims = [d for d in sd.dimensions(plate) if not (d.kind == "diameter" and abs(d.value - 10) < 0.01)]
    assert any("10" in item for item in sd.missing(plate, dims))


def test_oblique_plane_gets_an_angle_on_its_edge_on_view():
    part = wedge_cut(box(100, 60, 20), 30)
    dims = sd.dimensions(part)
    angles = by_kind(dims, "angle")
    assert len(angles) == 1 and angles[0].view == "Front"
    assert round(angles[0].value) in (30, 60)
    assert sd.missing(part, dims) == []


def test_complexity_counts_groups_and_directions(plate):
    assert sd.complexity(plate) == {"features": 8, "directions": 3}


def test_countersink_cone_is_a_chamfer_and_fillet_is_an_r(plate):
    from conftest import countersink, fillet_circles

    part = countersink(plate, 30, 30)
    dims = sd.dimensions(part)
    assert [round(d.value, 6) for d in by_kind(dims, "chamfer")] == [3.0]
    assert sd.missing(part, dims) == []
    rounded = fillet_circles(plate, 2.0)
    dims = sd.dimensions(rounded)
    assert 2.0 in [round(d.value, 6) for d in by_kind(dims, "radius")]
    assert sd.missing(rounded, dims) == []


def test_orient_puts_the_longest_axis_on_x_and_the_second_longest_on_z():
    low, high = sd.bounds(sd.orient(box(20, 100, 60)))
    assert [round(v, 6) for v in high - low] == [100.0, 20.0, 60.0]
    assert sd.missing(sd.orient(box(20, 100, 60)), sd.dimensions(sd.orient(box(20, 100, 60)))) == []
