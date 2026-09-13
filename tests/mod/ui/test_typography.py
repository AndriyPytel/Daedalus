"""
Level 1 -- the type scale read off the Figma spec.

Every text node the spec records sets Geist at one of four
(size, weight) pairs; the family stays a system font, so
only the size and the weight travel from Figma.
"""
from ui import typography


def test_the_scale_is_the_four_pairs_the_spec_repeats():
    assert {typography.CAPTION, typography.LABEL,
            typography.BODY, typography.BODY_STRONG} == \
        {(12, 400), (12, 500), (14, 400), (14, 500)}


def test_css_renders_size_and_weight_only():
    assert typography.css(typography.BODY) == "font-size: 14px; font-weight: 400;"


def test_css_carries_no_colour_or_family():
    for scale in (typography.CAPTION, typography.LABEL,
                  typography.BODY, typography.BODY_STRONG):
        rendered = typography.css(scale)
        assert "color" not in rendered and "font-family" not in rendered
