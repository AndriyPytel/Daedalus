"""
Level 2 -- `edges_by_concavity` groups a candidate edge selection by
concavity before any `PartDesign::Fillet` exists, so a Step can call it on
`edges_between`'s own return value and build one Fillet per group before
`doc.recompute()` ever sees a mixed Base. It shares `_edge_concavity_sign`
with `_fillets_with_mixed_concavity` rather than recomputing signs a second
way; the imported `A_PLATE_WITH_TWO_BOSSES` fixture names Edge10 concave
and Edge9 convex. `A_BOX_WITH_ONE_FILLETED_EDGE`, built here, has one edge
already tangent to its neighbour from a first fillet pass -- a transitional
edge, excluded from every group rather than mis-signed convex.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

from test_fillet_mixed_concavity_precheck_level2 import A_PLATE_WITH_TWO_BOSSES

pytestmark = pytest.mark.freecad


PRINT_FACTS = "import json\nprint('FACTS ' + json.dumps(facts))\n"


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_mixed_selection_is_split_by_concavity(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "top = body.Tip\n"
        "facts = edges_by_concavity(top.Shape, ['Edge10', 'Edge9'])\n"
    ) + PRINT_FACTS
    assert worker.seed().ok

    facts = worker.measure(code)

    assert facts is not None
    assert sorted(map(sorted, facts)) == [['Edge10'], ['Edge9']], facts


def test_no_returned_group_mixes_concavities(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "from core.kernel import kernel_worker\n"
        "top = body.Tip\n"
        "groups = edges_by_concavity(top.Shape, ['Edge10', 'Edge9'])\n"
        "facts = [len(set(kernel_worker._edge_concavity_sign(top.Shape, top.Shape.getElement(n))\n"
        "                for n in g)) for g in groups]\n"
    ) + PRINT_FACTS
    assert worker.seed().ok

    facts = worker.measure(code)

    assert facts is not None
    assert all(count == 1 for count in facts), (
        "a returned group shares more than one concavity: %r" % facts)


def test_a_single_concavity_selection_comes_back_as_one_group(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "import Part\n"
        "top = body.Tip\n"
        "base_junctions = ['Edge%d' % (at + 1) for at, edge in enumerate(top.Shape.Edges)\n"
        "                  if isinstance(edge.Curve, Part.Circle)\n"
        "                  and abs(edge.Curve.Radius - 12.0) < 1e-6\n"
        "                  and abs(edge.valueAt((edge.FirstParameter + edge.LastParameter)\n"
        "                                        / 2.0).z - 15.0) < 1e-6]\n"
        "facts = [base_junctions, edges_by_concavity(top.Shape, base_junctions)]\n"
    ) + PRINT_FACTS
    assert worker.seed().ok

    facts = worker.measure(code)

    assert facts is not None
    base_junctions, groups = facts
    assert len(base_junctions) == 2, "expected both boss base junctions, got %r" % base_junctions
    assert groups == [base_junctions], (
        "a single-concavity selection was split instead of coming back as one group: %r" % groups)


def test_a_step_builds_one_fillet_per_group_and_none_is_left_mixed(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "from core.kernel import kernel_worker\n"
        "top = body.Tip\n"
        "groups = edges_by_concavity(top.Shape, ['Edge10', 'Edge9'])\n"
        "for at, names in enumerate(groups):\n"
        "    fillet = doc.addObject('PartDesign::Fillet', 'Fillet%d' % at)\n"
        "    body.addObject(fillet)\n"
        "    fillet.Base = (top, names)\n"
        "    fillet.Radius = 1.0\n"
        "    doc.recompute()\n"
        "facts = kernel_worker._fillets_with_mixed_concavity(doc)\n"
    ) + PRINT_FACTS
    assert worker.seed().ok

    facts = worker.measure(code)

    assert facts == [], (
        "a Fillet built from one grouped selection was still flagged mixed: %r" % facts)



A_BOX_WITH_ONE_FILLETED_EDGE = """
body = doc.addObject('PartDesign::Body', 'Body')
box = doc.addObject('PartDesign::AdditiveBox', 'Box')
body.addObject(box)
box.Length = 80
box.Width = 40
box.Height = 15
doc.recompute()

