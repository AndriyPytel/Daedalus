
import math
import struct

import bim_fixtures as fx
import bim_tasks
import make_sheets


def polygon_wkt(points):
    ring = points + [points[0]]
    return "POLYGON ((" + ", ".join(f"{x:.9f} {y:.9f}" for x, y in ring) + "))"


def rectangle(cx, cy, length, thickness, angle):
    ux, uy = math.cos(angle), math.sin(angle)
    corners = [(-length / 2, -thickness / 2), (length / 2, -thickness / 2), (length / 2, thickness / 2), (-length / 2, thickness / 2)]
    return [[cx + a * ux - b * uy, cy + a * uy + b * ux] for a, b in corners]


def turned(points, angle, shift):
    c, s = math.cos(angle), math.sin(angle)
    return [[x * c - y * s + shift[0], x * s + y * c + shift[1]] for x, y in points]


def rows_of(spec, angle=0.5, shift=(120.0, -40.0), floor=2.9):
    rows = []
    for w in spec["walls"]:
        rows.append(("separator", "WALL", rectangle(*w["c"], w["len"], w["thk"], w["ang"]), floor, w["h"]))
    for kind, key in (("DOOR", "doors"), ("WINDOW", "windows")):
        for o in spec[key]:
            subtype = o.get("kind", kind)
            rows.append(("opening", subtype, rectangle(*o["c"], o["w"], 0.2, o["ang"]), floor + o["sill"], o["h"]))
    for s in spec["spaces"]:
        rows.append(("area", s["name"], s["poly"], floor, 2.6))
    return [
        {"entity_type": t, "entity_subtype": st, "geom": polygon_wkt(turned(pts, angle, shift)), "elevation": e, "height": h}
        for t, st, pts, e, h in rows
    ]


def area(poly):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def room_areas(spec):
    return sorted(round(area(s["poly"]), 3) for s in spec["spaces"])


def test_parse_wkt_returns_the_outer_ring_without_the_closing_point():
    ring = bim_tasks.parse_wkt("POLYGON ((0 0, 2 0, 2 1, 0 1, 0 0))")
    assert ring == [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]]


def test_oriented_rect_of_a_turned_rectangle_gives_its_length_thickness_and_centre():
    rect = bim_tasks.oriented_rect(rectangle(3.0, 2.0, 5.0, 0.2, 0.7))
    assert math.isclose(rect["len"], 5.0, abs_tol=1e-6) and math.isclose(rect["thk"], 0.2, abs_tol=1e-6)
    assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(rect["c"], [3.0, 2.0]))
    assert math.isclose(rect["ang"], 0.7, abs_tol=1e-6)


def test_spec_from_rows_recovers_counts_rooms_and_hosts_and_squares_the_plan():
    reference = fx.flat()
    spec = bim_tasks.spec_from_rows(rows_of(reference))
    assert [len(spec[k]) for k in ("walls", "doors", "windows", "spaces")] == [6, 2, 2, 3]
    assert all(math.isclose(a, b, abs_tol=0.01) for a, b in zip(room_areas(spec), room_areas(reference)))
    walls = {w["id"]: w for w in spec["walls"]}
    assert all(d["host"] in walls for d in spec["doors"] + spec["windows"])
    assert all(math.isclose(w["ang"] % (math.pi / 2), 0.0, abs_tol=1e-4) or math.isclose(w["ang"] % (math.pi / 2), math.pi / 2, abs_tol=1e-4) for w in spec["walls"])


def test_spec_from_rows_keeps_rooms_and_walls_in_the_same_turned_frame():
    spec = bim_tasks.spec_from_rows(rows_of(fx.flat()))
    xs = [p[0] for w in spec["walls"] for p in [(w["c"][0] - w["len"] / 2, 0), (w["c"][0] + w["len"] / 2, 0)]]
    for space in spec["spaces"]:
        assert min(xs) <= min(x for x, _ in space["poly"]) and max(x for x, _ in space["poly"]) <= max(xs)


def test_spec_from_rows_takes_sills_relative_to_the_floor_and_the_storey_height():
    spec = bim_tasks.spec_from_rows(rows_of(fx.flat(), floor=2.9))
    assert sorted(round(w["sill"], 3) for w in spec["windows"]) == [0.9, 0.9]
    assert spec["storey_height"] == 2.6


def test_spec_from_rows_drops_an_opening_that_touches_no_wall():
    rows = rows_of(fx.flat())
    stray = dict(rows[0], entity_type="opening", entity_subtype="WINDOW", geom=polygon_wkt(turned(rectangle(50.0, 50.0, 1.0, 0.2, 0.0), 0.5, (120.0, -40.0))))
    assert len(bim_tasks.spec_from_rows(rows + [stray])["windows"]) == 2


