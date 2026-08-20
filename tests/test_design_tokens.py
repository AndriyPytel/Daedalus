"""
Level 1 -- the QSS constants FCC-171 hands every later child of FCC-169.

Values are the ones repeated most in the FCC-167 Figma spec
(docs/research/fcc167-ai-elements-figma-spec.md) and the colours ADR-0011
(FCC-A-16) carves out as exceptions to "the panel has no colours of its own".
"""
from ui import design_tokens as tokens
from tests.test_the_panel_wears_the_users_theme import ALLOWED_ROLE, SEMANTIC


def test_the_spacing_scale_doubles_from_four():
    assert (tokens.SPACE_XS, tokens.SPACE_SM, tokens.SPACE_MD) == (4, 8, 16)


def test_the_radii_are_the_three_figma_repeats():
    assert (tokens.RADIUS_SM, tokens.RADIUS_MD, tokens.RADIUS_LG) == (5, 6, 10)


def test_the_semantic_colours_are_the_ones_the_theme_test_allows():
    assert {tokens.SUCCESS, tokens.FAIL} == SEMANTIC


def test_running_is_the_one_palette_role_the_theme_test_allows():
    assert tokens.RUNNING == ALLOWED_ROLE


def test_the_wash_is_grey():
    assert tokens.WASH == "rgba(127, 127, 127, 40)"
