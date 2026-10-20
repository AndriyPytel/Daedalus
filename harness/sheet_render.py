"""
Lays the four projected views out on an A2 ISO 5457 frame in first-angle
position (Front top left, Top below it, Left to its right, isometric top
right), draws the dimensions in SVG and renders PNG at 300 dpi and PDF; cairocffi cannot find Homebrew's libcairo on its own, so write_sheet points it there. The template's editable title-block fields are emptied. One ISO
5455 scale serves all views; a world point maps into a view as
((p - centre) . x_direction, -(p - centre) . (direction x x_direction)).
"""
import math
import re

import numpy as np

SCALES = [50, 20, 10, 5, 2, 1, 1 / 2, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100]
AREA = (20.0, 10.0, 584.0, 410.0)
MARGIN = 25.0
GUTTER_X = 55.0
GUTTER_Y = 50.0
TEXT_MM = 3.5
ARROW_LENGTH = 3.0
ARROW_HALF_WIDTH = 0.6
FIRST_STACK = 10.0
STACK_STEP = 7.0
GAP = 1.0
OVERSHOOT = 2.0
VISIBLE_WIDTH = 0.5
HIDDEN_WIDTH = 0.25
DIM_WIDTH = 0.25
HOMEBREW_CAIRO = "/opt/homebrew/lib/libcairo.2.dylib"


def format_value(value, kind):
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return {"diameter": "\u00d8", "radius": "R"}.get(kind, "") + text


def _frame(view):
    direction = np.array(view["direction"], dtype=float)
    x_direction = np.array(view["x_direction"], dtype=float)
    return x_direction, np.cross(direction, x_direction)


def project(point, view, center):
    x_direction, y_direction = _frame(view)
    offset = np.array(point, dtype=float) - np.array(center, dtype=float)
    return np.array([offset @ x_direction, -(offset @ y_direction)])


def _extent(view):
    x0, y0, x1, y1 = view["bbox2d"]
    return x1 - x0, y1 - y0


def _layout(views, scale):
    """View centres on the sheet for `scale`, or None when the views do not fit."""
    size = {name: tuple(scale * e for e in _extent(views[name])) for name in views if name != "center"}
    front, top, left, iso = (size[n] for n in ("Front", "Top", "Left", "FrontTopLeft"))
    row_height = max(front[1], left[1], iso[1])
    width = front[0] + GUTTER_X + left[0] + GUTTER_X + iso[0]
    height = row_height + GUTTER_Y + top[1]
    x0, y0, x1, y1 = AREA
    if width > x1 - x0 - 2 * MARGIN or height > y1 - y0 - 2 * MARGIN:
        return None
    left_edge = (x0 + x1 - width) / 2
    top_edge = (y0 + y1 - height) / 2
    row_centre = top_edge + row_height / 2
    column2 = left_edge + front[0] + GUTTER_X
    column3 = column2 + left[0] + GUTTER_X
    return {
        "Front": np.array([left_edge + front[0] / 2, row_centre]),
        "Left": np.array([column2 + left[0] / 2, row_centre]),
        "FrontTopLeft": np.array([column3 + iso[0] / 2, row_centre]),
        "Top": np.array([left_edge + top[0] / 2, top_edge + row_height + GUTTER_Y + top[1] / 2]),
    }


def choose_scale(views):
    for scale in SCALES:
        if _layout(views, scale):
            return scale
    raise ValueError("views do not fit the sheet at any scale")


def scale_label(scale):
    return f"{scale:g}:1" if scale >= 1 else f"1:{round(1 / scale)}"


def _arrow(tip, toward):
    toward = toward / np.linalg.norm(toward)
    normal = np.array([-toward[1], toward[0]])
    base = tip + toward * ARROW_LENGTH
    a, b = base + normal * ARROW_HALF_WIDTH, base - normal * ARROW_HALF_WIDTH
    return f'<polygon points="{tip[0]:.3f},{tip[1]:.3f} {a[0]:.3f},{a[1]:.3f} {b[0]:.3f},{b[1]:.3f}" fill="#000"/>'


def _line(a, b, width=DIM_WIDTH):
    return f'<line x1="{a[0]:.3f}" y1="{a[1]:.3f}" x2="{b[0]:.3f}" y2="{b[1]:.3f}" stroke="#000" stroke-width="{width}"/>'


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


