"""
Design reads each dimension from two views and names both views and their values in its description, in a headless and in an interactive run.
"""
from core.loop.prompt import prompt
from core.nobody import nobody_answering


def _said(headless):
    if headless:
        with nobody_answering():
            return " ".join(prompt("design").split())
    return " ".join(prompt("design").split())


def test_both_design_prompts_say_to_read_each_dimension_from_a_second_view():
    for headless in (True, False):
        said = _said(headless)
        assert "second view that shows the same length" in said
        assert "a side view for a width read from the front view" in said
        assert "a section for a depth" in said


def test_both_design_prompts_put_both_readings_in_the_description():
    for headless in (True, False):
        said = _said(headless)
        assert "names both views it was read in with the value from each" in said
        assert "front view 120; top view 120" in said
        assert "a value seen in one view only says so there" in said


def test_both_design_prompts_settle_a_disagreement_by_measurement_and_record_it_under_conflicts():
    for headless in (True, False):
        said = _said(headless)
        assert "pixel scale of the view" in said
        assert "a sum of dimensions that must close" in said
        assert "record the disagreement under `conflicts`" in said
        assert "not the nearer-looking number" in said
