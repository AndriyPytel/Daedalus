"""
Level 2 -- `AccordionWidget.append_text` coalescing (FCC-282), built for real
against FreeCAD's own Qt binding, offscreen.

`content_area` has `setWordWrap(True)`, so every `setText` is a relayout of
the whole accumulated string, not just the new fragment: a token-delta stream
calling `append_text` once per delta made that relayout quadratic in the
final response's length. These tests prove several `append_text` calls made
within one pass of the event loop (before its `QTimer.singleShot(0, ...)`
fires) collapse into exactly one `setText`, with no delta lost or reordered,
and that `opens_onto_nothing`'s chevron recovery still fires on the first
call after a closed state.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _widget():
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.accordion_widget import AccordionWidget

    return AccordionWidget()


def test_several_append_text_calls_in_one_turn_produce_one_setText(monkeypatch):
    widget = _widget()
    calls = []
    real_setText = widget.content_area.setText

    def counting_setText(text):
        calls.append(text)
        return real_setText(text)

    monkeypatch.setattr(widget.content_area, "setText", counting_setText)

    widget.append_text("alpha ")
    widget.append_text("beta ")
    widget.append_text("gamma")

    assert calls == []

    QT_APP.processEvents()

    assert len(calls) == 1
    assert calls[0] == "alpha beta gamma"
    assert widget.content_area.text() == "alpha beta gamma"


def test_opens_onto_nothing_recovers_its_chevron_on_the_next_append_text():
    widget = _widget()
    widget.opens_onto_nothing()
    assert widget._detail is False

    widget.append_text("first word")
    QT_APP.processEvents()

    assert widget._detail is True


def test_a_flush_for_an_already_deleted_widget_does_not_stop_the_others():
    """
    A queued flush for a row whose C++ object is already gone -- its
    top-level container was dropped and garbage-collected before the
    event-loop turn ran -- must not abort the rest of that turn's queued
    flushes: an unhandled RuntimeError in one QTimer.singleShot callback
    otherwise stops PySide from running the ones queued behind it in the
    same processEvents() call, which is what a single stale row would do
    to every accordion still streaming in the same turn.
    """
    from conftest import QtWidgets

    def make_and_drop():
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        doomed = _widget()
        doomed.append_text("never seen")
        layout.addWidget(doomed)

    for _ in range(3):
        make_and_drop()

    other = _widget()
    other.append_text("still seen")

    QT_APP.processEvents()

    assert other.content_area.text() == "still seen"
