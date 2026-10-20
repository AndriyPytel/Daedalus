"""Where nobody answers, the merge compares what two readings count and measure, not how they word it."""
from core.design.merge import merge
from core.nobody import nobody_answering


def _hole(id, mm, because="", axis="z", at=20, **more):
    return {"id": id, "kind": "hole", "region": because or "the arm",
            "diameter": {"value": "%g mm" % mm, "flag": "read", "mm": mm,
                         "from": because or "the top view"},
            "axis": {"value": axis, "flag": "read"},
            "position": {"the left end face": {"value": "%g mm" % at, "flag": "read", "mm": at}},
            **more}


def test_the_same_numbers_in_other_words_are_no_conflict_and_other_numbers_still_are():
    held = {"features": [_hole("bore-left", 40)]}
    with nobody_answering():
        reworded = merge(held, {"features": [
            _hole("bore-left", 40, because="callout beside the left bore", axis="Z axis")]})
        other = merge(held, {"features": [_hole("bore-left", 36)]})

    assert "conflicts" not in reworded
    assert [conflict["about"] for conflict in other["conflicts"]] == \
        ["features \u2192 bore-left \u2192 diameter"]


def test_one_feature_read_under_two_ids_is_one_feature():
    with nobody_answering():
        grown = merge({"features": [_hole("boss-right-lower", 40)]},
                      {"features": [_hole("boss-bottom-right", 40, because="again")]})

    assert [feature["id"] for feature in grown["features"]] == ["boss-right-lower"]
    assert "conflicts" not in grown


def test_a_correction_replaces_the_value_and_keeps_the_old_one_under_was():
    held = {"features": [_hole("bore-left", 40)]}
    correction = {"features": [{"id": "bore-left", "diameter": {
        "value": "36 mm", "flag": "read", "mm": 36, "from": "the top view",
        "corrects": "the callout reads 36"}}]}
    with nobody_answering():
        grown = merge(held, correction)

    diameter = grown["features"][0]["diameter"]
    assert (diameter["mm"], diameter["was"], diameter["because"]) == \
        (36, "40 mm", "the callout reads 36")
    assert "corrects" not in diameter and "conflicts" not in grown
    assert merge(held, correction)["features"][0]["diameter"]["mm"] == 40
