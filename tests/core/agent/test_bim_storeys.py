"""
Level 1 -- the BIM Assertion of a building of two or more Levels names what fails, with numbers; the one-storey Assertion is as it was.
"""
from core.agent.bim import refusals
from tests.bim_helpers import HOLDS

HEIGHT = 2800.0
WALLS = [0.0, 0.0, 8000.0, 5000.0]


def _level(label, elevation, walls=1, slabs=1, building=True):
    return {"label": label, "elevation": elevation, "walls": walls, "slabs": slabs, "building": building, "box": WALLS}


def _stair(label, bottom, top, placed=True, box=(1000.0, 1000.0, 2000.0, 4000.0)):
    return {"label": label, "bottom": bottom, "top": top, "level": placed, "box": list(box)}


BUILDING = HOLDS | {
    "levels": [_level("Level_0", 0.0), _level("Level_1", HEIGHT), _level("Level_2", 2 * HEIGHT)],
    "stairs": [_stair("Stairs_0", 0.0, HEIGHT), _stair("Stairs_1", HEIGHT, 2 * HEIGHT)],
}


def _building(**fields):
    return BUILDING | fields


def test_a_building_that_holds_is_refused_nothing():
    assert refusals(BUILDING) == []


def test_the_storey_height_is_no_part_of_the_assertion():
    levels = [_level("Level_0", 0.0), _level("Level_1", 3300.0), _level("Level_2", 6000.0)]
    stairs = [_stair("Stairs_0", 0.0, 3300.0), _stair("Stairs_1", 3300.0, 6000.0)]

    assert refusals(_building(levels=levels, stairs=stairs)) == []


def test_one_level_is_a_flat_and_needs_neither_a_second_level_nor_stairs():
    assert refusals(HOLDS) == []
    assert refusals(HOLDS | {"levels": [_level("Level", 0.0)], "stairs": []}) == []
    assert refusals(HOLDS | {"levels": [_level("Level", 0.0, building=False)], "stairs": []}) == []


def test_two_buildings_are_refused():
    found = refusals(_building(buildings=2))

    assert len(found) == 1 and "2 Buildings" in found[0]


def test_a_level_outside_the_building_is_named():
    levels = [_level("Level_0", 0.0), _level("Level_1", HEIGHT, building=False), _level("Level_2", 2 * HEIGHT)]

    found = refusals(_building(levels=levels))

    assert len(found) == 1 and "Level_1" in found[0] and "not under the Building" in found[0]


def test_levels_at_one_elevation_are_refused():
    levels = [_level("Level_0", 0.0), _level("Level_1", 0.0), _level("Level_2", 2 * HEIGHT)]

    found = refusals(_building(levels=levels))

    assert any("Level_1" in line and "elevation" in line for line in found), found


def test_a_level_without_walls_or_without_a_slab_is_named():
    levels = [_level("Level_0", 0.0), _level("Level_1", HEIGHT, walls=0), _level("Level_2", 2 * HEIGHT, slabs=0)]

    found = refusals(_building(levels=levels))

    assert any("Level_1" in line and "no Wall" in line for line in found), found
    assert any("Level_2" in line and "no Slab" in line for line in found), found


def test_a_gap_without_stairs_is_named_with_both_levels():
    found = refusals(_building(stairs=[_stair("Stairs_0", 0.0, HEIGHT)]))

    assert len(found) == 1 and "Level_1" in found[0] and "Level_2" in found[0] and "Stairs" in found[0]


def test_stairs_that_start_or_end_off_their_levels_leave_the_gap_without_stairs():
    for stair in (_stair("Stairs_1", HEIGHT + 100, 2 * HEIGHT), _stair("Stairs_1", HEIGHT, 2 * HEIGHT - 30)):
        found = refusals(_building(stairs=[_stair("Stairs_0", 0.0, HEIGHT), stair]))

        assert any("Level_1" in line and "Level_2" in line for line in found), found


def test_a_top_within_two_centimetres_of_the_upper_level_holds():
    assert refusals(_building(stairs=[_stair("Stairs_0", 0.0, HEIGHT - 20), _stair("Stairs_1", HEIGHT, 2 * HEIGHT + 15)])) == []


def test_two_stairs_in_one_gap_are_refused():
    found = refusals(_building(stairs=BUILDING["stairs"] + [_stair("Stairs_2", 0.0, HEIGHT)]))

    assert len(found) == 1 and "Stairs_2" in found[0]


def test_stairs_between_no_levels_are_named():
    found = refusals(_building(stairs=BUILDING["stairs"] + [_stair("Stairs_9", 500.0, 900.0)]))

    assert len(found) == 1 and "Stairs_9" in found[0]


def test_stairs_outside_every_level_are_named():
    found = refusals(_building(stairs=[_stair("Stairs_0", 0.0, HEIGHT, placed=False), BUILDING["stairs"][1]]))

    assert len(found) == 1 and "Stairs_0" in found[0] and "not under a Level" in found[0]


def test_stairs_outside_the_walls_of_the_lower_level_are_named():
    found = refusals(_building(stairs=[_stair("Stairs_0", 0.0, HEIGHT, box=(7500.0, 1000.0, 8500.0, 4000.0)),
                                       BUILDING["stairs"][1]]))

    assert len(found) == 1 and "Stairs_0" in found[0] and "Level_0" in found[0] and "walls" in found[0]
