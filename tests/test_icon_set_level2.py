"""
Level 2 -- the icon asset directory, rendered for real.

`ui/icons/` holds one SVG per name and `UIHelpers.get_icon` is the one place
that reads them. The set is Untitled UI Icons (MIT) throughout, save `brain`,
which the free Untitled UI distribution does not have and which comes from
Lucide -- the set Figma's own Reasoning header flattened.

Every file has to survive the rasteriser `get_icon` puts it through: a 24x24
viewBox to scale from, and the literal `currentColor` for the tint to replace.
A file that parses but carries a baked colour renders black on a dark panel,
which is the defect ADR-0011 exists about, and it renders without raising.
"""
import os
import re

import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad

ICONS = ("arrow_up", "attachment", "brain", "chevron_down", "circle", "circle_check",
         "circle_dot", "circle_x", "close", "eye_closed", "eye_open", "factory",
         "file_text", "history", "image", "plus", "refresh", "send", "square",
         "trash", "user")


def _opaque_pixels(icon, size):
    from conftest import QtGui

    image = icon.pixmap(size, size).toImage()
    return [QtGui.QColor(image.pixelColor(x, y))
            for y in range(image.height())
            for x in range(image.width())
            if QtGui.QColor(image.pixelColor(x, y)).alpha() == 255]


def test_every_icon_the_addon_names_has_a_file():
    from ui.ui_helpers import ICON_DIR

    for name in ICONS:
        assert os.path.isfile(os.path.join(ICON_DIR, "%s.svg" % name)), name


def test_the_directory_holds_nothing_the_set_does_not_name():
    """An icon nobody names is one nobody re-drew when the set changed hands."""
    from ui.ui_helpers import ICON_DIR

    found = {name[:-len(".svg")] for name in os.listdir(ICON_DIR)
             if name.endswith(".svg")}
    assert found == set(ICONS)


def test_every_icon_is_drawn_on_the_grid_the_tint_scales_from():
    """
    24x24 and `currentColor`, on every file, whatever set it came from.

    `get_icon` scales the viewBox to whatever size is asked for and swaps
    `currentColor` for a literal before rasterising. A file with its own
    width, or with a colour baked in, comes out the wrong size or the wrong
    colour -- and it comes out without raising, which is why this is a test
    and not a review note.
    """
    from ui.ui_helpers import ICON_DIR

    offenders = {}
    for name in ICONS:
        with open(os.path.join(ICON_DIR, "%s.svg" % name), encoding="utf-8") as handle:
            markup = handle.read()
        wrong = []
        if 'viewBox="0 0 24 24"' not in markup:
            wrong.append("no 24x24 viewBox")
        if "currentColor" not in markup:
            wrong.append("no currentColor")
        if re.search(r"<svg[^>]*\s(?:width|height)=", markup):
            wrong.append("pins its own size")
        if wrong:
            offenders[name] = wrong

    assert not offenders, offenders


def test_an_icon_renders_at_the_requested_size():
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.ui_helpers import UIHelpers

    for name in ICONS:
        icon = UIHelpers.get_icon(name, size=18)
        assert _opaque_pixels(icon, 18), name
        assert icon.pixmap(18, 18).size().toTuple() == (18, 18), name


def test_an_unknown_icon_name_is_the_old_empty_icon():
    from ui.ui_helpers import UIHelpers

    assert UIHelpers.get_icon("not-a-real-icon").isNull()


def test_a_new_icon_is_tinted_like_every_other():
    if QT_APP is None:
        pytest.skip(NO_QT)
    from conftest import QtGui, QtWidgets
    from ui.ui_helpers import UIHelpers

    button = QtWidgets.QPushButton()
    button.setStyleSheet("QPushButton { color: #1e64c8; }")
    button.show()
    QT_APP.processEvents()
    button.setIcon(UIHelpers.get_icon("circle_check", size=16, on=button))

    painted = {colour.name() for colour in _opaque_pixels(button.icon(), 16)}
    assert painted == {"#1e64c8"}
