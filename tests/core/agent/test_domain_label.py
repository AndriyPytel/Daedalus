"""
Level 1 -- a Domain is shown to the user by its name, BIM in capitals.
"""
from core.agent.domain import label


def test_bim_is_shown_in_capitals_and_the_others_capitalised():
    assert [label(name) for name in ("part", "assembly", "bim")] == ["Part", "Assembly", "BIM"]
