"""Design keeps every number it read: a later page replaces nothing on one sheet, and never a number."""
from core.design.gaps import decided_for_you, entries
from core.design.merge import merge
from core.design.report import distilled

from tests.loop_helpers import read

A_FACE = "hole-a bottom face"
DEPTH = "12 mm from the %s" % A_FACE


def _the_part(seen_in, kind="hole"):
    return {
        "envelope": {"length": dict(read("100 mm"), mm=100)},
        "datums": [{"name": A_FACE}],
        "features": [
            {"id": "hole-a", "kind": kind, "superseded_by": "hole-b", "position": {}},
            {"id": "hole-b", "kind": kind, "seen_in": seen_in,
             "depth": dict(read(DEPTH), mm=12)},
        ],
    }


ONE_SHEET = _the_part(["VIEW A", "VIEW B"])
TWO_PAGES_REVISED = _the_part(["VIEW A, on page 1", "VIEW B, on page 2"],
                              kind="hole, Phase 2 revision")
TWO_PAGES_NO_MARK = _the_part(["VIEW A, on page 1", "VIEW B, on page 2"])


def _depth(model):
    return next(feature for feature in distilled(merge({}, model))["features"]
                if feature["id"] == "hole-b")["depth"]


def test_nothing_is_replaced_on_a_single_sheet_so_no_value_goes_to_was():
    """Fixtures 146 and 138 lost a depth and a bottom face on one sheet."""
    document = distilled(merge({}, ONE_SHEET))
    depth = _depth(ONE_SHEET)

    assert depth["mm"] == 12 and depth["value"] == DEPTH and "was" not in depth
    assert A_FACE in [datum["name"] for datum in document["datums"]]
    assert "was" not in str(document)
    assert not [said for said in decided_for_you(document) if "not " in said]


def test_two_pages_with_no_revision_mark_replace_nothing():
    depth = _depth(TWO_PAGES_NO_MARK)

    assert depth["mm"] == 12 and "was" not in depth


def test_a_replaced_feature_keeps_its_number_in_mm_flagged_assumed_with_a_because():
    depth = _depth(TWO_PAGES_REVISED)

    assert depth["mm"] == 12
    assert depth["flag"] == "assumed"
    assert "later page" in depth["because"]


def test_the_value_the_replaced_feature_lost_is_still_a_criterion():
    document = distilled(merge({}, TWO_PAGES_REVISED))

    assert [entry["about"] for entry in entries(document)
            if entry["flag"] == "assumed"] == ["features \u2192 hole-b \u2192 depth"]


def test_a_number_after_the_word_page_with_a_decimal_or_unit_is_not_a_page():
    """Fixture 138 reads `page 1.5 mm` in a callout: not page 1, and not a second page."""
    said = _the_part(["VIEW A, on page 1", "VIEW B, page 1.5 mm, Phase 2 revision"])

    assert _depth(said)["mm"] == 12 and "was" not in _depth(said)
