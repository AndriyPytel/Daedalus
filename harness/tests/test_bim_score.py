
import copy

import pytest

import assembly_score
import bim_fixtures as fx
import bim_score

needs_freecad = pytest.mark.skipif(not __import__("os").path.exists(assembly_score.FREECADCMD), reason="freecadcmd missing")

TYPES = ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcSpace")


def measurement(walls=((0, 0, 4.0, 0.2, 2.6), (4, 0, 4.0, 0.2, 2.6)), spaces=((2, 2, 10.0), (6, 2, 10.0)), openings=(("IfcDoor", 2, 0, 0), ("IfcWindow", 6, 0, 1)), edges=((0, 1),)):
    return {
        "opens": True,
        "counts": {
            "IfcWall": len(walls), "IfcSlab": 1, "IfcSpace": len(spaces),
            "IfcDoor": sum(o[0] == "IfcDoor" for o in openings), "IfcWindow": sum(o[0] == "IfcWindow" for o in openings),
        },
        "walls": [{"c": [x, y], "len": ln, "thk": t, "h": h} for x, y, ln, t, h in walls],
        "spaces": [{"c": [x, y], "area": a} for x, y, a in spaces],
        "openings": [{"type": t, "c": [x, y], "host": host, "w": w, "h": 2.0} for t, x, y, host, *size in openings for w in [size[0] if size else 1.0]],
        "adjacency": [list(e) for e in edges],
    }


def test_reference_against_itself_is_one_in_every_component():
    ref = measurement()
    result = bim_score.combine(ref, ref)
    assert result["score"] == 1.0
    assert {result[k] for k in bim_score.COMPONENTS} == {1.0}


def test_candidate_that_does_not_open_scores_zero():
    assert bim_score.combine({"opens": False}, measurement())["score"] == 0.0


def test_missing_door_lowers_counts_and_hosting_only():
    ref = measurement()
    cand = measurement(openings=(("IfcWindow", 6, 0, 1),))
    result = bim_score.combine(cand, ref)
    assert result["counts"] < 1 and result["hosting"] < 1
    assert (result["walls"], result["spaces"], result["adjacency"]) == (1.0, 1.0, 1.0)


def test_moved_wall_lowers_walls_and_hosting():
    ref = measurement()
    cand = measurement(walls=((0, 0, 4.0, 0.2, 2.6), (4, 3.0, 4.0, 0.2, 2.6)))
    result = bim_score.combine(cand, ref)
    assert result["walls"] < 1 and result["hosting"] < 1 and result["counts"] == 1.0


def test_wrong_space_area_lowers_only_spaces():
    ref = measurement()
    cand = measurement(spaces=((2, 2, 6.0), (6, 2, 10.0)))
    result = bim_score.combine(cand, ref)
    assert 0 < result["spaces"] < 1
    assert (result["counts"], result["walls"], result["hosting"], result["adjacency"]) == (1.0, 1.0, 1.0, 1.0)


def test_unhosted_window_lowers_only_hosting():
    ref = measurement()
    cand = measurement(openings=(("IfcDoor", 2, 0, 0), ("IfcWindow", 6, 0, None)))
    result = bim_score.combine(cand, ref)
    assert result["hosting"] < 1
    assert (result["counts"], result["walls"], result["spaces"], result["openings"], result["adjacency"]) == (1.0, 1.0, 1.0, 1.0, 1.0)


def test_resized_window_lowers_only_the_openings_component():
    ref = measurement()
    cand = measurement(openings=(("IfcDoor", 2, 0, 0), ("IfcWindow", 6, 0, 1, 1.5)))
    result = bim_score.combine(cand, ref)
    assert 0 < result["openings"] < 1
    assert (result["counts"], result["walls"], result["spaces"], result["hosting"], result["adjacency"]) == (1.0, 1.0, 1.0, 1.0, 1.0)


def test_walls_shifted_seven_tenths_of_a_metre_score_zero_overall():
    ref = measurement()
    cand = measurement(walls=((0, 0.7, 4.0, 0.2, 2.6), (4, 0.7, 4.0, 0.2, 2.6)))
    result = bim_score.combine(cand, ref)
    assert result["walls"] == 0.0 and result["score"] == 0.0


def test_walls_shifted_a_tenth_of_a_metre_score_clearly_below_one_overall():
    ref = measurement()
    cand = measurement(walls=((0, 0.1, 4.0, 0.2, 2.6), (4, 0.1, 4.0, 0.2, 2.6)))
    result = bim_score.combine(cand, ref)
    assert result["walls"] == pytest.approx(0.6) and result["score"] == pytest.approx(0.6)


def test_score_is_the_walls_component_times_the_mean_of_the_other_five():
    ref = measurement()
    cand = measurement(walls=((0, 0.1, 4.0, 0.2, 2.6), (4, 0.1, 4.0, 0.2, 2.6)), edges=())
    result = bim_score.combine(cand, ref)
    others = [result[k] for k in bim_score.COMPONENTS if k != "walls"]
    assert result["score"] == pytest.approx(result["walls"] * sum(others) / 5) and 0 < result["score"] < 0.6


def test_wall_shift_loses_score_linearly_up_to_a_quarter_metre():
    ref = measurement()
    near = bim_score.combine(measurement(walls=((0, 0.125, 4.0, 0.2, 2.6), (4, 0, 4.0, 0.2, 2.6))), ref)["walls"]
    far = bim_score.combine(measurement(walls=((0, 0.25, 4.0, 0.2, 2.6), (4, 0, 4.0, 0.2, 2.6))), ref)["walls"]
    assert 1.0 > near > far


