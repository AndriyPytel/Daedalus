"""Level 1 — how far into a revised Plan `_carried_over` inherits already-applied Steps."""

from core.loop.helpers import _carried_over


def test_a_wording_only_revision_carries_a_reworded_step_that_keeps_its_operation():
    """
    Execute handing a later Step back unbuilt does not call the earlier,
    applied Steps into question, so a revision that only rewords one of
    them -- Boss stays Boss, Rib stays Rib -- must carry it, not redeclare it.
    """
    previous = ["Pad the outline", "Boss the centre", "Rib the centre",
                "Fillet the corner"]
    revised = ["Pad a cleaner outline", "Boss a rounder centre",
               "Rib the tidier centre", "Fillet the corner, R2"]

    assert _carried_over(previous, revised, applied=3, wording_only=True) == 3


def test_a_wording_only_revision_still_matches_unchanged_wording():
    previous = ["Pad the outline", "Fillet the marked edges"]
    steps = ["Pad the outline", "Fillet the marked edges",
             "Drill a 6mm hole", "Chamfer the edge"]

    assert _carried_over(previous, steps, applied=2, wording_only=True) == 2


def test_a_wording_only_revision_stops_where_it_swaps_the_operation():
    """
    A revision that replaces an applied Step with a different one -- not a
    reword of the same build, a different operation standing in its place
    -- must not be carried: that Step still has to be applied for real.
    """
    previous = ["First", "Impossible"]
    revised = ["Second", "Impossible again"]

    assert _carried_over(previous, revised, applied=1, wording_only=True) == 0


def test_carried_over_stays_exact_match_by_default():
    """
    Every cause but Execute's own unbuilt handback still calls the earlier
    Steps into question -- a failed acceptance check, a rejected review, an
    obsolete Document -- so a revision under any of them still rebuilds a
    Step whose wording moved, exact match, same as before this ticket.
    """
    previous = ["Pocket the split"]
    revised = ["Pocket the split again"]

    assert _carried_over(previous, revised, applied=1) == 0
    assert _carried_over(previous, revised, applied=1, wording_only=False) == 0


def test_carried_over_never_exceeds_the_revised_plans_own_length():
    """A revision that collapses Steps cannot be asked to carry more than it has."""
    previous = ["Pad the outline", "Boss the centre", "Rib the centre"]
    steps = ["Pad the outline"]

    assert _carried_over(previous, steps, applied=3) == 1


def test_carried_over_is_zero_with_nothing_applied_yet():
    assert _carried_over(["Pad the outline"], ["Pad the outline"], applied=0) == 0


def test_a_wording_only_revision_stops_when_a_numeral_moves():
    """
    Sharing a leading word is not enough: a Step whose diameter, length or
    count changed is a different Step wearing the same verb, and carrying it
    would leave the Document holding geometry the revised Plan no longer
    describes -- a mismatch nothing downstream would ever notice.
    """
    previous = ["Sketch a \u224820 circle and pad it 20 mm"]
    revised = ["Sketch a \u224824 circle and pad it 26 mm"]

    assert _carried_over(previous, revised, applied=1, wording_only=True) == 0
