"""
Level 2 — a code row is coloured the way FreeCAD's own editor colours Python,
built against FreeCAD's Qt binding, offscreen.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _widget(code=False):
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.accordion_widget import AccordionWidget

    return AccordionWidget(code=code)


def _colours():
    from ui.ui_helpers import UIHelpers

    return UIHelpers.get_editor_colours()


def test_a_code_row_paints_python_in_the_editors_colours():
    widget = _widget(code=True)
    widget.append_text("import math  # the bore\nradius = 25.0\nlabel = 'HoleLeft'\n")
    QT_APP.processEvents()

    shown = widget.content_area.text()
    colours = _colours()
    for painted in ("Keyword", "Comment", "Number", "String"):
        assert "color:%s;" % colours[painted] in shown, painted


def test_a_traceback_in_a_code_row_is_shown_and_not_read_as_markup():
    widget = _widget(code=True)
    widget.append_text('File "<step>", line 44, in <module>\nif 1 < 2 and 3 > 2:')
    QT_APP.processEvents()

    shown = widget.content_area.text()
    assert "&lt;module&gt;" in shown and "<module>" not in shown
    assert widget.content_area.selectedText() == ""


def test_a_row_that_is_not_code_is_left_plain():
    widget = _widget()
    widget.append_text("Review named 2 things to check before judging the result.")
    QT_APP.processEvents()

    assert widget.content_area.text() == (
        "Review named 2 things to check before judging the result.")


def test_a_reply_that_is_a_json_object_shows_its_code_coloured():
    widget = _widget()
    widget.append_text('{"code": "import math\\nradius = 25.0"}')
    QT_APP.processEvents()

    shown = widget.content_area.text()
    assert "color:%s;" % _colours()["Keyword"] in shown
    assert "radius" in shown and '"code"' not in shown
