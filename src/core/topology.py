"""
Betti numbers b0/b1/b2 of a closed, watertight triangle mesh, and a fuzzy
log-ratio score comparing two Betti triples. b0 counts connected mesh
components that are not nested inside another (outer solid shells); b2
counts components nested inside exactly one other (an enclosed void's
boundary), found by even/odd ray-cast containment, and b1 follows from
the Euler characteristic chi = 2 * (b0 - b1 + b2). Numpy and stdlib only,
so both the vendored scorer's own mesh and FreeCAD's own tessellation can
call this without either import touching the other.
"""
import math

import numpy as np

BETTI_SHARPNESS = 2.0


def betti_numbers(vertices, triangles):
    """(b0, b1, b2) of one closed 2-manifold triangle mesh.

    *vertices* is an (N, 3) array of points, *triangles* an (M, 3) array of
    integer vertex indices. The mesh is assumed already manifold, closed
    and orientation-consistent; that is a precondition, not something this
    function checks.
    """
    vertices = np.asarray(vertices, dtype=float)
    triangles = np.asarray(triangles, dtype=np.int64)
    chi = _euler_characteristic(vertices, triangles)
    components = _triangle_components(vertices, triangles)
    b0, b2 = _classify_components_by_containment(vertices, components)
    b1 = b0 + b2 - chi // 2
    return int(b0), int(b1), int(b2)


def topology_score(candidate, reference, sharpness=BETTI_SHARPNESS):
    """Product of the per-axis fuzzy log-ratio scores of two Betti triples, in [0, 1].

    ``exp(-sharpness * |log((c + 1) / (r + 1))|)`` per axis, so a match on
    every axis scores 1 and a single badly wrong axis collapses the product
    toward 0. A negative Betti number is not a real count and scores 0.
    """
    if any(b < 0 for b in candidate) or any(b < 0 for b in reference):
        return 0.0
    score = 1.0
    for c, r in zip(candidate, reference):
        score *= math.exp(-sharpness * abs(math.log((c + 1) / (r + 1))))
    return score


def _euler_characteristic(vertices, triangles):
    n_v = len(vertices)
    n_f = len(triangles)
    edges = np.sort(
        np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]], axis=0),
        axis=1,
    )
    keys = edges[:, 0].astype(np.int64) * (n_v + 1) + edges[:, 1]
    n_e = int(len(np.unique(keys)))
    return n_v - n_e + n_f


def _triangle_components(vertices, triangles):
    """
    Triangles grouped by the connected piece of mesh they belong to.

    Labels are pulled down edge by edge and compressed until none moves, in whole-array passes,
    because a mesh of a million triangles is too many for a Python call per triangle.
    """
    n_v = len(vertices)
    label = np.arange(n_v)
    ends = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]]])
    low, high = ends[:, 0], ends[:, 1]
    while True:
        a, b = label[low], label[high]
        apart = a != b
        if not apart.any():
            break
        np.minimum.at(label, np.maximum(a, b)[apart], np.minimum(a, b)[apart])
        while True:
            jumped = label[label]
            if (jumped == label).all():
                break
            label = jumped
    owner = label[triangles[:, 0]]
    order = np.argsort(owner, kind="stable")
    starts = np.flatnonzero(np.diff(owner[order])) + 1
    return [np.asarray(group, dtype=np.int64) for group in np.split(triangles[order], starts)]


def _component_aabb(vertices, tris):
    used = np.unique(tris.reshape(-1))
    pts = vertices[used]
    return pts.min(axis=0), pts.max(axis=0)


def _interior_point(vertices, tris):
    used = np.unique(tris.reshape(-1))
    pts = vertices[used]
    bbox_diag = float(np.linalg.norm(pts.max(0) - pts.min(0)))
    step = max(1e-6, 1e-4 * bbox_diag)

    seed_tri = tris[len(tris) // 2]
    a, b, c = vertices[seed_tri[0]], vertices[seed_tri[1]], vertices[seed_tri[2]]
    centroid = (a + b + c) / 3.0
    n = np.cross(b - a, c - a)
    norm = np.linalg.norm(n)
    if norm <= 1e-12:
        return centroid
    n = n / norm

    for sign in (-1.0, 1.0):
        p = centroid + sign * step * n
        if _point_in_mesh(p, vertices, tris):
            return p
    return centroid


def _point_in_mesh(point, vertices, tris):
    rng = np.random.default_rng(0)
    for attempt in range(8):
        if attempt == 0:
            direction = np.array([1.0, 0.0, 0.0])
        else:
            v = rng.standard_normal(3)
            direction = v / max(np.linalg.norm(v), 1e-12)

        v0 = vertices[tris[:, 0]] - point
        v1 = vertices[tris[:, 1]] - point
        v2 = vertices[tris[:, 2]] - point

        edge1 = v1 - v0
        edge2 = v2 - v0
        h = np.cross(direction, edge2)
        a = np.einsum("ij,ij->i", edge1, h)
        ok = np.abs(a) > 1e-12
        if not ok.any():
            continue
        f = np.zeros_like(a)
        f[ok] = 1.0 / a[ok]
        s = -v0
        u = f * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, edge1)
        v = f * np.einsum("j,ij->i", direction, q)
        t = f * np.einsum("ij,ij->i", edge2, q)

        hit = ok & (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
        grazed = ok & (
            (np.abs(u) < 1e-9) | (np.abs(u - 1) < 1e-9)
            | (np.abs(v) < 1e-9) | (np.abs(u + v - 1) < 1e-9)
        )
        if grazed.any():
            continue
        return bool(int(hit.sum()) % 2 == 1)
    return False


def _classify_components_by_containment(vertices, components):
    interior_pts = [_interior_point(vertices, c) for c in components]
    boxes = [_component_aabb(vertices, c) for c in components]
    b0 = 0
    b2 = 0
    for i, probe in enumerate(interior_pts):
        depth = 0
        for j, comp in enumerate(components):
            if i == j:
                continue
            lo, hi = boxes[j]
            if (probe < lo - 1e-9).any() or (probe > hi + 1e-9).any():
                continue
            if _point_in_mesh(probe, vertices, comp):
                depth += 1
        if depth % 2 == 0:
            b0 += 1
        else:
            b2 += 1
    return b0, b2
