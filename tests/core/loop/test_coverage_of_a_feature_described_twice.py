"""Where nobody answers, a Plan covers a feature described twice by citing either of the two."""
from core.design.gaps import entries
from core.loop.plan import _unconsumed
from core.nobody import nobody_answering


def _bore(id):
    return {"id": id, "kind": "hole",
            "diameter": {"value": "40 mm", "flag": "read", "mm": 40},
            "axis": {"value": "along the depth", "flag": "read"},
            "position": {"the left end face": {"value": "20 mm", "flag": "read", "mm": 20}}}


def test_citing_one_of_two_features_that_are_one_placement_leaves_nothing_unplanned():
    part = {"features": [_bore("bore-left"), _bore("bore-near-left")]}
    cited = [entry["says"] for entry in entries(part) if "bore-left" in entry["about"]]

    with nobody_answering():
        assert _unconsumed(part, cited) == []
    assert _unconsumed(part, cited)
