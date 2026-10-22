import numpy as np

import sheet_render as sr


def text_items(*rects):
    items = sr.Items()
    for i, rect in enumerate(rects):
        items.add_text(rect, f"t{i}", i)
    return items


def test_overlapping_texts_are_found_and_clear_ones_are_not():
    assert sr.overlaps(text_items((50, 50, 60, 54), (100, 100, 110, 104))) == []
    found = sr.overlaps(text_items((50, 50, 60, 54), (55, 52, 65, 56)))
    assert [f["meets"] for f in found] == [["text"], ["text"]]


def test_text_over_another_dimensions_line_or_the_frame_is_found():
    items = text_items((50, 50, 60, 54))
    items.add_segments([[40, 52, 70, 52]], 7)
    assert sr.overlaps(items)[0]["meets"] == ["line"]
    assert sr.overlaps(text_items((10, 50, 25, 54)))[0]["meets"] == ["frame"]


def test_a_texts_own_dimension_line_is_not_an_overlap():
    items = text_items((50, 50, 60, 54))
    items.add_segments([[40, 52, 70, 52]], 0)
    assert sr.overlaps(items) == []


def test_value_labels():
    assert sr.format_value({"kind": "diameter", "value": 6.0, "count": 4}) == "4\u00d7\u00d86"
    assert sr.format_value({"kind": "angle", "value": 30.0, "count": 1}) == "30\u00b0"
    assert sr.format_value({"kind": "chamfer", "value": 1.5, "count": 1}) == "C1.5"
