"""
Plan's kernel keeps every number it computed by name, a list of stations as
much as a single station, and nothing that is not a number.
"""
from core.kernel.kernel_worker import _harvested_plan_values


def test_what_is_not_a_number_or_a_list_of_numbers_is_left_behind():
    kept = _harvested_plan_values({
        "flag": True, "label": "Pad", "mixed": [1.0, "a"], "empty": [],
        "_scratch": 4.0, "design": 1.0,
    })

    assert kept == {}
