"""
Draws the views that sheet_layout chose, with the optional section view and detail views, on an A2
ISO 5457 frame, adds the dimensions, centre lines and notes in SVG and renders PNG at 300 dpi and PDF; cairocffi cannot find
Homebrew's libcairo on its own, so write_sheet points it there. Line weights are ISO 128 in sheet mm
whatever the scale: visible 0.5, hidden 0.35 dashed, dimension and extension 0.25. Every dimension
text is placed on the first candidate that touches no other text, line, outline or the frame, and
overlaps() lists the texts that still do.
"""
import math
import re

import numpy as np

import sheet_layout
from sheet_geometry import AREA, CHAR_WIDTH, KEEP_OUT, PAD, TEXT_MM, inside, rects_meet, segments_meet_rect, text_rect

ARROW_LENGTH = 3.0
ARROW_HALF_WIDTH = 0.6
FIRST_STACK = 8.0
STACK_STEP = 6.0
TIERS = 12
PASSES = 3
FRAME_SCORE = 900
CELL = 8.0
NEAR_TIERS = 3
LIFTS = [5.0 + 4.0 * i for i in range(11)]
FAN = [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6]
SLIDES = [0, 1, -1, 2, -2, 3, -3, 4, -4, 6, -6]
GAP = 1.0
OVERSHOOT = 2.0
VISIBLE_WIDTH = 0.5
HIDDEN_WIDTH = 0.35
DIM_WIDTH = 0.25
SCALE_FIELD = np.array([534.0, 371.2])
HOMEBREW_CAIRO = "/opt/homebrew/lib/libcairo.2.dylib"
ANGLES = [25 + 15 * i for i in range(24)]
REACHES = [8.0, 16.0, 28.0]
HATCH = '<pattern id="hatch" patternUnits="userSpaceOnUse" width="2.5" height="2.5" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="2.5" stroke="#000" stroke-width="0.25"/></pattern>'


def format_value(dim):
    text = f"{dim['value']:.2f}".rstrip("0").rstrip(".")
    prefix = {"diameter": "\u00d8", "radius": "R", "chamfer": "C"}.get(dim["kind"], "")
    suffix = "\u00b0" if dim["kind"] == "angle" else ""
    if dim.get("label"):
        return dim["label"]
    count = f"{dim['count']}\u00d7" if dim.get("count", 1) > 1 else ""
    return count + prefix + text + suffix


def map_point(point, spec, views):
    return spec["origin"] + spec["scale"] * sheet_layout.project(point, views[spec["base"]], views["center"])


def _arrow(tip, toward):
    toward = toward / np.linalg.norm(toward)
    normal = np.array([-toward[1], toward[0]])
    base = tip + toward * ARROW_LENGTH
    a, b = base + normal * ARROW_HALF_WIDTH, base - normal * ARROW_HALF_WIDTH
    return f'<polygon points="{tip[0]:.3f},{tip[1]:.3f} {a[0]:.3f},{a[1]:.3f} {b[0]:.3f},{b[1]:.3f}" fill="#000"/>'


def _line(a, b, width=DIM_WIDTH, dash=""):
    return f'<line x1="{a[0]:.3f}" y1="{a[1]:.3f}" x2="{b[0]:.3f}" y2="{b[1]:.3f}" stroke="#000" stroke-width="{width}"{dash}/>'


def _text(at, label, angle=0.0):
    return (
        f'<text x="{at[0]:.3f}" y="{at[1]:.3f}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="{TEXT_MM}" text-anchor="middle" '
        f'transform="rotate({angle:.2f} {at[0]:.3f} {at[1]:.3f})">{label}</text>'
    )


def _readable_angle(direction):
    angle = math.degrees(math.atan2(direction[1], direction[0]))
    if angle > 90:
        angle -= 180
    elif angle <= -90:
        angle += 180
    return angle


