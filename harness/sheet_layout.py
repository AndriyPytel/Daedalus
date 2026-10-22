"""
Chooses the views of a sheet and where they go. Dimension groups are assigned to the view that sees
them face-on and carries the fewest dimensions per 100 mm^2 of drawn view at the sheet scale; Right,
Bottom and Rear join only when they lower the highest such density. A cluster of three or more
dimensions inside a disc of 4 text heights becomes a DETAIL view at 2:1, 5:1 or 10:1 of the sheet
scale. The views, with room for their dimensions, are packed in rows on A2's drawing area, and the
largest ISO 5455 scale at which they fit wins. Notes sit bottom-right above the title block.
"""
import math

import numpy as np

SCALES = [50, 20, 10, 5, 2, 1, 1 / 2, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100]
AREA = (22.0, 12.0, 582.0, 356.0)
NOTE_WIDTH = 176.0
NOTE_LINE = 5.0
NOTE_CHARS = 80
TEXT_MM = 3.5
BASE = ("Front", "Top", "Left")
EXTRA = ("Right", "Bottom", "Rear")
OPPOSITE = {"Front": "Rear", "Rear": "Front", "Top": "Bottom", "Bottom": "Top", "Left": "Right", "Right": "Left"}
SHEET_AXES = {"Front": (0, 2), "Rear": (0, 2), "Top": (0, 1), "Bottom": (0, 1), "Left": (1, 2), "Right": (1, 2)}
ISO_SIZES = [150.0, 130.0, 110.0, 90.0, 70.0, 50.0]
DETAIL_FACTORS = [10, 5, 2]
DETAIL_DISC = 4 * TEXT_MM
DETAIL_MIN = 3
DETAIL_MAX_SIZE = 60.0
MAX_DETAILS = 4
LETTERS = "BCDEFG"
PAD_BASE, PAD_PER_DIM, PAD_MAX = 9.0, 2.5, 50.0
SMALL_PAD = 8.0
TITLE = 8.0


def project(point, view, center):
    direction = np.array(view["direction"], dtype=float)
    x_direction = np.array(view["x_direction"], dtype=float)
    y_direction = np.cross(direction, x_direction)
    offset = np.array(point, dtype=float) - np.array(center, dtype=float)
    return np.array([offset @ x_direction, -(offset @ y_direction)])


def extent(view):
    x0, y0, x1, y1 = view["bbox2d"]
    return x1 - x0, y1 - y0


def local_centre(view):
    x0, y0, x1, y1 = view["bbox2d"]
    return np.array([(x0 + x1) / 2, -(y0 + y1) / 2])


def scale_label(scale):
    return f"{scale:g}:1" if scale >= 1 else f"1:{round(1 / scale)}"


def notes_of(records):
    lines = []
    for d in records:
        if d["kind"] == "note":
            words, line = d["label"].split(" "), ""
            for word in words:
                if line and len(line) + 1 + len(word) > NOTE_CHARS:
                    lines.append(line)
                    line = ""
                line = f"{line} {word}".strip()
            lines.append(line)
    return lines


def note_rect(lines):
    if not lines:
        return None
    return (AREA[2] - NOTE_WIDTH, AREA[3] - NOTE_LINE * len(lines) - 3, AREA[2], AREA[3])


CHAIN_RUN = 8


def _units(records):
    """Dimensions that must stay together: runs of at most CHAIN_RUN consecutive level dims of one axis, the pair distance with its centre line, and every other dimension alone."""
    units, chains = {}, {}
    for i, d in enumerate(records):
        if d["kind"] == "note":
            continue
        if d["kind"] == "symmetry" or d["feature"].startswith("pair"):
            units.setdefault(("chain", d["axis"], "pair"), []).append(i)
        elif d["kind"] == "linear" and d["feature"].startswith("level"):
            chains.setdefault(d["axis"], []).append(i)
        else:
            units[("envelope" if d["kind"] == "envelope" else "one", i)] = [i]
    for axis, members in chains.items():
        members.sort(key=lambda i: records[i]["p1"][axis])
        for start in range(0, len(members), CHAIN_RUN):
            units[("chain", axis, start)] = members[start:start + CHAIN_RUN]
    return units


