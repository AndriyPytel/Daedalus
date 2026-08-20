"""
Level 2 -- the icon asset directory FCC-171 adds, rendered for real.

`ui/icons/` is FCC-171's asset directory: the addon's existing Feather-style
outlines moved out of `ui_helpers.py`, plus the five shapes the phase/Step
status marks and the model-selector trigger need
(docs/research/fcc167-ai-elements-figma-spec.md names the Lucide set these
come from). `UIHelpers.get_icon` is the one place that reads them.
"""
import os

import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad

OLD_ICONS = ("eye_open", "eye_closed", "factory", "user", "trash", "refresh", "plus",
             "image", "file_text", "attachment", "close", "arrow_up", "history")
NEW_ICONS = ("circle", "circle_dot", "circle_check", "circle_x", "chevron_down")


def _opaque_pixels(icon, size):
    from conftest import QtGui

    image = icon.pixmap(size, size).toImage()
    return [QtGui.QColor(image.pixelColor(x, y))
            for y in range(image.height())
            for x in range(image.width())
            if QtGui.QColor(image.pixelColor(x, y)).alpha() == 255]


def test_every_icon_the_addon_names_has_a_file():
    from ui.ui_helpers import ICON_DIR

    for name in OLD_ICONS + NEW_ICONS:
        assert os.path.isfile(os.path.join(ICON_DIR, "%s.svg" % name)), name


def test_a_new_icon_renders_at_the_requested_size():
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.ui_helpers import UIHelpers

    for name in NEW_ICONS:
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
