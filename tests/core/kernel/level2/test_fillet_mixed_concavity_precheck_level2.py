"""
Level 2 -- a PartDesign::Fillet whose Base mixes concave and convex edges is
refused with the mixed-concavity trap's own sentence, even when the same
selection also leaves OCC's own recompute with the Fillet Invalid.

`_objects_left_in_error` used to run before `_fillets_with_mixed_concavity`,
so a mixed selection that also failed the OCC command (`BRep_API: command
not done`, `TopoDS_FrozenShape`) reached the Step as that generic OCC
refusal instead of the sentence naming what it selected
(`logs/sessions/fb37f67515d646e2a887f714b81cd5db.jsonl`, the fillet Steps at
01:17-01:25). `A_PLATE_WITH_TWO_BOSSES`'s own two collar/plate junction
circles (Edge10, Edge9) reproduce that failure at Radius=8: the Fillet is
left `Invalid` by OCC on this exact selection, so the ordering this test
pins is the one the real crash-adjacent selection actually hits.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


A_PLATE_WITH_TWO_BOSSES = """
body = doc.addObject(\'PartDesign::Body\', \'Body\')
box = doc.addObject(\'PartDesign::AdditiveBox\', \'Box\')
body.addObject(box)
box.Length = 80
box.Width = 40
box.Height = 15
doc.recompute()

boss_a = doc.addObject(\'PartDesign::AdditiveCylinder\', \'BossA\')
body.addObject(boss_a)
boss_a.Radius = 12
boss_a.Height = 10
boss_a.Placement = App.Placement(App.Vector(20, 20, 15), App.Rotation())
doc.recompute()

boss_b = doc.addObject(\'PartDesign::AdditiveCylinder\', \'BossB\')
body.addObject(boss_b)
boss_b.Radius = 12
boss_b.Height = 10
boss_b.Placement = App.Placement(App.Vector(60, 20, 15), App.Rotation())
doc.recompute()
"""


def a_fillet_mixing_the_two_boss_junctions_at(radius):
    """
    Edge10 and Edge9 are BossA's own plate junction (concave, the boss pulls
    material in) and one of the plate's own top edges (convex) -- the same
    kind of flat-face/cylinder junction the ticket names. Radius 8 is where
    this selection also fails OCC's own command, not just the concavity
    check.
    """
    return A_PLATE_WITH_TWO_BOSSES + (
        "top = body.Tip\n"
        "fillet = doc.addObject(\'PartDesign::Fillet\', \'Fillet\')\n"
        "body.addObject(fillet)\n"
        "fillet.Base = (top, [\'Edge10\', \'Edge9\'])\n"
        "fillet.Radius = %s\n"
        "doc.recompute()\n" % (radius,)
    )


def test_the_worker_survives_a_mixed_concavity_selection_that_also_fails_occ(worker):
    assert worker.seed().ok
    result = worker.rehearse(a_fillet_mixing_the_two_boss_junctions_at(8.0))

    assert not result.ok, "a fillet mixing concave and convex Base edges was passed to the Document"
    assert worker.alive, "the kernel did not survive the mixed-concavity selection"


def test_the_failure_names_the_mixed_edges_not_the_bare_occ_refusal(worker):
    assert worker.seed().ok
    said = worker.rehearse(a_fillet_mixing_the_two_boss_junctions_at(8.0)).traceback

    assert "mixes concave and convex edges" in said, (
        "the mixed-concavity trap did not fire before OCC\'s own refusal: %s" % said)
    assert "Edge10" in said and "Edge9" in said, "the failure does not name the mixed edges: %s" % said
