"""
Level 1 -- the reader arm's table is parsed tolerantly, and the finish gate lists differences only.
"""
from core.agent.reader import _direction, mismatches, parse_table

PLATE = {"solid": True, "extents": [100.0, 50.0, 10.0], "volume": 40000.0, "faces": 10,
         "b0": 1, "b1": 0, "b2": 0}


def _table(**fields):
    return parse_table('{"overall": {"x": 100, "y": 50, "z": 10}, "through_count": 0}') | fields


def test_a_table_wrapped_in_prose_with_unreadable_fields_is_still_a_table():
    table = parse_table('Here you go:\n```json\n{"overall": {"x": "100", "y": "n/a", "z": 10}, '
                        '"through_count": "4", "thin_walled": "yes", "views": [{"crop": 1, '
                        '"view": "front"}, "junk"], "dimensions": 3}\n```')

    assert table["overall"] == {"x": 100.0, "y": None, "z": 10.0}
    assert table["through_count"] == 4 and table["thin_walled"] is True
    assert table["views"] == [{"crop": 1, "view": "front"}] and table["dimensions"] == []


def test_a_reply_with_no_object_is_no_table():
    assert parse_table("I could not read it.") is None
    assert parse_table("") is None


def test_a_part_that_matches_the_table_has_no_differences():
    assert mismatches(PLATE, _table()) == []


def test_a_wrong_count_is_not_a_difference_and_a_wrong_size_is_only_a_note_to_check_the_crop():
    table = _table(overall={"x": 120.0, "y": 50.0, "z": 10.0}, through_count=4, thin_walled=True)

    found = mismatches(dict(PLATE, volume=45000.0), table)

    assert len(found) == 2
    assert found[0].startswith("The table read 120 mm") and "Check it on the crop" in found[0]
    assert found[1].startswith("The drawing shows a thin-walled part")
    assert not any("conform" in line.lower() or "Through" in line for line in found)


def test_a_size_read_off_a_flat_pattern_is_not_compared_with_the_formed_part():
    table = _table(overall={"x": 300.0, "y": 150.0, "z": 3.0}, flat_pattern_present=True)

    assert mismatches(dict(PLATE, extents=[290.0, 150.0, 70.0], volume=5000.0), table) == []


def test_an_overall_size_within_two_percent_or_half_a_millimetre_is_not_a_difference():
    table = _table(overall={"x": 101.5, "y": 50.4, "z": 10.0})

    assert mismatches(PLATE, table) == []


def test_an_edit_that_changed_nothing_is_a_difference_and_a_changed_part_is_not():
    same = dict(PLATE, base={"volume": 40000.0, "faces": 10})
    changed = dict(PLATE, volume=39000.0, base={"volume": 40000.0, "faces": 10})

    assert len(mismatches(same, None)) == 1 and "not been made" in mismatches(same, None)[0]
    assert mismatches(changed, None) == []


def test_the_overall_size_ignores_which_axis_the_part_was_built_along():
    stood_up = dict(PLATE, extents=[10.0, 100.0, 50.0])

    assert mismatches(stood_up, _table()) == []
    assert len(mismatches(stood_up, _table(overall={"x": 120.0, "y": 50.0, "z": 10.0}))) == 1


def test_a_views_name_is_matched_to_a_standard_view_by_substring_and_a_bare_section_is_drawn_from_the_front():
    assert _direction("top (end view, upper) + front (lower)") == "top"
    assert _direction("Section A-A, right") == "right"
    assert _direction("section B-B") == "front"
    assert _direction("detail C") is None


def test_the_reading_prompt_keeps_overall_to_one_printed_dimension():
    from core.prompts import read_prompt

    text = read_prompt("read_table.md")

    assert "never a flat-pattern length and never a sum" in text
    assert len(text) < 3072


def test_the_builder_prompt_says_names_do_not_persist_and_no_longer_argues_for_finishing_early():
    from core.prompts import read_prompt

    text = read_prompt("reader.md")

    assert "fresh namespace" in text
    assert "turns are counted" not in text and "refine after" not in text


def test_the_table_carries_no_wall_thickness_even_when_the_reading_writes_one():
    from core.prompts import read_prompt

    table = parse_table('{"thin_walled": true, "wall_thickness": 1.5}')

    assert table["thin_walled"] is True and "wall_thickness" not in table
    assert "wall_thickness" not in read_prompt("read_table.md")
