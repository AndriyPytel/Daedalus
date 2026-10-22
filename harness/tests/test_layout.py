import sheet_layout as sl

DIRECTIONS = {
    "Front": ((0, -1, 0), (1, 0, 0)), "Rear": ((0, 1, 0), (-1, 0, 0)), "Top": ((0, 0, 1), (1, 0, 0)),
    "Bottom": ((0, 0, -1), (1, 0, 0)), "Left": ((1, 0, 0), (0, 1, 0)), "Right": ((-1, 0, 0), (0, -1, 0)),
    "FrontTopLeft": ((1, -1, 1), (1, 1, 0)),
}


def views(size=(100.0, 40.0, 20.0)):
    x, y, z = size
    extents = {"Front": (x, z), "Rear": (x, z), "Top": (x, y), "Bottom": (x, y), "Left": (y, z), "Right": (y, z), "FrontTopLeft": (x, y)}
    out = {"center": [x / 2, y / 2, z / 2]}
    for name, (w, h) in extents.items():
        out[name] = {"direction": DIRECTIONS[name][0], "x_direction": DIRECTIONS[name][1], "bbox2d": [-w / 2, -h / 2, w / 2, h / 2]}
    return out


def dim(feature, kind, view, p, axis=-1, **extra):
    return {"feature": feature, "kind": kind, "value": 1.0, "view": view, "p1": p, "p2": p, "count": 1, "axis": axis, "label": "", "covers": [], **extra}


def test_a_crowded_view_hands_dimensions_to_its_mirror_view():
    records = [dim(f"hole {i}", "diameter", "Front", (10 + i * 8, 20, 10)) for i in range(12)]
    used = {d["view"] for d in sl.assign(records, views(), 1)}
    assert used == {"Front", "Rear"}


def test_three_close_features_become_a_detail_view_at_a_larger_scale():
    records = [dim(f"radius {i}", "radius", "Front", (50 + i / 4, 20, 10)) for i in range(8)]
    (scale, placed, assigned), *_ = sl.plans(records, views())
    assert {d["view"] for d in assigned} >= {"Detail B"}
    assert placed["Detail B"]["scale"] == scale * placed["Detail B"]["factor"] and placed["Detail B"]["factor"] in (2, 5, 10)


def test_the_largest_scale_that_fits_is_taken_and_every_view_is_inside_the_drawing_area():
    records = [dim("envelope", "envelope", "Front", (0, 0, 0), 0)]
    scale, placed, _ = next(sl.plans(records, views()))
    assert scale == 5
    x0, y0, x1, y1 = sl.AREA
    for spec in placed.values():
        w, h = spec["size"]
        cx, cy = spec["rect_centre"]
        assert x0 <= cx - w / 2 and cx + w / 2 <= x1 and y0 <= cy - h / 2 and cy + h / 2 <= y1
