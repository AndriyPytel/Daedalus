"""
QSS constants shared by every widget this design system rebuilds. Spacing
doubles from 4: 8 is the gap inside a row or block, 16 between sibling
blocks, and SPACE_COMPOSER is the one 12, used only for the composer's text
padding; radii are named for the rule that picks them rather than their
size -- RADIUS_CONTROL for a button/chip/hover row, RADIUS_SURFACE for a
panel-like surface (bubble, composer, results card), RADIUS_CHIP for an
attachment chip inside another surface; CONTROL/CONTROL_DENSE are the two
hit-target sizes, 28 everywhere and 24 only in the densest list row, and
ICON/ICON_DENSE are the two icon sizes, 16 beside text and 12 inline in
something already tight. SUCCESS and FAIL are the two semantic hex colours,
RUNNING is the one palette role its themes keep meaningful, WASH is the
translucent grey for separating anything from its surroundings, and
HAIRLINE is that same grey at the alpha a one-pixel edge needs to read as
an edge rather than a smudge.
"""

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 16
SPACE_COMPOSER = 12

RADIUS_CHIP = 6
RADIUS_CONTROL = 8
RADIUS_SURFACE = 10

CONTROL = 28
CONTROL_DENSE = 24

ICON = 16
ICON_DENSE = 12

SUCCESS = "#4caf50"
FAIL = "#f44336"
RUNNING = "palette(highlight)"
WASH = "rgba(127, 127, 127, 40)"
HAIRLINE = "rgba(127, 127, 127, 60)"
