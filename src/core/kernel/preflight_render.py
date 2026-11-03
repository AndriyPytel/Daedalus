"""
The software rasterizer behind a Step's render. Isolated from
`core.kernel.kernel_worker` because it has no dependency on the kernel's JSON
protocol or its PartDesign checks -- only on a Document and a path -- and it
imports `numpy`/`PIL` locally the same way the worker imports FreeCAD locally,
so moving it does not add a module-level dependency the child process did not
already carry.
"""
import math
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from operator import attrgetter

_LIGHT = (0.4, 0.5, 0.76)
_TESSELLATION = 0.2
_BACKGROUND = (250, 250, 252)
_EDGE = (40, 40, 45)
_EDGE_BIAS = 0.002
_SMALL_SIDES = (1, 2, 4, 8, 16, 32, 64, 128)
_FRAGMENTS = 1 << 20
_BANDS = 4
_AZIMUTH, _ELEVATION = math.radians(45), math.radians(35.264)


VIEWS = {
    "front": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "top": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "right": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "left": ((0, -1, 0), (0, 0, 1), (-1, 0, 0)),
}
ISO = "iso"


def _view_matrix(view=ISO):
    """
    *view* as rows: right, up, towards the camera.

    `iso` is 45° azimuth and 35.264° elevation, the viewport's default and an
    exact isometry; `front`, `top`, `right` and `left` are the orthographic views FreeCAD's own buttons give.
    """
    import numpy as np

    if view in VIEWS:
        return np.array(VIEWS[view], dtype=float)
    azimuth, elevation = _AZIMUTH, _ELEVATION
    return np.array([
        [math.cos(azimuth), -math.sin(azimuth), 0],
        [math.sin(azimuth) * math.sin(elevation), math.cos(azimuth) * math.sin(elevation),
         math.cos(elevation)],
        [-math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation),
         math.sin(elevation)],
    ])


def _is_modelled(shape):
    """
    True for a shape that is part of the part, rather than of the scaffolding.

    A Body's Origin (three `App::Line` axes, three `App::Plane` datums,
    each 1e100 across) has no real surface or edges, but taken literally
    would frame the render around one pixel of part.
    """
    if shape is None or shape.isNull() or not shape.Faces:
        return False
    box = shape.BoundBox
    return box.isValid() and box.DiagonalLength < 1e6


def _inside_a_body(obj):
    """True for a PartDesign feature, whose Shape is the Body as it stood at that feature rather than the part."""
    group = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
    return group is not None and group.TypeId == "PartDesign::Body"


def _feeds_a_modelled_body(obj, modelled):
    """True for a shape a Body edits as its BaseFeature, which the Body's own Shape already holds."""
    return any(user.TypeId == "PartDesign::Body" and user.BaseFeature == obj and modelled[user.Name]
               for user in obj.InList)


def _tessellated(doc):
    """
    Every modelled shape's triangles, as an (N, 3, 3) array, and its real edges, in model space.

    A straight edge needs two points; a curve is walked finely enough that the
    polyline is not the thing you notice. A source a Body edits is drawn once, as the Body.
    """
    import numpy as np

    triangles, polylines = [], []
    modelled = {obj.Name: _is_modelled(getattr(obj, "Shape", None)) for obj in doc.Objects}
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if not modelled[obj.Name] or _inside_a_body(obj) or _feeds_a_modelled_body(obj, modelled):
            continue
        vertices, facets = shape.tessellate(_TESSELLATION)
        points = np.stack([np.fromiter(map(attrgetter(axis), vertices), dtype=float, count=len(vertices))
                           for axis in "xyz"], axis=1)
        triangles.append(points[np.fromiter(chain.from_iterable(facets), dtype=np.int64,
                                            count=3 * len(facets)).reshape(-1, 3)])
        for edge in shape.Edges:
            try:
                along = edge.discretize(Deflection=_TESSELLATION)
            except Exception:
                continue
            if len(along) >= 2:
                polylines.append(np.array([[p.x, p.y, p.z] for p in along], dtype=float))
    return (np.concatenate(triangles) if triangles else np.empty((0, 3, 3))), polylines


def _fill_triangle(zbuffer, owner, index, xy, z, rows=None):
    """
    One large flat-shaded triangle, per pixel, against the depth buffer; *owner* takes the triangle's *index* where it is nearer, within the *rows* (low, high) if given.

    Only a per-pixel comparison can tell which part of two interpenetrating triangles is in front,
    which mean-depth sort cannot. A zero-area triangle is edge-on and has no pixels.
    """
    import numpy as np

    height, width = zbuffer.shape
    low, high = rows or (0, height)
    left = max(int(math.floor(xy[:, 0].min())), 0)
    right = min(int(math.ceil(xy[:, 0].max())) + 1, width)
    top = max(int(math.floor(xy[:, 1].min())), low)
    bottom = min(int(math.ceil(xy[:, 1].max())) + 1, high)
    if right <= left or bottom <= top:
        return

    (ax, ay), (bx, by), (cx, cy) = xy
    area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(area) < 1e-12:
        return

    px, py = np.meshgrid(np.arange(left, right) + 0.5, np.arange(top, bottom) + 0.5)
    u = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / area
    v = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / area
    w = 1.0 - u - v
    inside = (u >= 0) & (v >= 0) & (w >= 0)
    if not inside.any():
        return

    depth = u * z[0] + v * z[1] + w * z[2]
    window = zbuffer[top:bottom, left:right]
    nearer = inside & (depth > window)
    window[nearer] = depth[nearer]
    owner[top:bottom, left:right][nearer] = index