def _allowed(key, records, members, available):
    if key[0] == "chain":
        return [v for v in available if key[1] in SHEET_AXES[v]]
    view = records[members[0]]["view"]
    if key[0] == "envelope":
        return [view]
    return [v for v in (view, OPPOSITE[view]) if v in available]


def _area(name, views, scale):
    w, h = extent(views[name])
    return max(w * scale * h * scale, 400.0) / 100.0


def assign(records, views, scale):
    """Copies of `records` with each dimension's view set, and the views used."""
    units = _units(records)
    order = sorted(units, key=lambda k: (k[0] != "envelope", -len(units[k])))

    def run(available):
        count = {v: 0 for v in available}
        views_of = {}
        for key in order:
            members = units[key]
            options = _allowed(key, records, members, available)
            best = min(options, key=lambda v: (count[v] + len(members)) / _area(v, views, scale))
            count[best] += len(members)
            views_of[key] = best
        return max(count[v] / _area(v, views, scale) for v in count if count[v]), views_of

    available = [v for v in BASE]
    density, chosen = run(available)
    while True:
        trials = [(run(available + [e]), e) for e in EXTRA if e in views and e not in available]
        better = [(r, e) for r, e in trials if r[0] < density - 1e-9]
        if not better:
            break
        (density, chosen), extra = min(better, key=lambda t: t[0][0])
        available.append(extra)
    out = [dict(d) for d in records]
    for key, members in units.items():
        for i in members:
            out[i]["view"] = chosen[key]
    return out


def _anchors(d, views, scale):
    view = views[d["view"]]
    centre = views["center"]
    points = [d["p1"]] if d["p1"] == d["p2"] else [d["p1"], d["p2"]]
    return [scale * project(p, view, centre) for p in points]


def _details(records, views, scale):
    """Clusters of 3+ dimensions inside a disc of DETAIL_DISC: (view, letter, centre, radius, factor, member indices)."""
    taken, found = set(), []
    for letter in LETTERS[:MAX_DETAILS]:
        best = None
        for name in {d["view"] for d in records if d["kind"] not in ("note", "symmetry")}:
            members = [i for i, d in enumerate(records) if d["view"] == name and d["kind"] not in ("note", "symmetry", "envelope") and i not in taken]
            anchors = {i: _anchors(records[i], views, scale) for i in members}
            for i in members:
                for centre in anchors[i]:
                    inside = [j for j in members if all(np.linalg.norm(a - centre) <= DETAIL_DISC / 2 for a in anchors[j])]
                    if len(inside) >= DETAIL_MIN and (best is None or len(inside) > len(best[1])):
                        best = (name, inside, centre)
        if best is None:
            break
        name, inside, centre = best
        points = np.array([a for j in inside for a in _anchors(records[j], views, scale)])
        centre = points.mean(axis=0)
        radius = max(float(np.linalg.norm(points - centre, axis=1).max()) + 1.5, 3.0)
        factor = next((f for f in DETAIL_FACTORS if 2 * radius * f <= DETAIL_MAX_SIZE), DETAIL_FACTORS[-1])
        found.append((name, letter, centre / scale, radius / scale, factor, inside))
        taken |= set(inside)
    return found


def _pad(count):
    return min(PAD_MAX, PAD_BASE + PAD_PER_DIM * count)