def test_edit_score_is_zero_for_the_unedited_input_and_one_for_the_expected_model():
    original = measurement()
    expected = measurement(openings=(("IfcDoor", 2, 0, 0), ("IfcWindow", 6, 0, 1), ("IfcDoor", 5, 0, 1)))
    assert bim_score.edit_combine(original, expected, original)["score"] == 0.0
    assert bim_score.edit_combine(expected, expected, original)["score"] == 1.0


def test_edit_score_is_the_gain_over_the_input_relative_to_the_headroom():
    original = measurement()
    expected = measurement(spaces=((2, 2, 10.0), (6, 2, 20.0)))
    half = measurement(spaces=((2, 2, 10.0), (6, 2, 15.0)))
    headroom = 1 - bim_score.combine(original, expected)["score"]
    gain = bim_score.combine(half, expected)["score"] - bim_score.combine(original, expected)["score"]
    assert bim_score.edit_combine(half, expected, original)["score"] == pytest.approx(gain / headroom)
    assert 0 < gain / headroom < 1


def test_edit_score_never_goes_below_zero():
    original = measurement()
    expected = measurement(spaces=((2, 2, 10.0), (6, 2, 20.0)))
    worse = measurement(spaces=((2, 2, 1.0), (6, 2, 1.0)))
    assert bim_score.edit_combine(worse, expected, original)["score"] == 0.0
    assert bim_score.edit_combine({"opens": False}, expected, original)["score"] == 0.0


def test_missing_adjacency_edge_lowers_only_adjacency():
    ref = measurement()
    result = bim_score.combine(measurement(edges=()), ref)
    assert result["adjacency"] == 0.0 and result["spaces"] == 1.0


def test_no_edges_in_either_is_full_adjacency():
    ref = measurement(edges=())
    assert bim_score.combine(ref, ref)["adjacency"] == 1.0


def test_combine_is_deterministic_and_independent_of_element_order():
    ref = measurement()
    cand = measurement(walls=((4, 0, 4.0, 0.2, 2.6), (0, 0, 3.0, 0.2, 2.6)))
    cand_shuffled = copy.deepcopy(cand)
    cand_shuffled["walls"].reverse()
    first = bim_score.combine(cand, ref)
    assert first == bim_score.combine(cand, ref)
    assert first["walls"] == bim_score.combine(cand_shuffled, ref)["walls"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not __import__("os").path.exists(assembly_score.FREECADCMD):
        pytest.skip("freecadcmd missing")
    root = tmp_path_factory.mktemp("bim")
    base = fx.flat()
    variants = {
        "reference": base,
        "missing_door": fx.without_door(base),
        "moved_wall": fx.with_moved_wall(base),
        "wrong_area": fx.with_wrong_area(base),
        "unhosted_window": fx.with_unhosted_window(base),
        "wide_window": fx.with_wide_window(base),
        "shifted_walls": fx.with_shifted_walls(base),
    }
    bim_score.build([(spec, root / f"{name}.FCStd") for name, spec in variants.items()])
    (root / "broken.ifc").write_text("ISO-10303-21;\nHEADER;\nnot an ifc file")
    paths = {name: root / f"{name}.ifc" for name in [*variants, "broken"]}
    measured = dict(zip(paths, bim_score.measure(list(paths.values()))))
    return paths, measured


@needs_freecad
def test_built_reference_holds_every_element_type_with_hosting_and_adjacency(built):
    _, measured = built
    ref = measured["reference"]
    assert ref["counts"] == {"IfcWall": 6, "IfcSlab": 1, "IfcDoor": 2, "IfcWindow": 2, "IfcSpace": 3}
    assert all(o["host"] is not None for o in ref["openings"])
    assert sorted(map(tuple, ref["adjacency"])) == [(0, 1), (0, 2), (1, 2)]
    areas = sorted(round(s["area"], 1) for s in ref["spaces"])
    assert areas == [8.7, 8.7, 18.2]


@needs_freecad
def test_hosting_is_read_from_the_voids_freecad_writes_not_from_fills(built):
    import re

    text = built[0]["reference"].read_text()
    assert len(re.findall(r"IFCRELVOIDSELEMENT", text)) == 4
    assert "IFCRELFILLSELEMENT" not in text
    hosts = {o["type"]: o["host"] for o in built[1]["reference"]["openings"]}
    assert hosts["IfcDoor"] is not None and hosts["IfcWindow"] is not None


@needs_freecad
def test_reference_scores_one_against_itself(built):
    _, measured = built
    assert bim_score.combine(measured["reference"], measured["reference"])["score"] == 1.0


@needs_freecad
@pytest.mark.parametrize("name", ["missing_door", "moved_wall", "wrong_area", "unhosted_window", "wide_window"])
def test_perturbed_model_scores_below_one(built, name):
    _, measured = built
    assert bim_score.combine(measured[name], measured["reference"])["score"] < 1.0


@needs_freecad
def test_broken_ifc_scores_zero_and_repeat_gives_the_same_score(built):
    paths, _ = built
    first = bim_score.score(paths["broken"], paths["reference"])
    assert first["score"] == 0.0
    one = bim_score.score(paths["moved_wall"], paths["reference"])
    assert one == bim_score.score(paths["moved_wall"], paths["reference"])


@needs_freecad
def test_built_shifted_walls_score_clearly_lower_on_walls(built):
    _, measured = built
    assert bim_score.combine(measured["shifted_walls"], measured["reference"])["score"] == 0.0


@needs_freecad
def test_built_edit_scores_no_op_zero_and_expected_one(built):
    paths, _ = built
    noop = bim_score.score_edit(paths["reference"], paths["moved_wall"], paths["reference"])
    done = bim_score.score_edit(paths["moved_wall"], paths["moved_wall"], paths["reference"])
    assert (noop["score"], done["score"]) == (0.0, 1.0)
