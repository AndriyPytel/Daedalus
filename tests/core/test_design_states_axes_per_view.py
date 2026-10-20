"""Design states the axes of each view, the face and axis of each feature, and cross-checks the views."""
import pytest
from core.loop.prompt import prompt
from core.nobody import nobody_answering


def _said(headless):
    if headless:
        with nobody_answering():
            return " ".join(prompt("design").split())
    return " ".join(prompt("design").split())


@pytest.mark.parametrize("headless", [False, True])
def test_the_design_prompt_asks_for_the_axes_of_each_view(headless):
    said = _said(headless)

    assert "which part axis is horizontal and which is vertical in that view" in said
    assert "the direction the view looks" in said


@pytest.mark.parametrize("headless", [False, True])
def test_the_design_prompt_asks_for_the_face_and_axis_of_each_feature(headless):
    said = _said(headless)

    assert "the face the feature sits on and the axis it runs along" in said
    assert "`position` and `because`" in said


@pytest.mark.parametrize("headless", [False, True])
def test_the_design_prompt_cross_checks_the_views_and_records_a_disagreement(headless):
    said = _said(headless)

    assert "every other view that shows it" in said
    assert "measure instead of choosing" in said
    assert "under `conflicts`" in said
