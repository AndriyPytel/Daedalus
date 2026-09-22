"""
Level 2 -- `edges_by_concavity` groups a candidate edge selection by
concavity before any `PartDesign::Fillet` exists, so a Step can call it on
`edges_between`'s own return value and build one Fillet per group before
`doc.recompute()` ever sees a mixed Base. It shares `_edge_concavity_sign`
with `_fillets_with_mixed_concavity` rather than recomputing signs a second
way (FCC-399's own `A_PLATE_WITH_TWO_BOSSES` names Edge10 concave and Edge9
convex).
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

from test_fillet_mixed_concavity_precheck_level2 import A_PLATE_WITH_TWO_BOSSES

pytestmark = pytest.mark.freecad


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
        "held = edges_by_concavity(top.Shape, ['Edge10', 'Edge9'])\n"
    )

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert sorted(map(sorted, result.held)) == [['Edge10'], ['Edge9']], result.held


def test_no_returned_group_mixes_concavities(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "from core.kernel import kernel_worker\n"
        "top = body.Tip\n"
        "groups = edges_by_concavity(top.Shape, ['Edge10', 'Edge9'])\n"
        "held = [len(set(kernel_worker._edge_concavity_sign(top.Shape, top.Shape.getElement(n))\n"
        "                for n in g)) for g in groups]\n"
    )

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert all(count == 1 for count in result.held), (
        "a returned group shares more than one concavity: %r" % result.held)


def test_a_single_concavity_selection_comes_back_as_one_group(worker):
    code = A_PLATE_WITH_TWO_BOSSES + (
        "import Part\n"
        "top = body.Tip\n"
        "base_junctions = ['Edge%d' % (at + 1) for at, edge in enumerate(top.Shape.Edges)\n"
        "                  if isinstance(edge.Curve, Part.Circle)\n"
        "                  and abs(edge.Curve.Radius - 12.0) < 1e-6\n"
        "                  and abs(edge.valueAt((edge.FirstParameter + edge.LastParameter)\n"
        "                                        / 2.0).z - 15.0) < 1e-6]\n"
        "held = [base_junctions, edges_by_concavity(top.Shape, base_junctions)]\n"
    )

    result = worker.check(code, None)

    assert result.ok, result.traceback
    base_junctions, groups = result.held
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
        "held = kernel_worker._fillets_with_mixed_concavity(doc)\n"
    )

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert result.held == [], (
        "a Fillet built from one grouped selection was still flagged mixed: %r" % result.held)
