"""
`UIHelpers._icon_svg` caches SVG markup per icon name (FCC-281): the file is
static for the life of the FreeCAD session, so a second call for the same
name must not touch disk again.
"""
from unittest.mock import mock_open, patch

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
