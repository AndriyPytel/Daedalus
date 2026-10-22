import numpy as np

import sheet_dimensions as sd
import sheet_layout as sl
import sheet_render as sr
from conftest import box, drill
from test_layout import views


def six_holes():
    part = box(100, 60, 20)
    for x in (10, 20, 30, 45, 60, 75):
        part = drill(part, x, 30, 2)
    return part


def test_a_six_level_chain_is_running_dims_with_one_record_per_level():
    part = six_holes()
    dims = sd.dimensions(part)
    running = [d for d in dims if d.kind == "running" and d.axis == 0]
    assert sorted(round(d.value, 6) for d in running) == [10.0, 20.0, 30.0, 45.0, 60.0, 75.0]
    assert not [d for d in dims if d.kind == "linear" and d.axis == 0 and d.feature.startswith("level")]
    assert sd.missing(part, dims) == []


def test_a_short_chain_keeps_its_chain_dims(plate):
    assert not [d for d in sd.dimensions(plate) if d.kind == "running"]


def test_a_running_group_renders_one_dimension_line_and_an_arrowhead_per_level():
    part = six_holes()
    records = [d.to_dict() for d in sd.dimensions(part) if d.kind == "running" and d.axis == 0]
    scale, placed, assigned = next(sl.plans(records, views((100.0, 60.0, 20.0))))
    items = sr.Items()
    parts = sr._dimensions(assigned, views((100.0, 60.0, 20.0)), placed, items)
    assert (items.segment_owner == sr.RUNNING_LINE).sum() == 1
    assert sum(p.startswith("<polygon") for p in parts) == 6
    assert sorted(t["label"] for t in items.texts) == ["10", "20", "30", "45", "60", "75"]


def test_shorter_spans_get_the_tier_nearer_the_view():
    assert sr.tier_ranks([("a", 0, 100), ("a", 10, 20)]) == [1, 0]
    assert sr.tier_ranks([("a", 0, 10), ("a", 20, 30), ("a", 0, 30)]) == [0, 0, 1]
    assert sr.tier_ranks([("a", 0, 100), ("b", 10, 20)]) == [0, 0]
