"""
Level 2 -- the Assertion results widget, built for real against
FreeCAD's own Qt binding, offscreen.

A pure renderer of one `ASSERTION` event's own data (`ui/assertion_results_
widget.py`'s own docstring): every row and the summary line above them come
from `confirmed`/`unmet`/`code`, nothing else. This is where its
Acceptance Criteria on that rendering are checked -- the pass/fail marks, the
summary count, and the per-entry expand to the code that ran. The wiring
from a live `core.events.Event` to this widget, and the fallback for the one
case it renders nothing for, is `tests/test_panel_level2.py`'s, the same
split `test_review_verdict_widget_level2.py` draws for the Review card.

This card was rebuilt to Figma's `TestResults` anatomy: the tests below
that touch icon colour and progress proportion are this file's part of that
-- rule 1 of the ten, a row is never recoloured to report its state, only
the mark beside it
and the word after it change.
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


def _opaque_pixels(pixmap):
    from conftest import QtGui

    image = pixmap.toImage()
    return [QtGui.QColor(image.pixelColor(x, y))
            for y in range(image.height())
            for x in range(image.width())
            if QtGui.QColor(image.pixelColor(x, y)).alpha() == 255]


def _mark_of(row):
    from conftest import QtWidgets

    container = row.parent()
    return next(w for w in container.findChildren(QtWidgets.QLabel) if not w.pixmap().isNull())


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
        ["Base height is 10mm", "Hole diameter is 5mm"]

    heading = next(w for w in card.findChildren(QtWidgets.QLabel) if w.text() == "2 passed")
    assert heading is not None


def test_a_failing_run_shows_one_row_per_unmet_entry():
    from ui.accordion_widget import AccordionWidget

    card = _widget(_failing())

    rows = card.findChildren(AccordionWidget)
    assert [row.toggle_button.text() for row in rows] == ["Lug height is 30mm"]


def _mixed(confirmed=("Base height is 10mm",), unmet=("Lug height is 30mm",),
           code="assert not failed"):
    return {"ok": False, "code": code, "confirmed": list(confirmed), "uncovered": [],
            "traceback": "AssertionError: these did not hold: x", "unmet": list(unmet)}


def test_a_mixed_result_shows_a_passed_row_and_a_failed_row_together():
    """
    A run where the checks measured some entries true and some
    false, proved against this widget unmodified -- it already iterates
    `confirmed` and `unmet` independently, so a mixed `ASSERTION` renders a
    mixed card with no change to `AssertionResultsWidget` at all.
    """
    from ui.accordion_widget import AccordionWidget
    from ui.assertion_results_widget import summary_line

    data = _mixed()
    card = _widget(data)

    assert summary_line(data) == "1 passed, 1 failed"

    rows = card.findChildren(AccordionWidget)
    assert [row.toggle_button.text() for row in rows] == [
        "Base height is 10mm", "Lug height is 30mm"]


def test_a_passed_row_is_marked_with_the_semantic_success_colour():
    from ui.accordion_widget import AccordionWidget
    from ui.assertion_results_widget import SUCCESS

    card = _widget(_passing(confirmed=("Base height is 10mm",)))

    row = card.findChildren(AccordionWidget)[0]
    painted = {colour.name() for colour in _opaque_pixels(_mark_of(row).pixmap())}
    assert painted == {SUCCESS}


def test_a_failed_row_is_marked_with_the_semantic_fail_colour():
    from ui.accordion_widget import AccordionWidget
    from ui.assertion_results_widget import FAIL

    card = _widget(_failing())

    row = card.findChildren(AccordionWidget)[0]
    painted = {colour.name() for colour in _opaque_pixels(_mark_of(row).pixmap())}
    assert painted == {FAIL}


def test_a_failed_row_does_not_recolour_its_container_or_its_text():
    """
    Rule 1 of the ten: state is never a repaint. Only `_mark_of` (the leading
    icon) and the trailing status word change between a passed and a failed
    row -- the row's own container keeps no background of its own, and the
    description text carries no colour, semantic or otherwise.
    """
    from ui.accordion_widget import AccordionWidget
    from ui.assertion_results_widget import FAIL, SUCCESS

    card = _widget(_failing())

    row = card.findChildren(AccordionWidget)[0]
    container = row.parent()

    assert container.styleSheet() == ""
    assert SUCCESS not in row.toggle_button.styleSheet()
    assert FAIL not in row.toggle_button.styleSheet()


def test_a_trailing_status_word_follows_each_row():
    from conftest import QtWidgets

    from ui.accordion_widget import AccordionWidget

    card = _widget(_mixed())

    rows = card.findChildren(AccordionWidget)
    words = [row.parent().findChild(QtWidgets.QLabel, "AssertionRowStatus").text()
             for row in rows]
    assert words == ["passed", "failed"]


def test_the_progress_track_is_proportional_to_the_counts():
    from ui.assertion_results_widget import _progress_fractions

    assert _progress_fractions(passed=3, failed=1) == (0.75, 0.25, 0.0)
    assert _progress_fractions(passed=0, failed=0) == (0.0, 0.0, 1.0)


def test_the_cards_progress_track_matches_its_own_counts():
    from ui.assertion_results_widget import _ProgressTrack

    card = _widget(_mixed(confirmed=("a", "b", "c"), unmet=("d",)))

    track = card.findChildren(_ProgressTrack)[0]
    assert track.fractions == (0.75, 0.25, 0.0)


def test_checks_that_could_not_be_written_render_no_rows():
    from ui.accordion_widget import AccordionWidget

    card = _widget({"ok": None, "code": "", "confirmed": [], "uncovered": []})

    assert card.findChildren(AccordionWidget) == []


def _shown(row):
    """The code a coloured row shows, with its markup read back off."""
    from ui.accordion_widget import QtGui

    return QtGui.QTextDocumentFragment.fromHtml(
        row.content_area.text()).toPlainText().replace("\u00a0", " ")


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
    assert _shown(row) == "assert True  # measured"