def _fill_small_triangles(zbuffer, owner, indices, pixels, depth, bounds, across, down):
    """
    Every pixel of the triangles *indices*, whose bounding boxes fit in *across* x *down*, tested at once.

    Tessellated parts are mostly triangles a pixel or two across, too many for one Python call each.
    Fragments are written nearest last, so a pixel two triangles reach keeps the nearer one.
    """
    import numpy as np

    width = zbuffer.shape[1]
    left, top, span_x, span_y = (column[indices] for column in bounds)
    offsets = np.arange(across * down)
    shift_x, shift_y = offsets % across, offsets // across
    flat_depth, flat_owner = zbuffer.reshape(-1), owner.reshape(-1)
    per_chunk = max(1, _FRAGMENTS // (across * down))

    local_x, local_y = shift_x.astype(np.float32), shift_y.astype(np.float32)

    for start in range(0, len(indices), per_chunk):
        chunk = slice(start, start + per_chunk)
        tri = indices[chunk]
        (ax, ay), (bx, by), (cx, cy) = (pixels[tri, k, :].T for k in range(3))
        z0, z1, z2 = depth[tri].T
        inverse = 1.0 / ((by - cy) * (ax - cx) + (cx - bx) * (ay - cy))
        u_x, u_y = (by - cy) * inverse, (cx - bx) * inverse
        v_x, v_y = (cy - ay) * inverse, (ax - cx) * inverse
        first_x, first_y = left[chunk] + 0.5, top[chunk] + 0.5
        u_0 = u_x * (first_x - cx) + u_y * (first_y - cy)
        v_0 = v_x * (first_x - cx) + v_y * (first_y - cy)
        z_x, z_y = u_x * (z0 - z2) + v_x * (z1 - z2), u_y * (z0 - z2) + v_y * (z1 - z2)
        z_0 = u_0 * (z0 - z2) + v_0 * (z1 - z2) + z2
        u = u_0.astype(np.float32)[:, None] + u_x.astype(np.float32)[:, None] * local_x \
            + u_y.astype(np.float32)[:, None] * local_y
        v = v_0.astype(np.float32)[:, None] + v_x.astype(np.float32)[:, None] * local_x \
            + v_y.astype(np.float32)[:, None] * local_y
        covered = (u >= 0) & (v >= 0) & (u + v <= 1)
        covered &= (shift_x < span_x[chunk, None]) & (shift_y < span_y[chunk, None])
        member, offset = np.nonzero(covered)
        if not len(member):
            continue
        fragment_depth = z_0[member] + z_x[member] * shift_x[offset] + z_y[member] * shift_y[offset]
        where = (top[chunk][member] + shift_y[offset]) * width + left[chunk][member] + shift_x[offset]
        keep = fragment_depth > flat_depth[where]
        where, fragment_depth, member = where[keep], fragment_depth[keep], member[keep]
        nearest_last = np.argsort(fragment_depth, kind="stable")
        flat_depth[where[nearest_last]] = fragment_depth[nearest_last]
        flat_owner[where[nearest_last]] = tri[member[nearest_last]]


def _rasterize(pixels, depth, size):
    """
    The nearest triangle's index at every pixel of a *size* (width, height) image, -1 where none.

    Triangles whose box fits `_SMALL_SIDES` are filled in batches grouped by the box that
    holds them; the few larger ones, one at a time. Horizontal bands of the image are filled by
    threads, numpy letting go of the GIL, each touching only its own rows.
    """
    import numpy as np

    width, height = size
    zbuffer = np.full((height, width), -np.inf)
    owner = np.full((height, width), -1, dtype=np.int64)

    x, y = pixels[:, :, 0], pixels[:, :, 1]
    first_x, last_x = np.floor(x.min(1)), np.ceil(x.max(1)) + 1
    first_y, last_y = np.floor(y.min(1)), np.ceil(y.max(1)) + 1
    (ax, ay), (bx, by), (cx, cy) = (pixels[:, k, :].T for k in range(3))
    flat = np.abs((by - cy) * (ax - cx) + (cx - bx) * (ay - cy)) < 1e-12
    sides = np.array(_SMALL_SIDES)

    def fill_band(rows):
        low, high = rows
        left = np.maximum(first_x, 0).astype(np.int64)
        span_x = np.minimum(last_x, width).astype(np.int64) - left
        top = np.maximum(first_y, low).astype(np.int64)
        span_y = np.minimum(last_y, high).astype(np.int64) - top
        drawn = (span_x > 0) & (span_y > 0) & ~flat
        longest = np.maximum(span_x, span_y)
        bounds = (left, top, span_x, span_y)
        class_x, class_y = np.searchsorted(sides, span_x), np.searchsorted(sides, span_y)
        small = drawn & (longest <= sides[-1])
        for wide in range(len(sides)):
            for tall in range(len(sides)):
                of_this_size = np.flatnonzero(small & (class_x == wide) & (class_y == tall))
                if len(of_this_size):
                    _fill_small_triangles(zbuffer, owner, of_this_size, pixels, depth, bounds,
                                          int(sides[wide]), int(sides[tall]))
        for index in np.flatnonzero(drawn & (longest > sides[-1])):
            _fill_triangle(zbuffer, owner, index, pixels[index], depth[index], rows)

    edges = np.linspace(0, height, _BANDS + 1).astype(int)
    with ThreadPoolExecutor(_BANDS) as pool:
        list(pool.map(fill_band, zip(edges[:-1], edges[1:])))
    return zbuffer, owner


def _draw_edges(colour, zbuffer, points, depths, firsts, bias):
    """
    Projected polylines, depth-tested so hidden edges stay hidden: *firsts* are the indices in *points* where a segment starts.

    Two adjacent faces of similar shade merge without edges drawn. An
    edge lies on the faces it bounds, so the depth bias is a fraction of
    the scene's depth range; sampling is capped so a far off-screen segment lands somewhere.
    """
    import numpy as np

    height, width = zbuffer.shape
    longest = 2 * (width + height)
    (x0, y0), (x1, y1) = points[firsts].T, points[firsts + 1].T
    z0, z1 = depths[firsts], depths[firsts + 1]
    finite = np.isfinite(x0) & np.isfinite(y0) & np.isfinite(x1) & np.isfinite(y1) \
        & np.isfinite(z0) & np.isfinite(z1)
    x0, y0, x1, y1, z0, z1 = (column[finite] for column in (x0, y0, x1, y1, z0, z1))
    steps = np.minimum(np.maximum(np.abs(x1 - x0), np.abs(y1 - y0)).astype(np.int64) + 1, longest)
    counts = steps * 2 + 2
    segment = np.repeat(np.arange(len(counts)), counts)
    first = np.cumsum(counts) - counts
    along = (np.arange(len(segment)) - first[segment]) / (counts[segment] - 1)
    xs = np.rint(x0[segment] + (x1 - x0)[segment] * along).astype(np.int64)
    ys = np.rint(y0[segment] + (y1 - y0)[segment] * along).astype(np.int64)
    zs = z0[segment] + (z1 - z0)[segment] * along

    on_screen = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    xs, ys, zs = xs[on_screen], ys[on_screen], zs[on_screen]
    visible = zs + bias >= zbuffer[ys, xs]
    colour[ys[visible], xs[visible]] = _EDGE


def render(doc, path, size=(800, 600), view=ISO):
    """
    Isometric render of every shape in the Document. Software-rasterised
    on purpose: a headless FreeCAD has no GL context, so Coin's offscreen
    renderer cannot produce a pixel here. A per-pixel depth buffer and
    real edges, because Review judges by this file and
    painter's-algorithm didn't resemble the viewport; anti-aliasing,
    per-object colour and perspective stay deliberately absent.
    """
    import numpy as np
    from PIL import Image

    triangles, polylines = _tessellated(doc)
    if not len(triangles):
        return None

    tris = triangles
    matrix = _view_matrix(view)
    viewed = tris @ matrix.T
    flat = viewed[:, :, :2]

    width, height = size
    low, high = flat.reshape(-1, 2).min(0), flat.reshape(-1, 2).max(0)
    scale = min(width, height) * 0.82 / max((high - low).max(), 1e-9)
    offset = np.array([width, height]) / 2 - (low + high) / 2 * scale

    def to_pixels(points_2d):
        placed = points_2d * scale + offset
        placed[..., 1] = height - placed[..., 1]
        return placed

    pixels = to_pixels(flat)
    depth = viewed[:, :, 2]

    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1
    shade = np.clip(abs(normals / lengths[:, None] @ np.array(_LIGHT)), 0, 1)

    zbuffer, owner = _rasterize(pixels, depth, size)
    grey = (60 + 175 * shade).astype(np.int64)
    shades = np.stack([grey, grey, np.minimum(255, (grey * 1.05).astype(np.int64))], axis=1)
    colour = np.zeros((height, width, 3), dtype=np.uint8)
    colour[:, :] = _BACKGROUND
    covered = owner >= 0
    colour[covered] = shades[owner[covered]]

    span = float(depth.max() - depth.min()) or 1.0
    bias = span * _EDGE_BIAS
    if polylines:
        every = np.concatenate(polylines)
        seen = every @ matrix.T
        ends = np.cumsum([len(polyline) for polyline in polylines]) - 1
        firsts = np.delete(np.arange(len(every) - 1), ends[:-1])
        _draw_edges(colour, zbuffer, to_pixels(seen[:, :2]), seen[:, 2], firsts, bias)

    Image.fromarray(colour, "RGB").save(path)
    return path
