"""
QSS constants shared by every widget FCC-169 rebuilds.

SPACE_XS/SM/MD double from 4: 8 is the gap between anything inside one row or
one block, 16 is the gap between sibling blocks -- card padding, the panel's
own gutter, and the space between two turns of the conversation. 4 is what is
left for the inside of a chip. SPACE_COMPOSER is the one 12 the design system
uses, and it uses it in one place: the padding around the composer's text.

The radii are named for the rule that picks them, not for their size. A
control -- a button, a chip, a row that highlights under the pointer -- takes
RADIUS_CONTROL. A surface -- something the reader takes for a panel of its
own, the user's bubble, the composer, a results card -- takes RADIUS_SURFACE.
An attachment chip inside another surface takes RADIUS_CHIP. Names that said
SM/MD/LG said nothing about which to reach for, and the wrong one was reached
for repeatedly.

CONTROL and CONTROL_DENSE are the two hit-target sizes: 28 everywhere, 24 only
inside the densest list row. ICON and ICON_DENSE are the two icon sizes: 16
beside text, 12 inline in something already tight. The design system uses no
others in a live component.

Colour is not geometry: SUCCESS and FAIL are the two hex values ADR-0011
carves out as semantic, RUNNING is the one palette role its themes keep
meaningful, and WASH is the translucent grey the same decision names for
anything that has to separate from its surroundings. HAIRLINE is that same
grey at the alpha a one-pixel edge needs to read as an edge rather than as a
smudge -- a surface separated by a rule instead of by a fill, which is what
this design system reaches for before it reaches for a fill at all.
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
