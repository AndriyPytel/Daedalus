"""
QSS constants shared by every widget FCC-169 rebuilds.

SPACE_XS/SM/MD double from 4, the padding and gap Figma's AI Elements
components repeat most across Queue, Task, Chain of Thought and Test Results
(`docs/research/fcc167-ai-elements-figma-spec.md`, FCC-167). RADIUS_SM/MD/LG
are 5, 6 and 10 -- the three border-radii those same components use, from
card (5) to thumbnail chip (6) to results panel (10). Geometry and spacing
are what ADR-0011 (FCC-A-16) lets Figma hand this addon.

Colour is not geometry: SUCCESS and FAIL are the two hex values ADR-0011
carves out as semantic, RUNNING is the one palette role its themes keep
meaningful, and WASH is the translucent grey the same decision names for
anything that has to separate from its surroundings.
`tests/test_the_panel_wears_the_users_theme.py` enforces all four as the only
colours a stylesheet under `ui/` or `Mod/pages/` may hold literally.
"""

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 16

RADIUS_SM = 5
RADIUS_MD = 6
RADIUS_LG = 10

SUCCESS = "#4caf50"
FAIL = "#f44336"
RUNNING = "palette(highlight)"
WASH = "rgba(127, 127, 127, 40)"