class Items:
    """Texts and line segments on one sheet, each owned by the dimension that drew it; segments are bucketed in a grid."""

    def __init__(self):
        self.texts = []
        self.segments = np.zeros((0, 4))
        self.segment_owner = np.zeros(0, dtype=int)
        self.dim_lines = []
        self.grid = {}

    def add_text(self, rect, label, owner):
        self.texts.append({"rect": rect, "label": label, "owner": owner})

    def add_segments(self, segments, owner):
        segments = np.asarray(segments, dtype=float).reshape(-1, 4)
        first = len(self.segments)
        self.segments = np.vstack([self.segments, segments])
        self.segment_owner = np.concatenate([self.segment_owner, np.full(len(segments), owner)])
        for offset, (x0, y0, x1, y1) in enumerate(segments):
            for cx in range(int(min(x0, x1) // CELL), int(max(x0, x1) // CELL) + 1):
                for cy in range(int(min(y0, y1) // CELL), int(max(y0, y1) // CELL) + 1):
                    self.grid.setdefault((cx, cy), []).append(first + offset)

    def text_rects(self):
        return np.array([t["rect"] for t in self.texts])

    def dim_line_rects(self):
        return np.array(self.dim_lines).reshape(-1, 4)

    def near(self, rect):
        ids = set()
        for cx in range(int(rect[0] // CELL), int(rect[2] // CELL) + 1):
            for cy in range(int(rect[1] // CELL), int(rect[3] // CELL) + 1):
                ids.update(self.grid.get((cx, cy), ()))
        return np.fromiter(ids, dtype=int, count=len(ids))

    def text_hits(self, text, skip=None):
        rect, owner = text["rect"], text["owner"]
        hits = []
        if not inside(rect, AREA) or rects_meet(rect, KEEP_OUT):
            hits.append("frame")
        hits += ["text" for j, other in enumerate(self.texts) if j != skip and other["owner"] != owner and rects_meet(rect, other["rect"])]
        ids = self.near(rect)
        if len(ids):
            meets = segments_meet_rect(self.segments[ids], rect) & (self.segment_owner[ids] != owner)
            if meets.any():
                hits += ["line" if o >= 0 else "edge" for o in np.unique(self.segment_owner[ids][meets])]
        return hits


def overlaps(items):
    """One record per text that meets another text, the frame, or a line that is not its own dimension's."""
    found = []
    for i, text in enumerate(items.texts):
        hits = items.text_hits(text, skip=i)
        if hits:
            found.append({"text": text["label"], "meets": sorted(set(hits))})
    return found


def _score(candidate, items):
    if any(not inside(t["rect"], AREA) or rects_meet(t["rect"], KEEP_OUT) for t in candidate["texts"]):
        return FRAME_SCORE
    hits = sum(len(items.text_hits(t)) for t in candidate["texts"])
    segments = candidate["segments"]
    if len(segments) and len(items.texts):
        rects = items.text_rects()
        low, high = segments[:, [0, 1]].min(axis=0) - PAD, segments[:, [0, 1]].max(axis=0) + PAD
        high2 = np.maximum(segments[:, [0, 1]], segments[:, [2, 3]]).max(axis=0) + PAD
        low2 = np.minimum(segments[:, [0, 1]], segments[:, [2, 3]]).min(axis=0) - PAD
        close = (rects[:, 0] < high2[0]) & (rects[:, 2] > low2[0]) & (rects[:, 1] < high2[1]) & (rects[:, 3] > low2[1])
        for j in np.nonzero(close)[0]:
            if items.texts[j]["owner"] != candidate["owner"]:
                hits += int(segments_meet_rect(segments, rects[j]).any())
    dim_rects = items.dim_line_rects()
    crowd = 0
    for r in candidate["dim_rects"]:
        if len(dim_rects):
            crowd += int(((dim_rects[:, 0] < r[2]) & (dim_rects[:, 2] > r[0]) & (dim_rects[:, 1] < r[3]) & (dim_rects[:, 3] > r[1])).sum())
    return hits * 100 + crowd * 10


def _commit(candidate, items):
    for text in candidate["texts"]:
        items.texts.append(text)
    items.add_segments(candidate["segments"], candidate["owner"])
    items.dim_lines += candidate["dim_rects"]
    return candidate["parts"]


def _best(candidates, items):
    best, best_score = None, None
    for candidate in candidates:
        score = _score(candidate, items)
        if best is None or score < best_score:
            best, best_score = candidate, score
        if score == 0:
            break
    return best


def _candidate(owner, parts, texts, segments, dim_rects):
    return {"owner": owner, "parts": parts, "texts": texts, "segments": np.array(segments).reshape(-1, 4), "dim_rects": dim_rects}


def _linear_candidates(owner, a, b, label, outward):
    along = b - a
    length = np.linalg.norm(along)
    along = along / length
    base = np.array([-along[1], along[0]])
    angle = _readable_angle(along)
    turn = math.radians(angle)
    up = np.array([math.sin(turn), -math.cos(turn)])
    step = len(label) * TEXT_MM * CHAR_WIDTH + 1.5
    inside = length >= 2 * ARROW_LENGTH + step
    order = []
    for side in (1, -1):
        for tier in range(NEAR_TIERS):
            order.append((tier, side, 0, 0.0))
            order += [(tier, side, k, lift) for lift in LIFTS for k in FAN]
    for side in (1, -1):
        order += [(tier, side, k, 0.0) for tier in range(NEAR_TIERS, TIERS) for k in SLIDES]
    for tier, side, k, lift in order:
        normal = (base if base @ outward >= 0 else -base) * side
        distance = FIRST_STACK + STACK_STEP * tier
        a2, b2 = a + normal * distance, b + normal * distance
        spot = length / 2 + k * step
        if inside and lift == 0 and not step / 2 + ARROW_LENGTH <= spot <= length - step / 2 - ARROW_LENGTH:
            continue
        anchor = length / 2
        if lift:
            spot = anchor + k * step
        reach = 0.0 if inside else ARROW_LENGTH + 3
        start, end = (min(0.0, spot - step / 2), max(length, spot + step / 2)) if not lift else (0.0, length)
        lo, hi = a2 + along * (start - reach), a2 + along * (end + reach)
        tips = (_arrow(a2, along), _arrow(b2, -along)) if inside else (_arrow(a2, -along), _arrow(b2, along))
        segs = [[*(a + normal * GAP), *(a2 + normal * OVERSHOOT)], [*(b + normal * GAP), *(b2 + normal * OVERSHOOT)], [*lo, *hi]]
        parts = [
            _line(a + normal * GAP, a2 + normal * OVERSHOOT), _line(b + normal * GAP, b2 + normal * OVERSHOOT),
            _line(lo, hi), *tips,
        ]
        point = a2 + along * (anchor if lift else spot)
        if lift:
            at = a2 + along * spot + normal * (lift + (0.0 if up @ normal > 0 else TEXT_MM))
            tail = a2 + along * spot + normal * lift
            segs.append([*point, *tail])
            parts.append(_line(point, tail))
        else:
            at = point - normal * 1.0
        parts.append(_text(at, label, angle))
        px, py = (-0.05, 1.5) if abs(hi[0] - lo[0]) > abs(hi[1] - lo[1]) else (1.5, -0.05)
        dim_rect = (min(lo[0], hi[0]) - px, min(lo[1], hi[1]) - py, max(lo[0], hi[0]) + px, max(lo[1], hi[1]) + py)
        text = {"rect": text_rect(at, label, angle), "label": label, "owner": owner}
        candidate = _candidate(owner, parts, [text], segs, [dim_rect])
        candidate["choice"] = (tier, side, k, lift)
        yield candidate


def _round_candidates(owner, kind, centre, radius, label):
    for reach in REACHES:
        for degrees in ANGLES:
            turn = math.radians(degrees)
            direction = np.array([math.cos(turn), -math.sin(turn)])
            far = centre + direction * radius
            end = far + direction * reach
            sign = 1 if direction[0] >= 0 else -1
            at = np.array([end[0] + sign * (len(label) * TEXT_MM * CHAR_WIDTH / 2 + 1), end[1] + TEXT_MM * 0.35])
            rect = text_rect(at, label)
            if kind == "diameter":
                near = centre - direction * radius
                start = near - direction * 2
                parts = [_line(start, end), _arrow(near, -direction), _arrow(far, direction)]
            elif radius > 0.5:
                start = centre
                parts = [_line(start, end), _arrow(far, direction)]
            else:
                start = far
                parts = [_line(start, end), _arrow(far, direction)]
            parts.append(_text(at, label))
            segs = [[*start, *end]]
            dim_rect = (min(start[0], end[0]) - 1, min(start[1], end[1]) - 1, max(start[0], end[0]) + 1, max(start[1], end[1]) + 1)
            yield _candidate(owner, parts, [{"rect": rect, "label": label, "owner": owner}], segs, [dim_rect])


def _dimensions(dims, views, placed, items, first=()):
    parts = []
    ordered = sorted(
        range(len(dims)),
        key=lambda i: (
            dims[i]["kind"] == "envelope", i not in first, dims[i]["kind"] not in ("linear", "envelope"), dims[i]["value"],
        ),
    )
    for owner in ordered:
        dim = dims[owner]
        if dim["kind"] in ("note", "symmetry", "concentric"):
            continue
        spec = placed[dim["view"]]
        a, b = map_point(dim["p1"], spec, views), map_point(dim["p2"], spec, views)
        label = format_value(dim)
        if dim["kind"] in ("linear", "envelope", "eq_spacing") and np.linalg.norm(b - a) >= 0.2:
            outward = (a + b) / 2 - spec["rect_centre"]
            along = (b - a) / np.linalg.norm(b - a)
            outward = outward - (outward @ along) * along
            if np.linalg.norm(outward) < 1e-6:
                outward = np.array([-along[1], along[0]])
            candidates = _linear_candidates(owner, a, b, label, outward)
        elif dim["kind"] in ("linear", "envelope"):
            continue
        elif dim["kind"] == "diameter":
            candidates = _round_candidates(owner, "diameter", (a + b) / 2, max(np.linalg.norm(b - a) / 2, 0.3), label)
        else:
            candidates = _round_candidates(owner, "radius", a, np.linalg.norm(b - a), label)
        parts += _commit(_best(candidates, items), items)
    return parts


def _edges(views, placed, items):
    parts = []
    for name, spec in placed.items():
        view, scale, origin = views[spec["base"]], spec["scale"], spec["origin"]
        detail = name.startswith("Detail")
        transform = f'translate({origin[0]:.3f} {origin[1]:.3f}) scale({scale:g})'
        hidden = f'stroke-width="{HIDDEN_WIDTH / scale:g}" stroke-dasharray="{3 / scale:g},{1.5 / scale:g}"'
        parts.append(f'<g transform="{transform}" fill="none" stroke="#000" stroke-linecap="round">')
        if detail:
            cx, cy = spec["shift"]
            parts.append(f'<clipPath id="clip{spec["letter"]}"><circle cx="{cx:.4f}" cy="{cy:.4f}" r="{spec["radius"]:.4f}"/></clipPath><g clip-path="url(#clip{spec["letter"]})">')
        else:
            parts.append("<g>")
        parts.append(f'<g {hidden}>{view["hidden_svg"]}</g>')
        parts.append(f'<g stroke-width="{VISIBLE_WIDTH / scale:g}">{view["visible_svg"]}</g></g>')
        if detail:
            parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{spec["radius"]:.4f}" stroke-width="{DIM_WIDTH / scale:g}"/>')
        parts.append("</g>")
        for line in view["visible_lines"]:
            points = np.array(line)
            if detail:
                points = points[np.linalg.norm(points - spec["shift"], axis=1) <= spec["radius"]]
            if len(points) > 1:
                points = origin + scale * points
                items.add_segments(np.hstack([points[:-1], points[1:]]), -1)
    return parts


def _detail_marks(views, placed, items):
    """Circle and letter on the parent view, and the "B (5:1)" title under each detail view."""
    parts = []
    for name, spec in placed.items():
        if not name.startswith("Detail"):
            continue
        parent = placed[spec["base"]]
        centre = parent["origin"] + parent["scale"] * spec["shift"]
        radius = parent["scale"] * spec["radius"]
        turn = np.linspace(0, 2 * math.pi, 16, endpoint=False)
        ring = centre + radius * np.stack([np.cos(turn), np.sin(turn)], axis=1)
        parts.append(f'<circle cx="{centre[0]:.3f}" cy="{centre[1]:.3f}" r="{radius:.3f}" fill="none" stroke="#000" stroke-width="{DIM_WIDTH}"/>')
        items.add_segments(np.hstack([ring, np.roll(ring, -1, axis=0)]), -6)
        letter = spec["letter"]
        spot = centre + (radius + 3) * np.array([0.7071, -0.7071]) + np.array([0.0, TEXT_MM * 0.35])
        parts.append(_text(spot, letter))
        items.add_text(text_rect(spot, letter), letter, -6)
        title = f"{letter} ({spec['factor']}:1)"
        at = spec["rect_centre"] + np.array([0.0, spec["size"][1] / 2 + 6])
        parts.append(_text(at, title))
        items.add_text(text_rect(at, title), title, -6)
    return parts


def _symmetry_lines(dims, views, placed, items):
    """Chain line through the centre plane with "=" at both ends, in the view that carries the pair distance."""
    parts = []
    for dim in dims:
        if dim["kind"] != "symmetry" or dim["view"] not in placed:
            continue
        spec = placed[dim["view"]]
        centre = map_point(dim["p1"], spec, views)
        unit = np.eye(3)[dim["axis"]]
        along = map_point(np.array(dim["p1"]) + unit, spec, views) - centre
        w, h = spec["size"]
        middle = spec["rect_centre"]
        if abs(along[0]) > abs(along[1]):
            a, b = np.array([centre[0], middle[1] - h / 2 - 4]), np.array([centre[0], middle[1] + h / 2 + 4])
        else:
            a, b = np.array([middle[0] - w / 2 - 4, centre[1]]), np.array([middle[0] + w / 2 + 4, centre[1]])
        parts.append(_line(a, b, DIM_WIDTH, ' stroke-dasharray="8,1.5,1.5,1.5"'))
        items.add_segments([[*a, *b]], -5)
        for end, away in ((a, a - b), (b, b - a)):
            spot = end + away / np.linalg.norm(away) * 3 + np.array([0, TEXT_MM * 0.35])
            parts.append(_text(spot, "="))
            items.add_text(text_rect(spot, "="), "=", -5)
    return parts


def _notes(dims, items):
    lines = sheet_layout.notes_of(dims)
    rect = sheet_layout.note_rect(lines)
    parts = []
    for i, line in enumerate(lines):
        y = rect[1] + 2 + sheet_layout.NOTE_LINE * (i + 1) - 1
        parts.append(f'<text x="{rect[0] + 2:.3f}" y="{y:.3f}" font-family="Helvetica, Arial, sans-serif" font-size="{TEXT_MM}">{line}</text>')
        box = (rect[0] + 2 - PAD, y - TEXT_MM - PAD, rect[0] + 2 + len(line) * TEXT_MM * CHAR_WIDTH + PAD, y + 0.3 + PAD)
        items.add_text(box, line, -4)
    return parts


def _section_marks(views, placed, items):
    """Hatch on the cut face, the A-A title and the cutting-plane line with its letters."""
    section = views["Section"]
    spec = placed["Section"]
    scale, origin = spec["scale"], spec["origin"]
    parts = []
    triangles = np.array(section["hatch"]).reshape(-1, 3, 2) * scale + origin
    path = "".join(f"M{t[0][0]:.3f} {t[0][1]:.3f}L{t[1][0]:.3f} {t[1][1]:.3f}L{t[2][0]:.3f} {t[2][1]:.3f}Z" for t in triangles)
    parts.append(f'<path d="{path}" fill="url(#hatch)" stroke="none"/>')
    title = spec["rect_centre"] + np.array([0.0, spec["size"][1] / 2 + 6])
    parts.append(_text(title, "A-A"))
    items.add_text(text_rect(title, "A-A"), "A-A", -2)
    axis = section["axis"]
    host_name = {0: "Left", 1: "Front", 2: "Top"}[(axis + 1) % 3]
    host = placed.get(host_name)
    if host is None:
        return parts
    w, h = host["size"]
    point = np.array(views["center"], dtype=float)
    point[axis] = section["plane"]
    here = map_point(point, host, views)
    along = map_point(point + np.eye(3)[axis], host, views) - here
    if abs(along[0]) > abs(along[1]):
        a, b = here - np.array([0, h / 2 + 6]), here + np.array([0, h / 2 + 6])
    else:
        a, b = here - np.array([w / 2 + 6, 0]), here + np.array([w / 2 + 6, 0])
    parts.append(_line(a, b, HIDDEN_WIDTH, ' stroke-dasharray="8,1.5,1.5,1.5"'))
    for end in (a, b):
        direction = (end - (a + b) / 2) / np.linalg.norm(end - (a + b) / 2)
        spot = end + direction * 4 + np.array([0, TEXT_MM * 0.35])
        parts.append(_text(spot, "A"))
        items.add_text(text_rect(spot, "A"), "A", -3)
    items.add_segments([[*a, *b]], -3)
    return parts


def _pass(views, dims, placed, first):
    items = Items()
    body = _edges(views, placed, items)
    if "Section" in views:
        body += _section_marks(views, placed, items)
    body += _detail_marks(views, placed, items)
    body += _symmetry_lines(dims, views, placed, items)
    body += _notes(dims, items)
    body += _dimensions(dims, views, placed, items, first=first)
    return body, items


def build(views, dims, placed):
    """Places the dims greedily; dims whose text still overlaps are placed first on the next pass, up to PASSES passes."""
    first, best = set(), None
    for _ in range(PASSES):
        body, items = _pass(views, dims, placed, first)
        found = overlaps(items)
        if best is None or len(found) < best[0]:
            best = (len(found), body, items)
        if not found:
            break
        first |= {t["owner"] for t in items.texts if t["label"] in {f["text"] for f in found} and t["owner"] >= 0}
    return best


def render_svg(template_svg, views, dims):
    """The sheet SVG, the Items drawn on it, the dimension records with their views and the views used, at the largest ISO scale at which the views fit."""
    plan = next(sheet_layout.plans(dims, views), None)
    if plan is None:
        raise ValueError("views do not fit the sheet at any scale")
    scale, placed, assigned = plan
    _, body, items = build(views, assigned, placed)
    body.append(_text(SCALE_FIELD, sheet_layout.scale_label(scale)))
    frame = re.sub(r"(<text[^>]*freecad:editable[^>]*>).*?(</text>)", r"\1\2", template_svg, flags=re.S)
    svg = frame.replace("</svg>", "<defs>" + HATCH + "</defs><g id=\"sheet\">" + "".join(body) + "</g></svg>")
    return svg, items, assigned, placed


def line_weights(svg):
    """Distinct stroke widths in sheet mm inside the drawn sheet group, read from the SVG alone."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(svg)
    group = next(e for e in root.iter() if e.get("id") == "sheet")
    widths = set()

    def walk(element, scale):
        match = re.search(r"scale\(([\d.e-]+)\)", element.get("transform", ""))
        scale = scale * float(match.group(1)) if match else scale
        if element.get("stroke-width"):
            widths.add(round(float(element.get("stroke-width")) * scale, 4))
        for child in element:
            walk(child, scale)

    walk(group, 1.0)
    return sorted(widths)


def write_sheet(svg, png_path, pdf_path):
    import ctypes.util

    find = ctypes.util.find_library
    ctypes.util.find_library = lambda name: HOMEBREW_CAIRO if name == "cairo" else find(name)
    import cairosvg

    data = svg.encode()
    cairosvg.svg2png(bytestring=data, write_to=str(png_path), dpi=300, background_color="white")
    cairosvg.svg2pdf(bytestring=data, write_to=str(pdf_path))
