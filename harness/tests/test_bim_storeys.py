import copy

import pytest

import assembly_score
import bim_fixtures as fx
import bim_score
import bim_tasks
from test_bim_score import measurement

needs_freecad = pytest.mark.skipif(not __import__("os").path.exists(assembly_score.FREECADCMD), reason="freecadcmd missing")
HEIGHT = 2.6


def stacked(count, stairs=True, drop=()):
    """A measurement of *count* storeys of the same flat, a stair in the gap between each pair unless *stairs* is off."""
    flat = measurement()
    levels = [{"elevation": round(i * HEIGHT, 3), "opens": True, **flat} for i in range(count) if i not in drop]
    return {**flat, "storeys": levels, "stairs": [{"c": [1.5, 1.5], "z": round(i * HEIGHT, 3), "rise": 2.4} for i in range(count - 1)] if stairs else []}


def test_a_building_scores_one_against_itself_in_storeys_and_stairs():
    result = bim_score.combine_storeys(stacked(3), stacked(3))
    assert result == {"score": 1.0, "storeys": 1.0, "stairs": 1.0}


def test_do_nothing_and_a_file_that_does_not_open_score_zero():
    assert bim_score.combine_storeys({"opens": False}, stacked(3))["score"] == 0.0
    empty = {"opens": True, "counts": dict.fromkeys(bim_score.TYPES, 0), "walls": [], "spaces": [], "openings": [], "adjacency": [], "storeys": [], "stairs": []}
    assert bim_score.combine_storeys(empty, stacked(3))["score"] == 0.0


def test_one_storey_without_stairs_scores_low_though_its_flat_is_right():
    result = bim_score.combine_storeys(measurement() | {"storeys": [], "stairs": []}, stacked(3))
    assert result["stairs"] == 0.0 and result["score"] == 0.0 and result["storeys"] == pytest.approx(1 / 3)


def test_the_flat_repeated_without_stairs_scores_zero_with_full_storeys():
    result = bim_score.combine_storeys(stacked(3, stairs=False), stacked(3))
    assert result["storeys"] == 1.0 and result["score"] == 0.0


def test_a_missing_storey_costs_its_share_of_the_mean():
    result = bim_score.combine_storeys(stacked(3, drop=(2,)), stacked(3))
    assert result["storeys"] == pytest.approx(2 / 3) and result["stairs"] == 1.0


def test_a_storey_at_the_wrong_elevation_matches_nothing():
    cand = stacked(2)
    cand["storeys"][1]["elevation"] += 1.0
    assert bim_score.combine_storeys(cand, stacked(2))["storeys"] == pytest.approx(0.5)


def test_a_stair_is_matched_by_position_within_a_third_of_a_metre_and_by_rise():
    near = stacked(2)
    near["stairs"][0]["c"] = [1.7, 1.5]
    far = stacked(2)
    far["stairs"][0]["c"] = [1.9, 1.5]
    short = stacked(2)
    short["stairs"][0]["rise"] = 1.2
    assert bim_score.combine_storeys(near, stacked(2))["stairs"] == 1.0
    assert bim_score.combine_storeys(far, stacked(2))["stairs"] == 0.0
    assert bim_score.combine_storeys(short, stacked(2))["stairs"] == pytest.approx(0.5)


def test_the_score_is_the_storeys_mean_times_the_stairs_factor():
    cand = stacked(3, drop=(2,))
    cand["stairs"].pop()
    result = bim_score.combine_storeys(cand, stacked(3))
    assert result["score"] == pytest.approx(result["storeys"] * result["stairs"]) and 0 < result["score"] < 1


def test_a_stair_in_the_wrong_gap_does_not_match():
    cand = stacked(3)
    cand["stairs"] = [{"c": [1.5, 1.5], "z": 0.0, "rise": 2.4}, {"c": [1.5, 1.5], "z": 0.0, "rise": 2.4}]
    assert bim_score.combine_storeys(cand, stacked(3))["stairs"] == pytest.approx(0.5)


def test_storeys_of_a_flat_stack_the_flat_and_put_a_stair_in_each_gap():
    spec = bim_tasks.storeys_spec(fx.flat(), 3)
    assert spec["storeys"] == 3 and len(spec["stairs"]) == 2
    assert [s["from"] for s in spec["stairs"]] == [0, 1]
    assert spec["walls"] == fx.flat()["walls"]


def test_the_stair_lies_wholly_inside_the_named_space_and_rises_one_storey():
    spec = bim_tasks.storeys_spec(fx.flat(), 2)
    stair = spec["stairs"][0]
    room = next(s for s in spec["spaces"] if s["id"] == stair["space"])
    (x, y), (dx, dy) = stair["start"], stair["dir"]
    half = stair["w"] / 2
    corners = [(x + dy * half * sign + dx * stair["len"] * t, y - dx * half * sign + dy * stair["len"] * t) for sign in (-1, 1) for t in (0, 1)]
    assert all(bim_tasks._inside(room["poly"], c) for c in corners)
    assert stair["steps"] * 0.19 >= spec["storey_height"] > (stair["steps"] - 1) * 0.19
    assert stair["len"] == pytest.approx((stair["steps"] - 1) * bim_tasks.TREAD)


def test_the_text_gives_the_storeys_the_height_and_the_stair_footprint():
    spec = bim_tasks.storeys_spec(fx.flat(), 3)
    text = bim_tasks.storeys_text(spec)
    stair = spec["stairs"][0]
    assert "3 storeys of 2.60 m each" in text
    assert f"Space_{stair['space']}" in text and f"{stair['w']:.2f} m wide" in text
    assert f"({stair['start'][0]:.2f}, {stair['start'][1]:.2f})" in text


def test_the_storey_count_is_two_to_four_and_the_same_for_the_same_task():
    counts = [bim_tasks.storey_count(n) for n in range(1, 21)]
    assert set(counts) == {2, 3, 4} and counts == [bim_tasks.storey_count(n) for n in range(1, 21)]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("storeys")
    spec = bim_tasks.storeys_spec(fx.flat(), 3)
    one = copy.deepcopy(spec) | {"storeys": 1, "stairs": []}
    bim_score.build([(spec, root / "three.FCStd"), (one, root / "one.FCStd")])
    paths = [root / "three.ifc", root / "one.ifc"]
    return spec, dict(zip(("three", "one"), bim_score.measure(paths)))


@needs_freecad
def test_a_built_building_measures_a_storey_per_level_and_a_stair_per_gap(built):
    spec, measured = built
    three = measured["three"]
    assert [s["elevation"] for s in three["storeys"]] == pytest.approx([0.0, HEIGHT, 2 * HEIGHT])
    assert all(s["counts"] == {"IfcWall": 6, "IfcSlab": 1, "IfcDoor": 2, "IfcWindow": 2, "IfcSpace": 3} for s in three["storeys"])
    assert [s["z"] for s in three["stairs"]] == pytest.approx([0.0, HEIGHT], abs=1e-3)
    stair = spec["stairs"][0]
    assert three["stairs"][0]["c"] == pytest.approx([stair["start"][0] + stair["dir"][0] * stair["len"] / 2, stair["start"][1] + stair["dir"][1] * stair["len"] / 2], abs=0.01)


@needs_freecad
def test_a_built_building_scores_one_against_itself_and_a_single_storey_scores_low(built):
    _, measured = built
    assert bim_score.combine_storeys(measured["three"], measured["three"])["score"] == 1.0
    assert bim_score.combine_storeys(measured["one"], measured["three"])["score"] < 0.1