def plane_info(f):
    s = f.Surface
    if type(s).__name__ != 'Plane':
        return None
    return s.Axis, s.Position

def is_at(axis_char, value):
    def predicate(f):
        info = plane_info(f)
        if info is None:
            return False
        axis, pos = info
        return abs(abs(getattr(axis, axis_char)) - 1) < 0.01 and abs(getattr(pos, axis_char) - value) < 0.1
    return predicate

top = body.Tip
seed = edges_between(top.Shape, is_at('z', 15), is_at('y', 0))
fillet = doc.addObject('PartDesign::Fillet', 'Fillet1')
body.addObject(fillet)
fillet.Base = (top, seed)
fillet.Radius = 3.0
doc.recompute()

tip = body.Tip

def is_fillet_face(f):
    s = f.Surface
    return type(s).__name__ == 'Cylinder' and abs(s.Radius - 3.0) < 1e-6

boundary = edges_between(tip.Shape, is_fillet_face, lambda f: not is_fillet_face(f))
transitional = [n for n in boundary if type(tip.Shape.getElement(n).Curve).__name__ == 'Line']
real_corner = edges_between(tip.Shape, is_at('x', 80), is_at('z', 0))
"""


def test_a_transitional_tangent_edge_has_no_concavity_sign(worker):
    """
    A fillet's own boundary edge is C0/G1-tangent to its neighbour -- the
    two adjacent faces meet at 0 degrees, not the 90 degrees of a real
    corner -- so `_edge_concavity_sign` must treat it like any other edge
    with no resolvable sign rather than call it convex.
    """
    code = A_BOX_WITH_ONE_FILLETED_EDGE + (
        "from core.kernel import kernel_worker\n"
        "facts = [kernel_worker._edge_concavity_sign(tip.Shape, tip.Shape.getElement(n))\n"
        "        for n in transitional]\n"
    ) + PRINT_FACTS
    assert worker.seed().ok

    facts = worker.measure(code)

    assert facts is not None
    assert all(sign is None for sign in facts), (
        "a transitional tangent edge was given a concavity sign: %r" % facts)


def test_a_transitional_tangent_edge_is_excluded_from_every_group(worker):
    code = A_BOX_WITH_ONE_FILLETED_EDGE + (
        "facts = [transitional, edges_by_concavity(tip.Shape, transitional + real_corner)]\n"
    ) + PRINT_FACTS
    assert worker.seed().ok

    facts = worker.measure(code)

    assert facts is not None
    transitional_names, groups = facts
    grouped = [name for group in groups for name in group]
    assert not (set(transitional_names) & set(grouped)), (
        "a transitional tangent edge was returned in a group: %r" % groups)


def test_a_fillet_on_the_filtered_group_actually_rounds_every_requested_edge(worker):
    """
    OCC silently drops a C0-tangent edge from a `PartDesign::Fillet`'s Base
    while still reporting the feature built, so a selection that still
    carried one would round fewer faces than edges it asked for. Counting
    the Fillet's new faces against the group size catches that.
    """
    code = A_BOX_WITH_ONE_FILLETED_EDGE + (
        "groups = edges_by_concavity(tip.Shape, transitional + real_corner)\n"
        "faces_before = len(tip.Shape.Faces)\n"
        "fillet2 = doc.addObject('PartDesign::Fillet', 'Fillet2')\n"
        "body.addObject(fillet2)\n"
        "fillet2.Base = (tip, groups[-1])\n"
        "fillet2.Radius = 1.0\n"
        "doc.recompute()\n"
        "facts = [len(groups[-1]), built_ok(fillet2), len(fillet2.Shape.Faces) - faces_before]\n"
    ) + PRINT_FACTS
    assert worker.seed().ok

    facts = worker.measure(code)

    assert facts is not None
    requested_count, ok, new_faces = facts
    assert ok, "the filtered group's fillet did not build clean"
    assert new_faces == requested_count, (
        "the fillet rounded %d faces but the group asked for %d edges -- an edge was silently "
        "dropped" % (new_faces, requested_count))
