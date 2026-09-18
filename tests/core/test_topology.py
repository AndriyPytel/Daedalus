"""
Level 1 -- `core.topology`'s pure Betti algorithm and score, on synthetic
boxes that need no FreeCAD, and the reference Betti triples the level-2
suite (`tests/core/level2/test_reference_facts.py`) measures against real
geometry.
"""
import numpy as np

from core.topology import betti_numbers, topology_score


def _box(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    vertices = np.array([
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ], dtype=float)
    faces = [
        (0, 2, 1), (0, 3, 2),
        (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6),
        (3, 0, 4), (3, 4, 7),
    ]
    triangles = np.array(faces, dtype=np.int64)
    return vertices, triangles


def test_a_single_box_has_one_component_no_handles_no_voids():
    vertices, triangles = _box((0, 0, 0), (1, 1, 1))

    assert betti_numbers(vertices, triangles) == (1, 0, 0)


def test_two_disjoint_boxes_have_two_components():
    v1, t1 = _box((0, 0, 0), (1, 1, 1))
    v2, t2 = _box((10, 10, 10), (11, 11, 11))
    vertices = np.concatenate([v1, v2])
    triangles = np.concatenate([t1, t2 + len(v1)])

    assert betti_numbers(vertices, triangles) == (2, 0, 0)


def test_a_matching_pair_scores_one():
    assert topology_score((1, 3, 0), (1, 3, 0)) == 1.0


def test_a_completely_wrong_axis_collapses_the_product_toward_zero():
    assert topology_score((1, 3, 0), (1, 3, 5)) < 0.1


def test_a_negative_betti_scores_zero():
    assert topology_score((-1, 3, 0), (1, 3, 0)) == 0.0
