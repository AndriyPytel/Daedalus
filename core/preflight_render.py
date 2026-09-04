"""
The software rasterizer behind Preflight's render. Isolated from
`core.preflight_worker` because it has no dependency on the worker's JSON
protocol or its PartDesign checks -- only on a Document and a path -- and it
imports `numpy`/`PIL` locally the same way the worker imports FreeCAD locally,
so moving it does not add a module-level dependency the child process did not
already carry.
"""
import math

_LIGHT = (0.4, 0.5, 0.76)
_TESSELLATION = 0.2
_BACKGROUND = (250, 250, 252)
_EDGE = (40, 40, 45)
_EDGE_BIAS = 0.002
_AZIMUTH, _ELEVATION = math.radians(45), math.radians(35.264)


def _view_matrix():
    """
    The isometric view, as rows: right, up, towards the camera.

    45° azimuth and 35.264° elevation, which is the viewport's default and an
    exact isometry.
    """
    import numpy as np

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

    A Body carries an Origin: three `App::Line` axes and three `App::Plane`
    datums, each of them 1e100 across. They have no surface to shade and their
    edges are not edges of anything, but taken literally they would set the
    framing for the whole render and put the part in one pixel. The viewport
    does not draw them either.
    """
    if shape is None or shape.isNull() or not shape.Faces:
        return False
    box = shape.BoundBox
    return box.isValid() and box.DiagonalLength < 1e6


def _tessellated(doc):
    """
    Every modelled shape's triangles and its real edges, in model space.

    A straight edge needs two points; a curve is walked finely enough that the
    polyline is not the thing you notice.
    """
    import numpy as np

    triangles, polylines = [], []
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if not _is_modelled(shape):
            continue
        vertices, facets = shape.tessellate(_TESSELLATION)
        points = np.array([[v.x, v.y, v.z] for v in vertices], dtype=float)
        for facet in facets:
            triangles.append(points[list(facet[:3])])
        for edge in shape.Edges:
            try:
                along = edge.discretize(Deflection=_TESSELLATION)
            except Exception:
                continue
            if len(along) >= 2:
                polylines.append(np.array([[p.x, p.y, p.z] for p in along], dtype=float))
    return triangles, polylines


def _fill_triangle(colour, zbuffer, xy, z, rgb):
    """
    One flat-shaded triangle, per pixel, against the depth buffer.

    The depth test is what the mean-depth sort could not do: a triangle that
    interpenetrates another is in front over part of itself and behind over the
    rest, and only a per-pixel comparison can say which part is which (FCC-46).

    A triangle of no area is edge-on and has no pixels of its own.
    """
    import numpy as np

    height, width = zbuffer.shape
    left = max(int(math.floor(xy[:, 0].min())), 0)
    right = min(int(math.ceil(xy[:, 0].max())) + 1, width)
    top = max(int(math.floor(xy[:, 1].min())), 0)
    bottom = min(int(math.ceil(xy[:, 1].max())) + 1, height)
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
    colour[top:bottom, left:right][nearer] = rgb


def _draw_edge(colour, zbuffer, points, depths, bias):
    """
    One projected polyline, depth-tested so hidden edges stay hidden.

    Edges are the larger half of why the render did not look like the viewport:
    two adjacent faces of a similar shade merge into one blob without them.

    An edge lies exactly on the faces it bounds, so it loses a strict depth
    test to its own surface; the bias against that is a fraction of the scene's
    depth range, which makes it independent of the part's size. The sampling is
    capped, so that a segment running far off-screen is sampled for the part
    that can land somewhere rather than along its whole length.
    """
    import numpy as np

    height, width = zbuffer.shape
    longest = 2 * (width + height)
    for start in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[start], points[start + 1]
        z0, z1 = depths[start], depths[start + 1]
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1, z0, z1)):
            continue
        steps = min(int(max(abs(x1 - x0), abs(y1 - y0))) + 1, longest)
        along = np.linspace(0.0, 1.0, steps * 2 + 2)
        xs = np.rint(x0 + (x1 - x0) * along).astype(int)
        ys = np.rint(y0 + (y1 - y0) * along).astype(int)
        zs = z0 + (z1 - z0) * along

        on_screen = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
        xs, ys, zs = xs[on_screen], ys[on_screen], zs[on_screen]
        if not len(xs):
            continue
        visible = zs + bias >= zbuffer[ys, xs]
        colour[ys[visible], xs[visible]] = _EDGE


def render(doc, path, size=(800, 600)):
    """
    Isometric render of every shape in the Document.

    Software-rasterised on purpose: a headless FreeCAD has no GL context, so
    Coin's offscreen renderer cannot produce a pixel here.

    A per-pixel depth buffer and the shapes' own edges, because Review judges
    by this file and the painter's-algorithm version did not resemble the
    viewport it is compared against (FCC-46). Anti-aliasing, per-object colour
    and perspective are deliberately still absent: Review does not judge by
    those, and the render is not trying to be a screenshot.
    """
    import numpy as np
    from PIL import Image

    triangles, polylines = _tessellated(doc)
    if not triangles:
        return None

    tris = np.array(triangles)
    view = _view_matrix()
    viewed = tris @ view.T
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

    colour = np.zeros((height, width, 3), dtype=np.uint8)
    colour[:, :] = _BACKGROUND
    zbuffer = np.full((height, width), -np.inf)

    for i in range(len(tris)):
        grey = int(60 + 175 * shade[i])
        _fill_triangle(colour, zbuffer, pixels[i], depth[i],
                       (grey, grey, min(255, int(grey * 1.05))))

    span = float(depth.max() - depth.min()) or 1.0
    bias = span * _EDGE_BIAS
    for polyline in polylines:
        seen = polyline @ view.T
        _draw_edge(colour, zbuffer, to_pixels(seen[:, :2]), seen[:, 2], bias)

    Image.fromarray(colour, "RGB").save(path)
    return path
