"""
Level 1 -- the Assertion of an edit on native IFC reads the facts of the saved IFC: the five kinds present, each Door and Window filling an opening in a Wall, every element in a storey, the schema IFC4, and a file unlike the input.
"""
from core.agent.bim import native_refusals

HOLDS = {"signature": "base", "schema": "IFC4", "storeys": 1, "fills": 3, "orphans": [], "unnamed": [], "labels": {"IfcWindow": ["Window_WIN1", "Window_WIN2"], "IfcDoor": ["Door_D1"]}, "openings": 3, "unfilled": [], "unplaced": [],
         "counts": {"IfcWall": 4, "IfcSlab": 1, "IfcDoor": 1, "IfcWindow": 2, "IfcSpace": 1}}


def test_a_file_that_differs_and_holds_everything_is_refused_nothing():
    assert native_refusals(HOLDS, unchanged="other") == []


def test_a_file_equal_to_the_input_is_refused():
    found = native_refusals(HOLDS, unchanged="base")

    assert len(found) == 1


def test_each_missing_kind_is_named():
    for kind in HOLDS["counts"]:
        found = native_refusals(HOLDS | {"counts": HOLDS["counts"] | {kind: 0}})

        assert len(found) == 1 and kind in found[0]


def test_a_door_or_window_with_no_opening_in_a_wall_to_fill_is_counted():
    found = native_refusals(HOLDS | {"openings": 2})

    assert len(found) == 1 and "3 Doors and Windows" in found[0] and "2 openings" in found[0]


def test_a_door_or_window_that_fills_no_opening_in_a_wall_is_named():
    found = native_refusals(HOLDS | {"unfilled": ["Window_WIN2"]})

    assert len(found) == 1 and "Window_WIN2" in found[0] and "opening" in found[0]


def test_an_element_outside_a_storey_is_named():
    found = native_refusals(HOLDS | {"unplaced": ["Wall_W3"]})

    assert len(found) == 1 and "Wall_W3" in found[0] and "storey" in found[0]


def test_a_schema_other_than_ifc4_is_refused():
    found = native_refusals(HOLDS | {"schema": "IFC2X3"})

    assert len(found) == 1 and "IFC4" in found[0]


def test_a_named_removal_target_still_in_the_ifc_is_refused():
    found = native_refusals(HOLDS, intent="Remove Window_WIN2, the 1.2 m wide window in Wall_W3, and close its opening.")

    assert len(found) == 1 and "Window_WIN2" in found[0]


def test_an_opening_with_nothing_filling_it_is_named():
    found = native_refusals(HOLDS | {"orphans": ["Window_WIN2"]})

    assert len(found) == 1 and "opening Window_WIN2" in found[0]


def test_a_file_that_had_no_storeys_is_not_asked_for_one():
    assert native_refusals(HOLDS | {"unplaced": ["Wall_W3"]}, storeyed=False) == []


def test_a_file_that_had_no_fills_matches_an_opening_to_its_door_or_window_by_name():
    facts = HOLDS | {"orphans": ["Window_WIN1"], "unfilled": ["Window_WIN1"], "unnamed": ["Opening_X"]}

    found = native_refusals(facts, filled=False)

    assert len(found) == 1 and "opening Opening_X" in found[0]
