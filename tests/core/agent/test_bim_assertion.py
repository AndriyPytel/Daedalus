"""
Level 1 -- the BIM Assertion names what fails, with numbers, and says nothing where nothing fails.
"""
from core.agent.bim import refusals

WALL = {"label": "Wall", "length": 4000.0, "height": 2600.0, "width": 200.0, "volume": 1.0}
HOLDS = {"walls": [WALL], "slabs": ["Slab"], "doors": [{"label": "Door", "hosts": ["Wall"]}],
         "windows": [{"label": "Window", "hosts": ["Wall"]}], "spaces": ["Space"],
         "buildings": 1, "unplaced": [], "ifc": {"schema": "IFC4", "IfcWall": 1, "IfcSlab": 1, "IfcDoor": 1,
                                                "IfcWindow": 1, "IfcSpace": 1}}


def _facts(**fields):
    return HOLDS | fields


def test_a_flat_that_holds_is_refused_nothing():
    assert refusals(HOLDS) == []


def test_a_missing_kind_of_element_is_named():
    for key, name in (("slabs", "Slab"), ("spaces", "Space"), ("doors", "Door"),
                      ("windows", "Window"), ("walls", "Wall")):
        found = refusals(_facts(**{key: []}))

        assert any("no " + name in line for line in found), (key, found)


def test_a_door_or_window_without_a_host_wall_is_named():
    found = refusals(_facts(windows=[{"label": "Window001", "hosts": []}]))

    assert len(found) == 1 and "Window001" in found[0] and "host" in found[0]


def test_a_wall_with_no_length_height_or_width_is_named_with_the_measure():
    for measure in ("length", "height", "width"):
        found = refusals(_facts(walls=[WALL | {"label": "Wall001", measure: 0.0}]))

        assert len(found) == 1 and "Wall001" in found[0] and measure in found[0], found


def test_a_flat_with_no_building_is_refused():
    found = refusals(_facts(buildings=0))

    assert len(found) == 1 and "Building" in found[0]


def test_an_element_outside_any_building_or_level_is_named():
    found = refusals(_facts(unplaced=["Wall001"]))

    assert len(found) == 1 and "Wall001" in found[0]


def test_an_export_that_failed_says_why():
    found = refusals(_facts(ifc={"error": "RuntimeError: no storey"}))

    assert len(found) == 1 and "IFC4" in found[0] and "no storey" in found[0]


def test_an_export_that_is_not_ifc4_or_lost_an_element_kind_is_named():
    assert "IFC2X3" in refusals(_facts(ifc=HOLDS["ifc"] | {"schema": "IFC2X3"}))[0]
    found = refusals(_facts(ifc=HOLDS["ifc"] | {"IfcSpace": 0}))

    assert len(found) == 1 and "IfcSpace" in found[0]
