"""
`UIHelpers._icon_svg` caches SVG markup per icon name: the file is
static for the life of the FreeCAD session, so a second call for the same
name must not touch disk again. `get_icon`'s rasterisation stays outside
that cache -- colour and size are per-call, so the markup cache must not be
mistaken for a pixmap cache.
"""
from unittest.mock import mock_open, patch

import pytest

from conftest import NO_QT, QT_APP
from ui.ui_helpers import UIHelpers


def test_icon_svg_reads_disk_once_per_name():
    UIHelpers._icon_svg.cache_clear()
    real_open = open
    calls = []

    def counting_open(path, *args, **kwargs):
        calls.append(path)
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=counting_open):
        first = UIHelpers._icon_svg("plus")
        second = UIHelpers._icon_svg("plus")

    assert first == second
    assert len(calls) == 1


def test_get_icon_still_returns_a_correct_icon_per_name_and_colour():
    """Caching the markup must not break `get_icon` itself."""
    if QT_APP is None:
        pytest.skip(NO_QT)

    red = UIHelpers.get_icon("plus", colour="#ff0000")
    blue = UIHelpers.get_icon("plus", colour="#0000ff")

    assert not red.isNull() and not blue.isNull()
    assert red.pixmap(24, 24).toImage() != blue.pixmap(24, 24).toImage(), \
        "get_icon ignored the requested colour"


def test_get_icons_rasterisation_stays_uncached(monkeypatch):
    """Only the SVG markup is cached, never the rasterised pixmap."""
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui import ui_helpers

    real_renderer = ui_helpers.QtSvg.QSvgRenderer
    made = []

    class _CountingRenderer(real_renderer):
        def __init__(self, *args, **kwargs):
            made.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ui_helpers.QtSvg, "QSvgRenderer", _CountingRenderer)

    UIHelpers.get_icon("plus", colour="#ff0000")
    UIHelpers.get_icon("plus", colour="#ff0000")

    assert len(made) == 2, "identical get_icon calls shared a rasterised pixmap"
