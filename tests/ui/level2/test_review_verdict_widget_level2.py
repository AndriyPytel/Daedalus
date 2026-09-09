"""
Level 2 -- the Review verdict widget FCC-178 adds, built for real against
FreeCAD's own Qt binding, offscreen.

A pure renderer (`ui/review_verdict_widget.py`'s own docstring): three
strings and a list of paths in, one `decided` signal out. The wiring from a
live `core.events.Event` to those arguments is `tests/test_panel_level2.py`'s,
the same split `test_queue_widget_level2.py` draws for the Queue.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _widget(view_paths=(), assertion_line="", verdict_line=""):
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.review_verdict_widget import ReviewVerdictWidget

    return ReviewVerdictWidget(view_paths, assertion_line, verdict_line)


def _a_view(tmp_path, name="iso.png"):
    from conftest import QtGui

    path = str(tmp_path / name)
    QtGui.QPixmap(40, 30).save(path)
    return path


def test_the_card_shows_the_assertion_and_verdict_lines():
    from conftest import QtWidgets

    card = _widget(assertion_line="✓ Acceptance checks passed: 2 confirmed.",
                   verdict_line="✗ Review: fail — the flange is mirrored")

    labels = [w.text() for w in card.findChildren(QtWidgets.QLabel)]
    assert "✓ Acceptance checks passed: 2 confirmed." in labels
    assert "✗ Review: fail — the flange is mirrored" in labels


def test_the_card_offers_exactly_approve_reject_and_start_over():
    from conftest import QtWidgets

    card = _widget(verdict_line="✗ Review: fail — no match")

    labels = sorted(b.text() for b in card.findChildren(QtWidgets.QPushButton))
    assert labels == ["Approve", "Reject", "Start Over"]


def test_clicking_approve_reject_and_start_over_each_emit_their_own_word():
    from conftest import QtWidgets

    from ui.review_verdict_widget import APPROVE, REJECT, START_OVER

    card = _widget(verdict_line="✗ Review: fail — no match")
    heard = []
    card.decided.connect(heard.append)

    for label, word in (("Approve", APPROVE), ("Reject", REJECT),
                        ("Start Over", START_OVER)):
        button = next(b for b in card.findChildren(QtWidgets.QPushButton)
                     if b.text() == label)
        button.click()
        assert heard[-1] == word


def test_start_over_is_the_one_subdued_button():
    from conftest import QtWidgets

    card = _widget(verdict_line="✗ Review: fail — no match")

    by_label = {b.text(): b for b in card.findChildren(QtWidgets.QPushButton)}
    effect = by_label["Start Over"].graphicsEffect()
    assert isinstance(effect, QtWidgets.QGraphicsOpacityEffect)
    assert effect.opacity() < 1.0
    assert by_label["Approve"].graphicsEffect() is None
    assert by_label["Reject"].graphicsEffect() is None


def test_the_four_views_are_shown_as_thumbnails(tmp_path):
    paths = [_a_view(tmp_path, "iso.png"), _a_view(tmp_path, "front.png"),
            _a_view(tmp_path, "top.png"), _a_view(tmp_path, "right.png")]

    card = _widget(view_paths=paths, verdict_line="✓ Review: pass")

    from conftest import QtWidgets

    pictures = [w for w in card.findChildren(QtWidgets.QLabel) if w.pixmap()]
    assert len(pictures) == 4


def test_no_views_means_no_picture_row(tmp_path):
    card = _widget(view_paths=[], verdict_line="✓ Review: pass")

    from conftest import QtWidgets

    pictures = [w for w in card.findChildren(QtWidgets.QLabel) if w.pixmap()]
    assert pictures == []


def test_a_missing_view_file_is_skipped_not_a_broken_picture(tmp_path):
    card = _widget(view_paths=["/does/not/exist.png", _a_view(tmp_path)],
                  verdict_line="✓ Review: pass")

    from conftest import QtWidgets

    pictures = [w for w in card.findChildren(QtWidgets.QLabel) if w.pixmap()]
    assert len(pictures) == 1
