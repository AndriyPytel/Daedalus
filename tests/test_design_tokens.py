"""
Level 1 -- the QSS constants FCC-171 hands every later child of FCC-169.

The values are the ones the AI Elements design language repeats
(`docs/research/fcc169-ai-elements-visual-grammar.md`, read off the user's own Figma file), and
the colours ADR-0011 (FCC-A-16) carves out as exceptions to "the panel has no
colours of its own".
"""
from ui import design_tokens as tokens
from tests.test_the_panel_wears_the_users_theme import ALLOWED_ROLES, SEMANTIC


def test_the_spacing_scale_doubles_from_four():
    assert (tokens.SPACE_XS, tokens.SPACE_SM, tokens.SPACE_MD) == (4, 8, 16)


def test_the_composer_keeps_the_one_twelve_in_the_scale():
    """The single place the design system steps off the 4/8/16 scale."""
    assert tokens.SPACE_COMPOSER == 12


def test_a_radius_is_named_for_what_it_is_put_on():
    """A chip, a control and a surface, and no fourth radius anywhere."""
    assert (tokens.RADIUS_CHIP, tokens.RADIUS_CONTROL, tokens.RADIUS_SURFACE) == (6, 8, 10)


def test_the_two_control_sizes_and_the_two_icon_sizes():
    assert (tokens.CONTROL, tokens.CONTROL_DENSE) == (28, 24)
    assert (tokens.ICON, tokens.ICON_DENSE) == (16, 12)


def test_the_semantic_colours_are_the_ones_the_theme_test_allows():
    assert {tokens.SUCCESS, tokens.FAIL} == SEMANTIC


def test_running_is_a_palette_role_the_theme_test_allows():
    assert tokens.RUNNING in ALLOWED_ROLES


def test_the_wash_and_the_hairline_are_the_same_grey():
    assert tokens.WASH == "rgba(127, 127, 127, 40)"
    assert tokens.HAIRLINE == "rgba(127, 127, 127, 60)"