def test_spec_from_rows_leaves_out_shafts_and_outdoor_areas():
    rows = rows_of(fx.flat())
    extra = dict(rows[-1], entity_subtype="SHAFT")
    assert len(bim_tasks.spec_from_rows(rows + [extra])["spaces"]) == 3


def test_add_door_puts_one_new_hosted_door_in_an_interior_wall_without_an_opening():
    before = fx.flat()
    after, text = bim_tasks.edit_add_door(before)
    new = [d for d in after["doors"] if d["id"] not in {d["id"] for d in before["doors"]}]
    assert len(new) == 1 and new[0]["host"] == "W6"
    assert new[0]["id"] in text and new[0]["host"] in text
    assert len(before["doors"]) == 2


def test_remove_window_drops_exactly_the_named_window():
    before = fx.flat()
    after, text = bim_tasks.edit_remove_window(before)
    gone = {w["id"] for w in before["windows"]} - {w["id"] for w in after["windows"]}
    assert len(gone) == 1 and gone.pop() in text


def test_resize_window_widens_it_about_its_centre():
    before = fx.flat()
    after, text = bim_tasks.edit_resize_window(before)
    changed = [(b, a) for b, a in zip(before["windows"], after["windows"]) if b != a]
    assert len(changed) == 1
    old, new = changed[0]
    assert new["c"] == old["c"] and new["w"] > old["w"] and old["id"] in text


def test_move_wall_shifts_the_wall_and_resizes_the_two_rooms_it_separates():
    before = fx.flat()
    after, text = bim_tasks.edit_move_wall(before)
    old = next(w for w in before["walls"] if w["id"] == "W6")
    new = next(w for w in after["walls"] if w["id"] == "W6")
    shift = math.dist(old["c"], new["c"])
    assert shift > 0.1 and old["id"] in text
    changed = [(b, a) for b, a in zip(before["spaces"], after["spaces"]) if b != a]
    assert len(changed) == 2
    assert math.isclose(sum(area(a["poly"]) - area(b["poly"]) for b, a in changed), 0.0, abs_tol=1e-6)
    assert math.isclose(max(abs(area(a["poly"]) - area(b["poly"])) for b, a in changed), shift * 3.8, abs_tol=1e-6)


def test_split_room_adds_a_wall_and_replaces_one_room_by_two_that_fill_it():
    before = fx.flat()
    after, text = bim_tasks.edit_split_room(before)
    assert len(after["walls"]) == len(before["walls"]) + 1 and len(after["spaces"]) == len(before["spaces"]) + 1
    wall = after["walls"][-1]
    original = next(s for s in before["spaces"] if s["id"] == "A")
    parts = [s for s in after["spaces"] if s["id"].startswith("A")]
    assert len(parts) == 2 and "A" in text
    assert math.isclose(sum(area(p["poly"]) for p in parts), area(original["poly"]) - wall["thk"] * wall["len"], abs_tol=1e-6)


def test_edits_are_deterministic_and_report_none_when_nothing_qualifies():
    before = fx.flat()
    assert bim_tasks.edit_move_wall(before) == bim_tasks.edit_move_wall(before)
    empty = {**before, "walls": [], "doors": [], "windows": [], "spaces": []}
    for edit in bim_tasks.EDIT_FUNCTIONS.values():
        assert edit(empty) is None


def test_render_plan_writes_a_png(tmp_path):
    path = tmp_path / "plan.png"
    bim_tasks.render_plan(fx.flat(), path)
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", data[16:24])[0] > 200


def test_split_is_seeded_and_takes_the_test_share_of_the_tasks():
    names = [f"create_{i:02d}" for i in range(bim_tasks.TASKS_PER_MODE)]
    test, validation = bim_tasks.split(names)
    tests = make_sheets.test_count(bim_tasks.TASKS_PER_MODE)
    assert (len(test), len(validation)) == (tests, bim_tasks.TASKS_PER_MODE - tests)
    assert sorted(test + validation) == names
    assert bim_tasks.split(names) == (test, validation)


def test_modes_are_create_and_edit_only():
    assert bim_tasks.MODES == ("create", "edit")


def test_clean_ring_drops_spurs_and_simple_rejects_a_bow_tie():
    ring = [[0.0, 0.0], [0.001, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]]
    assert len(bim_tasks._clean_ring(ring)) == 4
    assert bim_tasks._simple(bim_tasks._clean_ring(ring))
    assert not bim_tasks._simple([[0, 0], [4, 3], [4, 0], [0, 3]])
