"""
ui/icons/*.svg are standalone-valid SVG documents.

Standalone-valid here means the root element carries `xmlns`: without it a
viewer that treats the file as a document on its own (not fed through
`UIHelpers.get_icon`'s string handling) refuses to render it. `width`/
`height` stay out of scope: all 21 files already carry a `viewBox="0 0 24 24"`,
and `tests/test_icon_set_level2.py::test_every_icon_is_drawn_on_the_grid_the_tint_scales_from`
already treats a pinned size as an offender.
"""
import os
import re

from ui.ui_helpers import ICON_DIR

ICONS = ("arrow_up", "attachment", "brain", "chevron_down", "circle", "circle_check",
         "circle_dot", "circle_x", "close", "eye_closed", "eye_open", "factory",
         "file_text", "history", "image", "plus", "refresh", "send", "square",
         "trash", "user")


def test_every_icon_svg_root_carries_the_svg_namespace():
    offenders = []
    for name in ICONS:
        with open(os.path.join(ICON_DIR, "%s.svg" % name), encoding="utf-8") as handle:
            markup = handle.read()
        if 'xmlns="http://www.w3.org/2000/svg"' not in markup:
            offenders.append(name)
    assert not offenders, offenders


def test_no_icon_svg_gained_a_pinned_size():
    """xmlns is scoped in, width/height stays scoped out -- see the module docstring."""
    for name in ICONS:
        with open(os.path.join(ICON_DIR, "%s.svg" % name), encoding="utf-8") as handle:
            markup = handle.read()
        assert not re.search(r"<svg[^>]*\s(?:width|height)=", markup), name