def _linear(a, b, label, outward, stack):
    along = b - a
    length = np.linalg.norm(along)
    along = along / length
    normal = np.array([-along[1], along[0]])
    if normal @ outward < 0:
        normal = -normal
    distance = FIRST_STACK + STACK_STEP * stack
    a2, b2 = a + normal * distance, b + normal * distance
    parts = [
        _line(a + normal * GAP, a2 + normal * OVERSHOOT),
        _line(b + normal * GAP, b2 + normal * OVERSHOOT),
        _line(a2, b2),
        _arrow(a2, along),
        _arrow(b2, -along),
    ]
    middle = (a2 + b2) / 2
    if length < 3 * ARROW_LENGTH + len(label) * TEXT_MM * 0.6:
        parts.append(_text(b2 + along * (len(label) * TEXT_MM * 0.3 + 4) - normal * 1.0, label, _readable_angle(along)))
    else:
        parts.append(_text(middle - normal * 1.0, label, _readable_angle(along)))
    return parts


def _round(center, radius, label, kind, index):
    angle = math.radians(25 + 40 * index)
    direction = np.array([math.cos(angle), -math.sin(angle)])
    far = center + direction * radius
    parts = []
    if kind == "diameter":
        near = center - direction * radius
        parts += [_line(near - direction * 6, far + direction * 8), _arrow(near, -direction), _arrow(far, direction)]
        reach = far + direction * 8
    else:
        parts += [_line(center, far + direction * 8), _arrow(far, direction)]
        reach = far + direction * 8
    parts.append(_text(reach + direction * (len(label) * TEXT_MM * 0.3 + 1), label, 0.0))
    return parts


def _dimensions(dims, views, positions, scale):
    center = views["center"]
    stacks = {}
    rounds = {}
    parts = []
    for dim in sorted(dims, key=lambda d: d["value_mm"]):
        view = views[dim["view"]]
        a = positions[dim["view"]] + scale * project(dim["p1"], view, center)
        b = positions[dim["view"]] + scale * project(dim["p2"], view, center)
        label = format_value(dim["value_mm"], dim["kind"])
        if dim["kind"] in ("diameter", "radius"):
            index = rounds.get(dim["view"], 0)
            rounds[dim["view"]] = index + 1
            middle, radius = ((a + b) / 2, np.linalg.norm(b - a) / 2) if dim["kind"] == "diameter" else (a, np.linalg.norm(b - a))
            parts += _round(middle, radius, label, dim["kind"], index)
            continue
        if np.linalg.norm(b - a) < 1.0:
            continue
        outward = (a + b) / 2 - positions[dim["view"]]
        along = (b - a) / np.linalg.norm(b - a)
        outward = outward - (outward @ along) * along
        if np.linalg.norm(outward) < 1e-6:
            outward = np.array([-along[1], along[0]])
        side = (round(outward[0] / (abs(outward).max() or 1)), round(outward[1] / (abs(outward).max() or 1)))
        key = (dim["view"], side)
        parts += _linear(a, b, label, outward, stacks.get(key, 0))
        stacks[key] = stacks.get(key, 0) + 1
    return parts


def _edges(views, positions, scale):
    parts = []
    for name, position in positions.items():
        view = views[name]
        transform = f'translate({position[0]:.3f} {position[1]:.3f}) scale({scale:g})'
        hidden = f'stroke-width="{HIDDEN_WIDTH / scale:g}" stroke-dasharray="{3 / scale:g},{1.5 / scale:g}"'
        parts.append(f'<g transform="{transform}" fill="none" stroke="#000" stroke-linecap="round">')
        parts.append(f'<g {hidden}>{view["hidden_svg"]}</g>')
        parts.append(f'<g stroke-width="{VISIBLE_WIDTH / scale:g}">{view["visible_svg"]}</g></g>')
    return parts


def render_svg(template_svg, views, dims):
    scale = choose_scale(views)
    positions = _layout(views, scale)
    body = _edges(views, positions, scale) + _dimensions(dims, views, positions, scale)
    x0, y0, x1, y1 = AREA
    body.append(_text(np.array([x1 - 25, y1 - 4]), f"{scale_label(scale)}", 0.0))
    frame = re.sub(r"(<text[^>]*freecad:editable[^>]*>).*?(</text>)", r"\1\2", template_svg, flags=re.S)
    return frame.replace("</svg>", "<g id=\"sheet\">" + "".join(body) + "</g></svg>")


def write_sheet(svg, png_path, pdf_path):
    import ctypes.util

    find = ctypes.util.find_library
    ctypes.util.find_library = lambda name: HOMEBREW_CAIRO if name == "cairo" else find(name)
    import cairosvg

    data = svg.encode()
    cairosvg.svg2png(bytestring=data, write_to=str(png_path), dpi=300, background_color="white")
    cairosvg.svg2pdf(bytestring=data, write_to=str(pdf_path))
