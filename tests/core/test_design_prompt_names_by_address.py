"""On an edit Design names datums and features by their address in the base part."""
from core.loop.prompt import prompt


def test_the_design_prompt_tells_an_edit_to_name_by_address_in_the_base_part():
    said = " ".join(prompt("design").split())

    assert "the_base_part" in said
    assert "plane y = -177.5" in said
    assert "boss @ plane y = -17.5" in said
