"""Level 1 — how far into a revised Plan `_carried_over` inherits already-applied Steps."""

from core.loop.helpers import _carried_over


def test_carried_over_trusts_the_applied_count_even_when_the_revised_wording_differs():
    """
    FCC-432: matched to the post-epic smoke, where a failure on Step 4
    triggered a revision and the revised Plan began again at Step 2 -
    whose objects were already standing in the Document. A revision
    inherits the applied Steps and continues from the first unapplied
    one (FCC-428 Q11); the run already knows how many landed, so a
    revision that only rewords an already-applied Step must still carry
    it, not redeclare it.
    """
    previous = ["Pad the outline", "Boss the centre", "Rib the centre",
                "Fillet the corner"]
    revised = ["Pad a cleaner outline", "Boss a rounder centre",
               "Rib the tidier centre", "Fillet the corner, R2"]

    assert _carried_over(applied=3, steps=revised) == 3


def test_carried_over_matches_a_revision_that_left_the_wording_alone():
    previous = ["Pad the outline", "Fillet the marked edges"]
    steps = ["Pad the outline", "Fillet the marked edges",
             "Drill a 6mm hole", "Chamfer the edge"]

    assert _carried_over(applied=2, steps=steps) == 2


def test_carried_over_never_exceeds_the_revised_plans_own_length():
    """A revision that collapses Steps cannot be asked to carry more than it has."""
    steps = ["Pad the outline"]

    assert _carried_over(applied=3, steps=steps) == 1


def test_carried_over_is_zero_with_nothing_applied_yet():
    assert _carried_over(applied=0, steps=["Pad the outline"]) == 0
