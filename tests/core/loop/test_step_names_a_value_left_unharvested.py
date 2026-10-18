from core.loop.plan import _steps_of


def test_a_step_naming_a_value_the_kernel_holds_but_could_not_keep_is_told_so():
    *_, value_errors = _steps_of(
        [{"do": "Pad it", "values": ["solid", "ghost"]}], None, {}, None,
        {"solid": "Shape"})

    why = {error["why"] for error in value_errors}
    assert any("'solid'" in text and "Shape" in text and "plain data" in text
               and "never computed" not in text for text in why)
    assert any("'ghost' was never computed" in text for text in why)
