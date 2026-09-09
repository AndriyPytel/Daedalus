"""
Resources/icons/*.svg are standalone-valid SVG documents.

Standalone-valid here means the root element carries `xmlns`: without it a
viewer that treats the file as a document on its own (not fed through
`UIHelpers.get_icon`'s string handling) refuses to render it. `width`/
`height` stays out of scope for panel icons: all of them carry a
`viewBox="0 0 24 24"`, and
`tests/test_icon_set_level2.py::test_every_icon_is_drawn_on_the_grid_the_tint_scales_from`
already treats a pinned size as an offender. Workbench icons (copilot.svg,
preferences-freecadcopilot.svg) are logos with a different viewBox and a
pinned size that FreeCAD's workbench loader expects.
"""
import os
import re

from ui.ui_helpers import ICON_DIR


def _icon_names():
    """Derive the icon list from what the directory actually contains."""
    return [name[:-len(".svg")] for name in sorted(os.listdir(ICON_DIR))
            if name.endswith(".svg")]


def test_exactly_one_icon_directory_exists():
    """The addon has one icon directory; the old ui/icons/ no longer exists."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ui_icons = os.path.join(repo, "src", "ui", "icons")
    assert not os.path.isdir(ui_icons), "src/ui/icons/ should be deleted"
    assert os.path.isdir(ICON_DIR), f"{ICON_DIR} must exist"


def test_every_icon_svg_root_carries_the_svg_namespace():
    offenders = []
    for name in _icon_names():
        with open(os.path.join(ICON_DIR, "%s.svg" % name), encoding="utf-8") as handle:
            markup = handle.read()
        if 'xmlns="http://www.w3.org/2000/svg"' not in markup:
            offenders.append(name)
    assert not offenders, offenders


def test_no_panel_icon_svg_gained_a_pinned_size():
    """
    Panel icons carry no width/height; workbench logos do.

    The two workbench icons (copilot, preferences-freecadcopilot) are logos at
    a fixed size, not tintable glyphs. Panel icons are loaded by
    UIHelpers.get_icon which scales the viewBox, so they must not pin a size.
    """
    workbench_icons = {"copilot", "preferences-freecadcopilot"}
    for name in _icon_names():
        if name in workbench_icons:
            continue
        with open(os.path.join(ICON_DIR, "%s.svg" % name), encoding="utf-8") as handle:
            markup = handle.read()
        assert not re.search(r"<svg[^>]*\s(?:width|height)=", markup), name
