"""
Builds the BIM benchmark in test-data/bim/{create,edit}/<task>/ from one single-storey apartment of Modified Swiss Dwellings
(CC BY-SA 4.0, read from the public Hugging Face mirror MIRROR) per task: a spec in metres built into an Arch document and an
IFC4 file in `freecadcmd`, a floor-plan PNG for create, one edit for edit. Fetch MIRROR into ~/.cache/msd with curl -L, then run
`candidates` with `uv run --with pyarrow python` and `build` with `harness/.venv/bin/python`.
"""
import argparse
import json
import math
import random
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import bim_score
import make_sheets

OUT = Path(__file__).resolve().parents[1] / "test-data" / "bim"
CACHE = Path.home() / ".cache" / "msd"
MIRROR = "https://huggingface.co/datasets/philippds/modified-swiss-dwellings-enriched/resolve/main/geometries/test-00000-of-00001.parquet"
MODES = ("create", "edit")
TASKS_PER_MODE = 20
SEED = 0
LICENCE = "CC BY-SA 4.0"
ROOM_TYPES = {"BATHROOM", "BEDROOM", "CORRIDOR", "DINING", "KITCHEN", "KITCHEN_DINING", "LIVING_DINING", "LIVING_ROOM", "ROOM", "STOREROOM"}
MIN_WALL = 0.25
MAX_WALL_THICKNESS = 0.6
HOST_REACH = 0.5
MIN_ROOM_AREA = 1.0
MOVE = 0.3
WIDEN = 0.3
NEW_WALL = 0.1
NEW_DOOR = (0.9, 2.1)
SPLITTABLE = 14.0
FACE_TOLERANCE = 0.03
EDIT_ORDER = ("add_door", "remove_window", "resize_window", "move_wall", "split_room")
PNG_DPI = 100


def parse_wkt(text):
    """The outer ring of a WKT POLYGON as [x, y] pairs, without the closing point."""
    ring = re.search(r"\(\(([^()]*)\)", text).group(1)
    points = [[float(v) for v in pair.split()[:2]] for pair in ring.split(",")]
    return points[:-1]


def _hull(points):
    points = sorted(set(map(tuple, points)))

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(p)
        return out

    return half(points)[:-1] + half(reversed(points))[:-1]


def oriented_rect(points):
    """The smallest rectangle around the points: centre c, length len, thickness thk, angle ang in [0, pi) of the length axis."""
    hull = _hull(points)
    best = None
    for a, b in zip(hull, hull[1:] + hull[:1]):
        norm = math.dist(a, b)
        if norm == 0:
            continue
        u = ((b[0] - a[0]) / norm, (b[1] - a[1]) / norm)
        v = (-u[1], u[0])
        s = [p[0] * u[0] + p[1] * u[1] for p in hull]
        t = [p[0] * v[0] + p[1] * v[1] for p in hull]
        size = (max(s) - min(s), max(t) - min(t))
        if best is None or size[0] * size[1] < best[0]:
            best = (size[0] * size[1], size, u, v, (max(s) + min(s)) / 2, (max(t) + min(t)) / 2)
    _, (one, other), u, v, s0, t0 = best
    centre = [s0 * u[0] + t0 * v[0], s0 * u[1] + t0 * v[1]]
    axis = u if one >= other else v
    return {"c": centre, "len": max(one, other), "thk": min(one, other), "ang": math.atan2(axis[1], axis[0]) % math.pi}


def _rotated(points, angle):
    c, s = math.cos(angle), math.sin(angle)
    return [[x * c - y * s, x * s + y * c] for x, y in points]


def _along(rect, point):
    u = (math.cos(rect["ang"]), math.sin(rect["ang"]))
    dx, dy = point[0] - rect["c"][0], point[1] - rect["c"][1]
    return dx * u[0] + dy * u[1], -dx * u[1] + dy * u[0]


def _reach(rect, point):
    along, across = _along(rect, point)
    return math.hypot(max(0.0, abs(along) - rect["len"] / 2), max(0.0, abs(across) - rect["thk"] / 2))


def _area(poly):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def _clean_ring(poly, gap=0.02):
    """The ring without vertices closer than gap to the one kept before them."""
    kept = []
    for p in poly:
        if not kept or math.dist(p, kept[-1]) >= gap:
            kept.append(p)
    while len(kept) > 1 and math.dist(kept[0], kept[-1]) < gap:
        kept.pop()
    return kept


