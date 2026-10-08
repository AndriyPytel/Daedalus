"""The Acceptance entries a number alone settles are checked by generated code, and the model is asked only for the rest."""
import json
import os
from collections import Counter

import pytest

from core.loop.mechanical import REASONS, classified, covered_entries, the_collateral_measured, the_sizes_measured

_RECORDED = os.path.join(os.path.dirname(__file__), "fixtures", "recorded_acceptance.json")
_SIX_KINDS = ("envelope", "diameter", "inner_diameter", "outer_diameter", "radius", "axis", "count")
_ARROW = " \u2192 "

_DATUMS = {"datums": [
    {"name": "the left end face", "fixes": "the length", "is": "the plane x = -52.2884, facing -x"},
    {"name": "the right end face", "fixes": "the length", "is": "the extreme +x of the part, at x = 61.529"},
    {"name": "the top face", "fixes": "the height", "is": "the highest plane of the part, z = 15.9735"},
    {"name": "the front face", "fixes": "the depth front to rear", "is": "the extreme -y, at y = -42.0"},
    {"name": "the bore axis", "fixes": "the depth front to rear and the height",
     "is": "parallel to the length, at y = 19.24, z = -4.30"},
]}


def covered(entries, base=None, description=None):
    return covered_entries(entries, _DATUMS if description is None else description, base)


@pytest.mark.parametrize("entry", [
    "features \u2192 hole-bottom \u2192 diameter: \u00d850",
    "features \u2192 hole-left \u2192 diameter: 80 mm",
    "features \u2192 hole-left \u2192 diameter: 30 mm (read)",
    "features \u2192 boss-bottom \u2192 outer_diameter: \u00d870",
    "features \u2192 boss-centre \u2192 inner_diameter: \u00d880",
    "features \u2192 fillet-left \u2192 radius: R0.5",
    "features \u2192 fillet-outer \u2192 radius: 14 mm (derived)",
    "features \u2192 boss-bottom \u2192 diameter: \u00d850 bore",
    "features \u2192 boss-left \u2192 outer_diameter: \u00d8100 mm outer circle",
    "features \u2192 hole-left \u2192 diameter: 80 mm at its narrowest (100, 162 and 200 mm are on the same axis)",
    "features \u2192 fillet-outer \u2192 radius: 14 mm (was 18 mm)",
    "features \u2192 hole-left \u2192 diameter: about 9.2 mm",
    "features \u2192 hole-centre \u2192 diameter: \u00d84 / \u00d86 / \u00d810 as listed in the_base_part",
    "features \u2192 fillet-inner \u2192 radius: R2 inside, R4 outside",
    "features \u2192 cutout-rear \u2192 radius: R173 and R170",
])
def test_a_size_entry_with_its_number_first_is_checked_in_code(entry):
    assert covered([entry]) == [entry]
    assert the_sizes_measured([entry], _DATUMS)


@pytest.mark.parametrize("entry", [
    "features \u2192 hole-left \u2192 diameter: not read",
    "features \u2192 hole-left \u2192 diameter: R40",
    "features \u2192 hole-left \u2192 diameter: 12 mm and 14 mm",
    "features \u2192 boss-left \u2192 radius: the corners keep the base part's radius (derived)",
    "features \u2192 hole-left \u2192 position \u2192 the left end face: 20 mm",
    "features \u2192 tab-top \u2192 count: 2 (read)",
])
def test_an_entry_that_needs_a_guess_is_left_to_the_model(entry):
    assert covered([entry]) == []
    assert the_sizes_measured([entry], _DATUMS) == ""


def test_a_size_Design_holds_as_a_number_is_taken_from_it_and_prose_that_disagrees_is_left_alone():
    model = {"features": [{"id": "hole-a", "diameter": {"value": "twelve", "mm": 12.0, "flag": "read"}},
                          {"id": "hole-b", "diameter": {"value": "13 mm", "mm": 12.0, "flag": "read"}}]}
    taken = "features \u2192 hole-a \u2192 diameter: about twelve"
    clash = "features \u2192 hole-b \u2192 diameter: 13 mm"
    assert covered([taken, clash], description=model) == [taken]
    assert "12.0" in the_sizes_measured([taken], model)


def test_an_axis_and_a_count_ride_on_the_size_of_the_same_feature():
    size = "features \u2192 hole-centre-4x \u2192 diameter: 40 mm"
    axis = "features \u2192 hole-centre-4x \u2192 axis: parallel to the height (derived)"
    count = "features \u2192 hole-centre-4x \u2192 count: 4 (derived)"
    assert sorted(covered([size, axis, count])) == sorted([size, axis, count])


def test_an_axis_or_a_count_with_no_size_beside_it_is_left_to_the_model():
    entries = ["features \u2192 hole-a \u2192 axis: parallel to the height",
               "features \u2192 hole-a \u2192 count: 4"]
    assert covered(entries) == []
    assert set(classified(entries, _DATUMS, None).values()) == {REASONS["no size"]}


def test_an_axis_named_by_a_dimension_resolves_through_the_datums_that_fix_it():
    size = "features \u2192 hole-a \u2192 diameter: 10 mm"
    script = the_sizes_measured(
        [size, "features \u2192 hole-a \u2192 axis: parallel to the length, on the boss's centre"], _DATUMS)
    assert "'x'" in script


