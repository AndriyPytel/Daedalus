"""
The type scale Figma's AI Elements components repeat, translated to size and
weight only.

Every text node the FCC-167 spec recorded sets Geist at one of four
combinations -- 12/400, 12/500, 14/400, 14/500 -- never a fifth. ADR-0011
(FCC-A-16) keeps the family: FreeCAD's own system font, from
`UIHelpers.get_ui_font()`, stands in for Geist, so only the size and the
weight travel from here into a stylesheet.
"""

CAPTION = (12, 400)
LABEL = (12, 500)
BODY = (14, 400)
BODY_STRONG = (14, 500)


def css(scale):
    """The QSS fragment for a `(size, weight)` pair from this module's scale."""
    size, weight = scale
    return "font-size: %dpx; font-weight: %d;" % (size, weight)