def _simple(poly):
    """True when no two non-adjacent edges of the ring cross."""
    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    edges = list(zip(poly, poly[1:] + poly[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue
            c, d = edges[j]
            if turn(a, b, c) * turn(a, b, d) < 0 and turn(c, d, a) * turn(c, d, b) < 0:
                return False
    return True


def _inside(poly, point):
    x, y = point
    hit = False
    for a, b in zip(poly, poly[1:] + poly[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            hit = not hit
    return hit


def spec_from_rows(rows):
    """The spec of one apartment from its MSD geometry rows, turned so its longest wall is horizontal and moved to the origin; None when it has no wall or room."""
    shapes = [(r["entity_type"], r["entity_subtype"], parse_wkt(r["geom"]), r["elevation"], r["height"]) for r in rows]
    walls = [oriented_rect(p) for t, s, p, _, _ in shapes if s == "WALL"]
    walls = [w for w in walls if w["len"] >= MIN_WALL and w["thk"] <= MAX_WALL_THICKNESS]
    areas = [(s, p, e) for t, s, p, e, _ in shapes if t == "area"]
    if not walls or not areas:
        return None
    turn = -max(walls, key=lambda w: w["len"])["ang"]
    shapes = [(t, s, _rotated(p, turn), e, h) for t, s, p, e, h in shapes]
    areas = [(s, p, e) for t, s, p, e, _ in shapes if t == "area"]
    floor = min(e for t, s, _, e, _ in shapes if t == "area")
    heights = [h for t, s, _, _, h in shapes if s == "WALL"]
    storey = Counter(round(h, 2) for h in heights).most_common(1)[0][0]
    walls = [oriented_rect(p) for t, s, p, _, _ in shapes if s == "WALL"]
    walls = [w for w in walls if w["len"] >= MIN_WALL and w["thk"] <= MAX_WALL_THICKNESS]
    left = min(p[0] for _, _, poly, _, _ in shapes for p in poly)
    bottom = min(p[1] for _, _, poly, _, _ in shapes for p in poly)
    walls.sort(key=lambda w: (round(w["c"][0], 2), round(w["c"][1], 2)))
    for number, w in enumerate(walls, 1):
        w.update(id=f"W{number}", h=storey)
    doors, windows = [], []
    for t, s, p, e, h in shapes:
        if t != "opening":
            continue
        rect = oriented_rect(p)
        host = min(walls, key=lambda w: (_reach(w, rect["c"]), w["id"]))
        if _reach(host, rect["c"]) > HOST_REACH:
            continue
        along, _ = _along(host, rect["c"])
        u = (math.cos(host["ang"]), math.sin(host["ang"]))
        item = {
            "c": [host["c"][0] + along * u[0], host["c"][1] + along * u[1]], "w": min(rect["len"], host["len"]), "h": h,
            "sill": max(0.0, e - floor), "ang": host["ang"], "host": host["id"],
        }
        (windows if s == "WINDOW" else doors).append((item, s))
    spaces = [
        {"name": s, "poly": p} for s, p, _ in areas if s in ROOM_TYPES and _area(p) >= MIN_ROOM_AREA
    ]
    spaces.sort(key=lambda sp: (round(sum(x for x, _ in sp["poly"]) / len(sp["poly"]), 2), round(sum(y for _, y in sp["poly"]) / len(sp["poly"]), 2)))

    def moved(point):
        return [round(point[0] - left, 3), round(point[1] - bottom, 3)]

    for w in walls:
        w["c"], w["len"], w["thk"], w["ang"] = moved(w["c"]), round(w["len"], 3), round(w["thk"], 3), round(w["ang"], 5)
    out = {"storey_height": storey, "walls": walls, "doors": [], "windows": [], "spaces": []}
    for key, items, prefix in (("doors", doors, "D"), ("windows", windows, "WIN")):
        items.sort(key=lambda it: (round(it[0]["c"][0], 2), round(it[0]["c"][1], 2)))
        for number, (item, subtype) in enumerate(items, 1):
            item.update(id=f"{prefix}{number}", c=moved(item["c"]), w=round(item["w"], 3), h=round(item["h"], 3), sill=round(item["sill"], 3), ang=round(item["ang"], 5))
            if key == "doors":
                item["kind"] = "ENTRANCE_DOOR" if subtype == "ENTRANCE_DOOR" else "DOOR"
            out[key].append(item)
    for number, sp in enumerate(spaces, 1):
        out["spaces"].append({"id": f"S{number}", "name": sp["name"], "poly": _clean_ring([moved(p) for p in sp["poly"]])})
    return out


def _next_id(items, prefix):
    numbers = [int(i["id"][len(prefix):]) for i in items if i["id"].startswith(prefix) and i["id"][len(prefix):].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1}"


def _copy(spec):
    return json.loads(json.dumps(spec))


def _unit(wall):
    return math.cos(wall["ang"]), math.sin(wall["ang"])


def _sides(spec, wall):
    """The spaces on the two faces of the wall at its middle, as (space, space), either may be None."""
    ux, uy = _unit(wall)
    offset = wall["thk"] / 2 + 0.15
    found = []
    for sign in (1, -1):
        point = (wall["c"][0] - sign * uy * offset, wall["c"][1] + sign * ux * offset)
        found.append(next((s for s in spec["spaces"] if _inside(s["poly"], point)), None))
    return found[0], found[1]


def _interior(spec):
    return [w for w in sorted(spec["walls"], key=lambda w: (-w["len"], w["id"])) if None not in _sides(spec, w) and _sides(spec, w)[0] is not _sides(spec, w)[1]]


def _openings_of(spec, wall):
    return [o for o in spec["doors"] + spec["windows"] if o["host"] == wall["id"]]


def _free_offset(spec, wall, width):
    """The offset along the wall, nearest its middle, where a door of this width clears every opening and both ends by 0.2 m, or None."""
    reach = wall["len"] / 2 - width / 2 - 0.2
    if reach < 0:
        return None
    steps = [0.0]
    for i in range(1, int(reach / 0.05) + 1):
        steps += [i * 0.05, -i * 0.05]
    taken = [(_along(wall, o["c"])[0], o["w"]) for o in _openings_of(spec, wall)]
    return next((s for s in steps if all(abs(s - at) >= width / 2 + w / 2 + 0.2 for at, w in taken)), None)


def edit_add_door(spec):
    """(after, text) adding a door to the interior wall with the fewest openings (longest first) where one fits, or None."""
    for wall in sorted(_interior(spec), key=lambda w: (len(_openings_of(spec, w)), -w["len"], w["id"])):
        offset = _free_offset(spec, wall, NEW_DOOR[0])
        if offset is None or wall["len"] < 1.2:
            continue
        ux, uy = _unit(wall)
        centre = [round(wall["c"][0] + offset * ux, 3), round(wall["c"][1] + offset * uy, 3)]
        after = _copy(spec)
        door = {"id": _next_id(spec["doors"], "D"), "c": centre, "w": NEW_DOOR[0], "h": NEW_DOOR[1], "sill": 0.0, "ang": wall["ang"], "host": wall["id"], "kind": "DOOR"}
        after["doors"].append(door)
        one, other = _sides(spec, wall)
        text = (
            f"Add a door {door['w']:.2f} m wide and {door['h']:.2f} m high with its sill on the floor as Door_{door['id']} in Wall_{wall['id']}, "
            f"the wall between Space_{one['id']} ({one['name']}) and Space_{other['id']} ({other['name']}), centred at ({centre[0]:.2f}, {centre[1]:.2f}) m."
        )
        return after, text
    return None


def edit_remove_window(spec):
    """(after, text) removing the widest window, or None."""
    if not spec["windows"]:
        return None
    window = max(spec["windows"], key=lambda o: (o["w"], o["id"]))
    after = _copy(spec)
    after["windows"] = [o for o in after["windows"] if o["id"] != window["id"]]
    return after, f"Remove Window_{window['id']}, the {window['w']:.2f} m wide window in Wall_{window['host']}, and close its opening in the wall."


def _fits(spec, window):
    wall = next(w for w in spec["walls"] if w["id"] == window["host"])
    along, _ = _along(wall, window["c"])
    half = (window["w"] + WIDEN) / 2
    if abs(along) + half > wall["len"] / 2 - 0.1:
        return False
    for o in _openings_of(spec, wall):
        if o["id"] != window["id"] and abs(_along(wall, o["c"])[0] - along) < half + o["w"] / 2 + 0.1:
            return False
    return True


def edit_resize_window(spec):
    """(after, text) widening the first window that has room for it about its centre, or None."""
    window = next((o for o in sorted(spec["windows"], key=lambda o: o["id"]) if _fits(spec, o)), None)
    if window is None:
        return None
    after = _copy(spec)
    next(o for o in after["windows"] if o["id"] == window["id"])["w"] = round(window["w"] + WIDEN, 3)
    text = f"Change the width of Window_{window['id']} in Wall_{window['host']} from {window['w']:.2f} m to {window['w'] + WIDEN:.2f} m, keeping its centre, height and sill."
    return after, text


def _face_vertices(poly, wall):
    """Indices of the polygon vertices that lie on the wall, within its length and a little more than its half thickness across."""
    return [
        i for i, p in enumerate(poly)
        if abs(_along(wall, p)[1]) <= wall["thk"] / 2 + 0.08 and abs(_along(wall, p)[0]) <= wall["len"] / 2 + 0.1
    ]


def edit_move_wall(spec):
    """(after, text) moving an interior wall of at least 1.5 m by 0.30 m towards its larger neighbour with its doors, and the two rooms' vertices on it; walls that meet it are left as they are; or None."""
    for wall in _interior(spec):
        if any(o in spec["windows"] for o in _openings_of(spec, wall)):
            continue
        plus, minus = _sides(spec, wall)
        if wall["len"] < 1.5 or len(_face_vertices(plus["poly"], wall)) < 2 or len(_face_vertices(minus["poly"], wall)) < 2:
            continue
        sign = 1 if _area(plus["poly"]) >= _area(minus["poly"]) else -1
        shrinking = plus if sign == 1 else minus
        if _area(shrinking["poly"]) - MOVE * wall["len"] < 4.0:
            continue
        ux, uy = _unit(wall)
        shift = (-uy * MOVE * sign, ux * MOVE * sign)
        after = _copy(spec)
        target = next(w for w in after["walls"] if w["id"] == wall["id"])
        target["c"] = [round(wall["c"][0] + shift[0], 3), round(wall["c"][1] + shift[1], 3)]
        for o in _openings_of(after, wall):
            o["c"] = [round(o["c"][0] + shift[0], 3), round(o["c"][1] + shift[1], 3)]
        for room in (plus, minus):
            moved = next(s for s in after["spaces"] if s["id"] == room["id"])
            for i in _face_vertices(room["poly"], wall):
                moved["poly"][i] = [round(room["poly"][i][0] + shift[0], 3), round(room["poly"][i][1] + shift[1], 3)]
        grown = minus if sign == 1 else plus
        text = (
            f"Move Wall_{wall['id']} by {MOVE:.2f} m perpendicular to its length, towards the ({shift[0]:+.2f}, {shift[1]:+.2f}) side, "
            f"and carry its doors with it. Shrink Space_{shrinking['id']} ({shrinking['name']}) and enlarge Space_{grown['id']} ({grown['name']}) "
            f"so that both still meet the faces of the wall."
        )
        return after, text
    return None


def _convex(poly):
    signs = {
        (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) > 0
        for a, b, c in zip(poly, poly[1:] + poly[:1], poly[2:] + poly[:2])
    }
    return len(signs) == 1


def _clip(poly, point, normal):
    """The part of the polygon where (p - point) . normal >= 0."""
    out = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        da = (a[0] - point[0]) * normal[0] + (a[1] - point[1]) * normal[1]
        db = (b[0] - point[0]) * normal[0] + (b[1] - point[1]) * normal[1]
        if da >= 0:
            out.append(a)
        if (da >= 0) != (db >= 0):
            t = da / (da - db)
            out.append([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])])
    return out


def edit_split_room(spec):
    """(after, text) splitting the largest convex room of at least 14 m2 across its long side by a 0.10 m wall, or None."""
    room = next((s for s in sorted(spec["spaces"], key=lambda s: (-_area(s["poly"]), s["id"])) if _area(s["poly"]) >= SPLITTABLE and _convex(s["poly"])), None)
    if room is None:
        return None
    rect = oriented_rect(room["poly"])
    u = (math.cos(rect["ang"]), math.sin(rect["ang"]))
    centre = rect["c"]
    cut = (-u[1], u[0])
    crossings = []
    for p, q in zip(room["poly"], room["poly"][1:] + room["poly"][:1]):
        sp = (p[0] - centre[0]) * u[0] + (p[1] - centre[1]) * u[1]
        sq = (q[0] - centre[0]) * u[0] + (q[1] - centre[1]) * u[1]
        if (sp < 0) != (sq < 0):
            t = sp / (sp - sq)
            crossings.append([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])])
    crossings.sort(key=lambda p: (p[0] - centre[0]) * cut[0] + (p[1] - centre[1]) * cut[1])
    a, b = crossings[0], crossings[-1]
    half = NEW_WALL / 2
    near = _clip(room["poly"], (centre[0] - u[0] * half, centre[1] - u[1] * half), (-u[0], -u[1]))
    far = _clip(room["poly"], (centre[0] + u[0] * half, centre[1] + u[1] * half), u)
    after = _copy(spec)
    wall_id = _next_id(spec["walls"], "W")
    after["walls"].append({
        "id": wall_id, "c": [round(centre[0], 3), round(centre[1], 3)], "len": round(math.dist(a, b), 3), "thk": NEW_WALL,
        "ang": round(math.atan2(cut[1], cut[0]) % math.pi, 5), "h": spec["storey_height"],
    })
    target = next(s for s in after["spaces"] if s["id"] == room["id"])
    target["poly"] = [[round(x, 3), round(y, 3)] for x, y in near]
    new_id = f"{room['id']}_2"
    after["spaces"].append({"id": new_id, "name": room["name"], "poly": [[round(x, 3), round(y, 3)] for x, y in far]})
    text = (
        f"Split Space_{room['id']} ({room['name']}) in two with a new interior wall {NEW_WALL:.2f} m thick, Wall_{wall_id}, running from "
        f"({a[0]:.2f}, {a[1]:.2f}) to ({b[0]:.2f}, {b[1]:.2f}) m across the room. Keep Space_{room['id']} for one part, add Space_{new_id} "
        f"for the other, each ending at a face of the new wall."
    )
    return after, text


EDIT_FUNCTIONS = {
    "add_door": edit_add_door, "remove_window": edit_remove_window, "resize_window": edit_resize_window,
    "move_wall": edit_move_wall, "split_room": edit_split_room,
}


def render_plan(spec, path):
    """The floor plan as a PNG: walls in grey, doors orange, windows blue, rooms labelled with name and area, axes in metres."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot
    from matplotlib.patches import Polygon

    def box(item, depth):
        ux, uy = math.cos(item["ang"]), math.sin(item["ang"])
        length = item.get("len", item.get("w"))
        return [
            [item["c"][0] + a * ux - b * uy, item["c"][1] + a * uy + b * ux]
            for a, b in ((-length / 2, -depth / 2), (length / 2, -depth / 2), (length / 2, depth / 2), (-length / 2, depth / 2))
        ]

    figure, axes = pyplot.subplots(figsize=(9, 7))
    for space in spec["spaces"]:
        axes.add_patch(Polygon(space["poly"], closed=True, facecolor="#f2efe6", edgecolor="none"))
        xs, ys = [p[0] for p in space["poly"]], [p[1] for p in space["poly"]]
        axes.text(sum(xs) / len(xs), sum(ys) / len(ys), f"{space['name']}\n{_area(space['poly']):.1f} m²", ha="center", va="center", fontsize=7)
    for wall in spec["walls"]:
        axes.add_patch(Polygon(box(wall, wall["thk"]), closed=True, facecolor="#555555", edgecolor="none"))
    for key, colour in (("doors", "#e8892b"), ("windows", "#3b82c4")):
        for item in spec[key]:
            depth = next(w for w in spec["walls"] if w["id"] == item["host"])["thk"] * 1.05
            axes.add_patch(Polygon(box(item, depth), closed=True, facecolor=colour, edgecolor="none"))
    xs = [p[0] for w in spec["walls"] for p in box(w, w["thk"])]
    ys = [p[1] for w in spec["walls"] for p in box(w, w["thk"])]
    axes.set_xlim(min(xs) - 0.5, max(xs) + 0.5)
    axes.set_ylim(min(ys) - 0.5, max(ys) + 0.5)
    axes.set_aspect("equal")
    axes.grid(True, linewidth=0.3)
    axes.set_xlabel("x (m)")
    axes.set_ylabel("y (m)")
    figure.savefig(path, dpi=PNG_DPI)
    pyplot.close(figure)


def split(names):
    return make_sheets.split(names, make_sheets.test_count(len(names)), SEED)


def read_apartments(parquet):
    """MSD geometry rows grouped by residential apartment that sits on one floor; needs pyarrow."""
    import pyarrow.parquet

    columns = ["apartment_id", "floor_id", "unit_usage", "entity_type", "entity_subtype", "geom", "elevation", "height"]
    table = pyarrow.parquet.read_table(parquet, columns=columns).to_pydict()
    grouped = {}
    for index, apartment in enumerate(table["apartment_id"]):
        if table["unit_usage"][index] == "RESIDENTIAL":
            grouped.setdefault(apartment, []).append({k: table[k][index] for k in columns[1:]})
    return {a: rows for a, rows in grouped.items() if len({r["floor_id"] for r in rows}) == 1}


def usable(spec):
    """Rooms 3-8, walls 6-45, at least two doors and two windows, every room at least 3 m2 and at least four fifths of the walls square to the plan."""
    square = sum(min(w["ang"] % (math.pi / 2), math.pi / 2 - w["ang"] % (math.pi / 2)) < 0.03 for w in spec["walls"])
    return (
        3 <= len(spec["spaces"]) <= 8 and 6 <= len(spec["walls"]) <= 45 and len(spec["doors"]) >= 2 and len(spec["windows"]) >= 2
        and all(len(s["poly"]) >= 3 and _area(s["poly"]) >= 3.0 and _simple(s["poly"]) for s in spec["spaces"]) and square >= 0.8 * len(spec["walls"])
    )


def select_candidates(parquet, out, count=120):
    """Seeded shuffle of the usable apartments as {apartment id: spec}, written to out."""
    apartments = read_apartments(parquet)
    ids = sorted(apartments)
    random.Random(SEED).shuffle(ids)
    chosen = {}
    for apartment in ids:
        spec = spec_from_rows(apartments[apartment])
        if spec is not None and usable(spec):
            chosen[apartment] = spec
        if len(chosen) == count:
            break
    Path(out).write_text(json.dumps(chosen))


def builds_cleanly(candidates):
    """The candidates whose Arch model exports to an IFC with every wall, door, window and space present and every door and window hosted."""
    work = Path(tempfile.mkdtemp(prefix="bim-valid-"))
    bim_score.build([(spec, work / f"{n}.FCStd") for n, spec in enumerate(candidates.values())])
    measured = bim_score.measure([work / f"{n}.ifc" for n in range(len(candidates))])
    shutil.rmtree(work)
    keys = (("IfcWall", "walls"), ("IfcDoor", "doors"), ("IfcWindow", "windows"), ("IfcSpace", "spaces"))
    return {
        apartment: spec for (apartment, spec), m in zip(candidates.items(), measured)
        if m["opens"] and all(m["counts"][t] == len(spec[k]) for t, k in keys) and all(o["host"] is not None for o in m["openings"])
    }


def assign(candidates):
    """Distinct apartments per mode: the first TASKS_PER_MODE for create, then for edit the next apartment that can take each of the five edit kinds in turn."""
    items = list(candidates.items())
    create, pool, edit = items[:TASKS_PER_MODE], items[TASKS_PER_MODE:], []
    for number in range(TASKS_PER_MODE):
        kind = EDIT_ORDER[number % len(EDIT_ORDER)]
        found = next(i for i, (_, spec) in enumerate(pool) if EDIT_FUNCTIONS[kind](spec))
        apartment, spec = pool.pop(found)
        edit.append((apartment, spec, kind))
    return {"create": create, "edit": edit}


def _typical_thickness(spec):
    return sorted(w["thk"] for w in spec["walls"])[len(spec["walls"]) // 2]


def create_text(spec):
    return (
        "Model the flat on the attached floor plan (plan.png, axes in metres) as native FreeCAD Arch objects: Wall, Slab, Door, Window "
        f"and Space, each door and window hosted in its wall, and export the model as IFC4. Storey height {spec['storey_height']:.2f} m; "
        f"walls are about {_typical_thickness(spec):.2f} m thick; the slab is 0.20 m thick under the walls."
    )


LICENCES = """
## Source and licences

- MSD, Modified Swiss Dwellings (ECCV 2024, https://arxiv.org/abs/2407.10121; code https://github.com/caspervanengelenburg/msd): CC BY-SA 4.0 on the Kaggle page (https://www.kaggle.com/datasets/caspervanengelenburg/modified-swiss-dwellings), derived from the Swiss Dwellings dataset. Kaggle needs a login, so the geometry is read from the public Hugging Face mirror https://huggingface.co/datasets/philippds/modified-swiss-dwellings-enriched (`geometries` config, test split), which declares CC BY 4.0, not CC BY-SA. The mirror is not the original release, so the tasks are shipped under the stricter CC BY-SA 4.0 with attribution to the MSD and Swiss Dwellings authors. Task data stays in test-data/ and is never committed.
- ResPlan (arXiv 2508.14006): not used. The paper says permissive, the Kaggle page says CC BY-NC-SA 4.0; the conflict is settled by leaving it out.
- BIM-Edit (arXiv 2606.20146, dataset https://huggingface.co/datasets/BIM-Edit/BIM-Edit): the dataset card says CC BY 4.0 and the paper's appendix J says prompts, task metadata and the author-created IFC files are CC BY 4.0, the inference harness and evaluator MIT; the paper text itself is CC BY-SA 4.0. Our use as a yardstick is allowed with attribution. Not integrated here. To run it later: load each task's input .ifc into FreeCAD with `importIFC` (edit mode as in this benchmark), give the agent the task instruction, export IFC4, and score it with BIM-Edit's own evaluator against the task's `gt` IFC (geometric accuracy, semantic validity, topological consistency), reporting its numbers beside this scorer's.
"""


def write_credits(out, names):
    rows = ["| task | source | licence | MSD apartment |", "| --- | --- | --- | --- |"]
    for mode in MODES:
        for name in names[mode]:
            meta = json.loads((out / mode / name / "task.json").read_text())
            rows.append(f"| {name} | MSD | {meta['licence']} | {meta['source_id']} |")
    (out / "CREDITS.md").write_text("# Credits\n\n" + "\n".join(rows) + "\n" + LICENCES)


def _task(mode, name, apartment, text, inputs, spec):
    return {"mode": mode, "id": name, "source": "MSD", "source_id": apartment, "licence": LICENCE, "text": text, "inputs": inputs, "reference_spec": spec}


def write_tasks(chosen, out=OUT):
    if out.exists():
        shutil.rmtree(out)
    names = {mode: [] for mode in MODES}
    jobs = []
    for number, (apartment, spec) in enumerate(chosen["create"], 1):
        name = f"create_{number:02d}"
        directory = out / "create" / name
        directory.mkdir(parents=True)
        render_plan(spec, directory / "plan.png")
        (directory / "task.json").write_text(json.dumps(_task("create", name, apartment, create_text(spec), {"plan": "plan.png"}, spec), indent=1))
        jobs.append((spec, directory / "reference.FCStd"))
        names["create"].append(name)
    for number, (apartment, spec, kind) in enumerate(chosen["edit"], 1):
        name = f"edit_{number:02d}"
        directory = out / "edit" / name
        directory.mkdir(parents=True)
        after, text = EDIT_FUNCTIONS[kind](spec)
        meta = _task("edit", name, apartment, text, {"input": "input.FCStd", "input_ifc": "input.ifc", "edit": kind}, after)
        (directory / "task.json").write_text(json.dumps(meta, indent=1))
        jobs += [(spec, directory / "input.FCStd"), (after, directory / "reference.FCStd")]
        names["edit"].append(name)
    bim_score.build(jobs)
    splits = {}
    for mode, listed in names.items():
        test, validation = split(listed)
        splits[mode] = {"test": test, "validation": validation}
    (out / "split.json").write_text(json.dumps({"seed": SEED, **splits}, indent=1))
    write_credits(out, names)
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["candidates", "build"])
    parser.add_argument("--parquet", default=str(CACHE / MIRROR.rsplit("/", 1)[1]))
    args = parser.parse_args()
    if args.command == "candidates":
        select_candidates(args.parquet, CACHE / "candidates.json")
        return
    chosen = assign(builds_cleanly(json.loads((CACHE / "candidates.json").read_text())))
    names = write_tasks(chosen)
    print({mode: len(items) for mode, items in names.items()}, Counter(k for _, _, k in chosen["edit"]))


if __name__ == "__main__":
    main()
