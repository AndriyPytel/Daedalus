"""
Level 2 -- the Assertion results widget FCC-179 adds, built for real against
FreeCAD's own Qt binding, offscreen.

A pure renderer of one `ASSERTION` event's own data (`ui/assertion_results_
widget.py`'s own docstring): every row and the summary line above them come
from `confirmed`/`unmet`/`code`, nothing else. This is where FCC-179's
Acceptance Criteria on that rendering are checked -- the pass/fail marks, the
summary count, and the per-entry expand to the code that ran. The wiring
from a live `core.events.Event` to this widget, and the fallback for the one
case it renders nothing for, is `tests/test_panel_level2.py`'s, the same
split `test_review_verdict_widget_level2.py` draws for the Review card.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _passing(confirmed=("Base height is 10mm", "Hole diameter is 5mm"), code="assert True"):
    return {"ok": True, "code": code, "confirmed": list(confirmed), "uncovered": [],
            "traceback": ""}


def _failing(unmet=("Lug height is 30mm",), code="assert False, 'these did not hold: x'"):
    return {"ok": False, "code": code, "confirmed": [], "uncovered": [],
            "traceback": "AssertionError: these did not hold: x", "unmet": list(unmet)}


def _widget(data):
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.assertion_results_widget import AssertionResultsWidget

    return AssertionResultsWidget(data)


def test_the_summary_line_counts_passed_only_when_nothing_failed():
    from ui.assertion_results_widget import summary_line

    assert summary_line(_passing()) == "2 passed"


def test_the_summary_line_counts_passed_and_failed_together():
    from ui.assertion_results_widget import summary_line

    assert summary_line(_failing()) == "0 passed, 1 failed"


def test_the_summary_line_is_empty_when_the_checks_could_not_be_written():
    from ui.assertion_results_widget import summary_line

    assert summary_line({"ok": None, "code": "", "confirmed": [], "uncovered": []}) == ""


def test_a_passing_run_shows_one_row_per_confirmed_entry():
    from conftest import QtWidgets

    from ui.accordion_widget import AccordionWidget

    card = _widget(_passing())

    rows = card.findChildren(AccordionWidget)
    assert [row.toggle_button.text() for row in rows] == \
        ["\u25b6  Base height is 10mm", "\u25b6  Hole diameter is 5mm"]

    heading = next(w for w in card.findChildren(QtWidgets.QLabel) if w.text() == "2 passed")
    assert heading is not None


def test_a_failing_run_shows_one_row_per_unmet_entry():
    from ui.accordion_widget import AccordionWidget

    card = _widget(_failing())

    rows = card.findChildren(AccordionWidget)
    assert [row.toggle_button.text() for row in rows] == ["\u25b6  Lug height is 30mm"]


def _mixed(confirmed=("Base height is 10mm",), unmet=("Lug height is 30mm",),
           code="assert not failed"):
    return {"ok": False, "code": code, "confirmed": list(confirmed), "uncovered": [],
            "traceback": "AssertionError: these did not hold: x", "unmet": list(unmet)}


def test_a_mixed_result_shows_a_passed_row_and_a_failed_row_together():
    """
    FCC-169: a run where the checks measured some entries true and some
    false, proved against this widget unmodified -- it already iterates
    `confirmed` and `unmet` independently, so a mixed `ASSERTION` renders a
    mixed card with no change to `AssertionResultsWidget` at all.
    """
    from ui.accordion_widget import AccordionWidget
    from ui.assertion_results_widget import FAIL, SUCCESS, summary_line

    data = _mixed()
    card = _widget(data)

    assert summary_line(data) == "1 passed, 1 failed"

    rows = card.findChildren(AccordionWidget)
    assert [row.toggle_button.text() for row in rows] == [
        "\u25b6  Base height is 10mm", "\u25b6  Lug height is 30mm"]
    assert SUCCESS in rows[0].toggle_button.styleSheet()
    assert FAIL in rows[1].toggle_button.styleSheet()


def test_a_passed_row_is_marked_with_the_semantic_success_colour():
    from ui.assertion_results_widget import SUCCESS
    from ui.accordion_widget import AccordionWidget

    card = _widget(_passing(confirmed=("Base height is 10mm",)))

    row = card.findChildren(AccordionWidget)[0]
    assert SUCCESS in row.toggle_button.styleSheet()


def test_a_failed_row_is_marked_with_the_semantic_fail_colour():
    from ui.assertion_results_widget import FAIL
    from ui.accordion_widget import AccordionWidget

    card = _widget(_failing())

    row = card.findChildren(AccordionWidget)[0]
    assert FAIL in row.toggle_button.styleSheet()


def test_clicking_an_entry_expands_it_to_the_code_that_ran():
    from ui.accordion_widget import AccordionWidget

    card = _widget(_passing(confirmed=("Base height is 10mm",), code="assert True  # measured"))
    card.show()
    QT_APP.processEvents()

    row = card.findChildren(AccordionWidget)[0]
    assert not row.content_area.isVisible()

    row.toggle_content()
    QT_APP.processEvents()

    assert row.content_area.isVisible()
    assert row.content_area.text() == "assert True  # measured"


def test_checks_that_could_not_be_written_render_no_rows():
    from ui.accordion_widget import AccordionWidget

    card = _widget({"ok": None, "code": "", "confirmed": [], "uncovered": []})

    assert card.findChildren(AccordionWidget) == []