def _pack(items, note):
    """Rows of (name, w, h) in the drawing area, or None. Returns {name: centre} of each item's box."""
    x0, y0, x1, y1 = AREA
    width, height = x1 - x0, y1 - y0
    note_top = note[1] if note else y1
    for order in (
        sorted(items, key=lambda i: -i[2]), sorted(items, key=lambda i: -i[1]), list(items),
    ):
        shelves = []
        for item in order:
            if shelves and sum(i[1] for i in shelves[-1]) + item[1] <= width:
                shelves[-1].append(item)
            else:
                shelves.append([item])
        shelves.sort(key=lambda shelf: -sum(i[1] for i in shelf))
        heights = [max(i[2] for i in shelf) for shelf in shelves]
        if sum(heights) > height:
            continue
        gap = (height - sum(heights)) / (len(shelves) + 1)
        y, positions, ok = y0 + gap, {}, True
        for shelf, h in zip(shelves, heights):
            usable = width - (NOTE_WIDTH if y + h > note_top else 0)
            used = sum(i[1] for i in shelf)
            if used > usable:
                ok = False
                break
            slack = (usable - used) / (len(shelf) + 1)
            x = x0 + slack
            for name, w, _ in shelf:
                positions[name] = np.array([x + w / 2, y + h / 2])
                x += w + slack
            y += h + gap
        if ok:
            return positions
    return None


def _place(records, views, scale, details, iso_scale, note):
    count = {}
    for d in records:
        if d["kind"] not in ("note", "symmetry"):
            count[d["view"]] = count.get(d["view"], 0) + 1
    items, spec = [], {}
    parents = {detail[0] for detail in details}
    names = [n for n in ("Front", "Top", "Left", "Right", "Bottom", "Rear") if count.get(n) or n in parents]
    for name in names:
        w, h = (scale * e for e in extent(views[name]))
        pad = _pad(count.get(name, 0))
        spec[name] = {"base": name, "scale": scale, "shift": local_centre(views[name]), "size": (w, h)}
        items.append((name, w + 2 * pad, h + 2 * pad))
    if "Section" in views:
        w, h = (scale * e for e in extent(views["Section"]))
        spec["Section"] = {"base": "Section", "scale": scale, "shift": local_centre(views["Section"]), "size": (w, h)}
        items.append(("Section", w + 2 * SMALL_PAD, h + 2 * SMALL_PAD + TITLE))
    for name, letter, centre, radius, factor, members in details:
        key = f"Detail {letter}"
        side = 2 * radius * scale * factor
        spec[key] = {"base": name, "scale": scale * factor, "shift": centre, "size": (side, side), "radius": radius, "letter": letter, "factor": factor}
        items.append((key, side + 2 * _pad(len(members)), side + 2 * _pad(len(members)) + TITLE))
    iso = views["FrontTopLeft"]
    s_iso = iso_scale
    w, h = (s_iso * e for e in extent(iso))
    spec["FrontTopLeft"] = {"base": "FrontTopLeft", "scale": s_iso, "shift": local_centre(iso), "size": (w, h)}
    items.append(("FrontTopLeft", w + 2 * SMALL_PAD, h + 2 * SMALL_PAD))
    positions = _pack(items, note)
    if positions is None:
        return None
    for name, record in spec.items():
        titled = name == "Section" or name.startswith("Detail")
        record["rect_centre"] = positions[name] - np.array([0.0, TITLE / 2 if titled else 0.0])
        record["origin"] = record["rect_centre"] - record["scale"] * record["shift"]
    return spec


def plans(records, views):
    """(scale, placed views, records with views and detail owners) for each fitting ISO scale, largest first."""
    note = note_rect(notes_of(records))
    iso_extent = max(extent(views["FrontTopLeft"]))
    for scale in SCALES:
        assigned = assign(records, views, scale)
        details = _details(assigned, views, scale)
        for name, letter, centre, radius, factor, members in details:
            for j in members:
                assigned[j]["view"] = f"Detail {letter}"
        for size in ISO_SIZES:
            iso_scale = min(scale, size / iso_extent)
            spec = _place(assigned, views, scale, details, iso_scale, note)
            if spec:
                yield scale, spec, assigned
                break
