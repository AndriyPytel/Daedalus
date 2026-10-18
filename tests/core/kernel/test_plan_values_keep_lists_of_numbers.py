"""
Plan's kernel keeps every plain value it computed by name -- numbers, strings
and booleans, alone or nested in lists and string-keyed dicts -- and names the
rest without keeping them.
"""
from core.kernel.kernel_worker import _harvested_plan_values, _unharvested_plan_names


def test_what_is_not_plain_data_is_left_behind():
    kept = _harvested_plan_values({
        "empty": [], "none": None, "holes": [1.0, None], "int_keys": {1: 2.0},
        "shape": object(), "_scratch": 4.0, "design": 1.0,
    })

    assert kept == {}


def test_a_segment_with_text_and_flags_is_kept_unchanged():
    segment = {"type": "line", "plane": "XY", "closed": False, "points": [[0, 0], [147.6, 0]]}

    kept = _harvested_plan_values({"base_outline": [segment], "label": "Pad", "flag": True})

    assert kept == {"base_outline": [segment], "label": "Pad", "flag": True}


def test_a_self_referencing_or_very_deep_value_is_not_kept_and_does_not_raise():
    loop = []
    loop.append(loop)
    deep = current = []
    for _ in range(5000):
        current.append([])
        current = current[0]
    current.append(1.0)

    assert _harvested_plan_values({"loop": loop, "deep": deep, "fine": 2.0}) == {"fine": 2.0}


def test_the_names_not_kept_are_reported_with_the_type_they_hold():
    class Shape:
        pass

    seen = _unharvested_plan_names({
        "solid": Shape(), "table": [1.0, None], "ok": 3.0, "_scratch": Shape(), "design": Shape(),
    })

    assert seen == {"solid": "Shape", "table": "list"}
