"""
Design builds the part the isometric view shows, and reads a flat pattern only as how the sheet is cut, in a headless and in an interactive run.
"""
from core.loop.prompt import nobody_answering, prompt


def _said(headless):
    if headless:
        with nobody_answering():
            return " ".join(prompt("design").split())
    return " ".join(prompt("design").split())


def test_both_design_prompts_build_the_isometric_and_read_a_flat_pattern_as_a_cut():
    for headless in (True, False):
        said = _said(headless)
        assert "The isometric view shows the finished part, and that is what gets built" in said
        assert "A flat pattern only shows how the sheet is cut" in said
        assert "developed lengths, never the part's extents" in said