@pytest.mark.parametrize("prose,letter", [
    ("parallel to the bore axis", "x"),
    ("coaxial with the bore axis", "x"),
    ("perpendicular to the top face", "z"),
    ("along the bore axis, perpendicular to the left end face", "x"),
    ("parallel to the Document's Y axis (derived)", "y"),
    ("parallel to the height, axis (0,0,1) (derived)", "z"),
])
def test_an_axis_the_datums_settle_is_checked_along_that_global_letter(prose, letter):
    size = "features \u2192 hole-a \u2192 diameter: 10 mm"
    entry = "features \u2192 hole-a \u2192 axis: " + prose
    assert covered([size, entry]) == [size, entry]
    assert "'%s'" % letter in the_sizes_measured([size, entry], _DATUMS)


@pytest.mark.parametrize("prose", [
    "parallel to the width",
    "parallel to the bore axis, along the height",
    "along the length and the Document's Y axis",
    "perpendicular to the plate each hole is in (derived)",
    "perpendicular to the ring axis, radial",
])
def test_an_axis_that_names_no_single_global_direction_is_left_to_the_model(prose):
    size = "features \u2192 hole-a \u2192 diameter: 10 mm"
    assert covered([size, "features \u2192 hole-a \u2192 axis: " + prose]) == [size]


def test_four_or_five_explicit_envelope_entries_are_checked_in_code():
    entries = ["envelope \u2192 length: 80 mm between the ends (read)",
               "envelope \u2192 depth front to rear: 30 mm (read)",
               "envelope \u2192 width: 30 mm front to rear, unchanged",
               "envelope \u2192 height: 40 mm",
               "envelope \u2192 depth left to right: 30 mm"]
    assert covered(entries) == entries


def test_an_envelope_value_comes_from_designs_number_when_it_has_one():
    model = {"envelope": {"length": {"value": "as long as the arm", "mm": 80.0, "flag": "derived"}}}
    entry = "envelope \u2192 length: as long as the arm (derived)"
    assert covered([entry], description=model) == [entry]


def test_an_envelope_entry_with_no_number_or_not_a_side_is_left_to_the_model():
    entries = ["envelope \u2192 length: as long as the arm reaches (assumed)",
               "envelope \u2192 thickness: 2 mm sheet thickness throughout"]
    assert covered(entries) == []


_UNCHANGED = "features \u2192 hole-right \u2192 topology: unchanged by the edit (derived)"
_SECOND_CLAIMS = [
    "features \u2192 pad-top \u2192 direction: moves 5 mm up in the height, to z 7.613..9.613, carrying the rear flange unchanged",
    "features \u2192 ear-right \u2192 position \u2192 the top face: 5 mm further below the top face than in the_base_part; shape unchanged",
    "acceptance_note: volume about 2.10661e6 mm3, about 540 mm3 less than the base part; bounding box unchanged; bottom face becomes an annulus \u00d864..\u00d856 (derived)",
    "features \u2192 pad-outer \u2192 position \u2192 the front face: 0 mm, unmoved",
    "features \u2192 boss-left \u2192 position \u2192 the right end face: the root plane lies 105.818 mm from the right end face; the edit leaves it unchanged",
]


def test_an_entry_whose_whole_claim_is_unchanged_is_checked_through_no_collateral_on_an_edit():
    assert covered([_UNCHANGED], base={"volume": 1.0}) == [_UNCHANGED]
    assert "no_collateral" in the_collateral_measured([_UNCHANGED], {"volume": 1.0})


def test_an_unchanged_entry_with_no_base_part_is_left_to_the_model():
    assert covered([_UNCHANGED]) == []


@pytest.mark.parametrize("entry", _SECOND_CLAIMS)
def test_an_entry_that_says_unchanged_and_something_else_is_left_to_the_model(entry):
    assert covered([entry], base={"volume": 1.0}) == []


def test_an_explicit_volume_and_a_box_that_does_not_move_become_arguments_of_no_collateral():
    entry = "acceptance_note: about 540 mm3 less than the base part; bounding box unchanged"
    assert covered([entry], base={"volume": 1.0}) == [entry]
    script = the_collateral_measured([entry], {"volume": 1.0})
    assert "volume_change=-540.0" in script and "moved=()" in script


def _kind(entry):
    parts = entry.partition(": ")[0].split(_ARROW)
    if parts[0] == "envelope":
        return "envelope"
    return parts[-1] if parts[-1] in _SIX_KINDS else "other"


def _recorded():
    with open(_RECORDED, encoding="utf-8") as recorded:
        return json.load(recorded)


def test_every_recorded_entry_of_the_six_kinds_is_generated_or_says_why_it_needs_a_guess():
    runs = _recorded()
    total, generated, left = Counter(), Counter(), Counter()
    for run in runs:
        verdicts = classified(run["acceptance"], run["description"], {"volume": 1.0})
        assert list(verdicts) == run["acceptance"]
        for entry, why in verdicts.items():
            kind = _kind(entry)
            if kind == "other":
                continue
            total[kind] += 1
            generated[kind] += why is None
            if why:
                left[why] += 1
            assert why is None or why in REASONS.values(), (entry, why)
    assert sum(total.values()) == sum(1 for run in runs for entry in run["acceptance"] if _kind(entry) != "other")
    assert dict(left) == {REASONS["no size"]: 26, REASONS["axis"]: 1, REASONS["no number"]: 2, REASONS["not a side"]: 2}
    assert sum(generated.values()) == sum(total.values()) - sum(left.values())


def test_a_no_size_reason_is_only_given_to_an_axis_or_a_count_whose_feature_has_no_size_entry():
    for run in _recorded():
        sized = {entry.split(_ARROW)[1] for entry in run["acceptance"]
                 if _kind(entry) in ("diameter", "inner_diameter", "outer_diameter", "radius")
                 and classified([entry], run["description"], None)[entry] is None}
        for entry, why in classified(run["acceptance"], run["description"], None).items():
            if why == REASONS["no size"]:
                assert entry.split(_ARROW)[1] not in sized, entry
