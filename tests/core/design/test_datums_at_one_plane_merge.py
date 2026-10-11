"""Two datums that are one plane of the base part become one datum in the merge."""
from core.design.merge import merge

from tests.loop_helpers import read


def _datum(name, said):
    return {"name": name, "is": said, "fixes": "the depth front to rear", "proves": "40 mm to the rear face"}


def test_two_datums_at_one_plane_address_become_one_and_the_position_points_to_it():
    offered = {
        "datums": [_datum("plane y = -177.5", "the planar flange face"),
                   _datum("plane y=-177.50", "the mounting flange face")],
        "features": [{"id": "boss-a", "kind": "boss",
                      "position": {"plane y = -177.5": read("140 mm"),
                                   "plane y=-177.50": read("140 mm")}}],
    }

    merged = merge({}, offered)

    assert [datum["name"] for datum in merged["datums"]] == ["plane y = -177.5"]
    assert list(merged["features"][0]["position"]) == ["plane y = -177.5"]
    assert not [conflict for conflict in merged.get("conflicts", ()) if not conflict.get("machine_written")]


def test_two_readings_naming_one_plane_differently_give_a_feature_one_position_from_it():
    first = {"datums": [_datum("plane y = -177.5", "the bottom face")],
             "features": [{"id": "boss-a", "kind": "boss", "position": {"plane y = -177.5": read("160 mm")}}]}
    second = {"datums": [_datum("plane y = -177.500", "the front face")],
              "features": [{"id": "boss-a", "kind": "boss", "position": {"plane y = -177.500": read("160 mm")}}]}

    merged = merge(merge({}, first), second)

    assert len(merged["datums"]) == 1
    assert list(merged["features"][0]["position"]) == ["plane y = -177.5"]


def test_datums_at_different_planes_or_not_named_by_an_address_stay_apart():
    offered = {"datums": [_datum("plane y = -177.5", "a"), _datum("plane y = -17.5", "b"),
                          _datum("the front face", "c")]}

    assert [datum["name"] for datum in merge({}, offered)["datums"]] == \
        ["plane y = -177.5", "plane y = -17.5", "the front face"]


def test_a_feature_placed_from_one_plane_twice_with_different_values_is_one_position_and_a_conflict():
    offered = {"datums": [_datum("plane y = -177.5", "a"), _datum("plane y = -177.50", "b")],
               "features": [{"id": "boss-a", "kind": "boss",
                             "position": {"plane y = -177.5": read("140 mm"),
                                          "plane y = -177.50": read("150 mm")}}]}

    merged = merge({}, offered)

    assert list(merged["features"][0]["position"]) == ["plane y = -177.5"]
    assert any(conflict["about"] == "features → boss-a → position → plane y = -177.5"
               for conflict in merged["conflicts"])
