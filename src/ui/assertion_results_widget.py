"""
Structured Assertion results (FCC-179), rebuilt to Figma's `TestResults`
anatomy (FCC-169, Figma component B8):
a header (summary left, a derived fact right), a proportional progress
track, then one row per check.

The card itself is a surface -- `QFrame#AssertionResultsCard` already gets
its WASH fill, `HAIRLINE` border and `RADIUS_SURFACE` corners from the
panel's own stylesheet (`copilot_panel.py :: _build_qss`) -- so this module
only lays out what sits inside it.

The checks run as one Python script, asserting every acceptance entry
together (`core/loop.py` :: `assertion`), so there is one `code` to show,
not one per entry -- every row's expansion shows the same script. A failed
run's rows are its `unmet` entries only: the script fails or passes as a
whole, so `confirmed` is always empty exactly when there is anything in
`unmet` to show, and there is no per-entry "skipped" mark here -- `uncovered`
entries are Review's to judge, not a third status this card invents one for.
`ASSERTION` fires once, after the checks already finished, so there is no
duration to print in the header's secondary slot -- the percent passed is
shown there instead, since it is the one fact this card can derive rather
than invent.

Rule 1 of the ten in AI Elements' visual grammar:
state is never a repaint. A row keeps its background in every state; only
the leading icon (tinted `SUCCESS`/`FAIL`) and the trailing status word
change. The word itself carries no colour -- if it did, the row would be
recolouring itself by another name.
"""
import re

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.accordion_widget import AccordionWidget
from ui.design_tokens import (FAIL, ICON, SPACE_COMPOSER, SPACE_MD, SPACE_SM, SUCCESS, WASH)
from ui.typography import BODY_STRONG, CAPTION, css
from ui.ui_helpers import UIHelpers

PASSED = "passed"
FAILED = "failed"

_ICON = {PASSED: "circle_check", FAILED: "circle_x"}
_COLOUR = {PASSED: SUCCESS, FAILED: FAIL}
_WORD = {PASSED: "passed", FAILED: "failed"}

_WASH_CHANNELS = tuple(int(value) for value in re.findall(r"\d+", WASH))


def summary_line(data):
    """
    "N passed" or "N passed, M failed", from `confirmed` and `unmet` -- the
    same fields `ASSERTION` already carries. "" when the checks could not be
    written at all (`ok` is `None`): that is not a pass/fail count, it is the
    absence of one, and both callers -- `copilot_panel.py`'s `on_event` and
    `_put_the_review_verdict` -- fall back to the plain narration line for it.
    """
    if data.get("ok") is None:
        return ""
    passed = len(data.get("confirmed") or ())
    failed = len(data.get("unmet") or ())
    if not failed:
        return "%d passed" % passed
    return "%d passed, %d failed" % (passed, failed)


def _percent_passed(passed, failed):
    """
    The header's secondary fact: the one number the progress track already
    shows as area, put into words. "" with nothing measured, rather than a
    misleading "0%" for a run that never ran.
    """
    total = passed + failed
    if not total:
        return ""
    return "%d%%" % round(100.0 * passed / total)


def _progress_fractions(passed, failed):
    """
    Three fractions summing to 1.0, for `SUCCESS`/`FAIL`/`WASH` in that
    order -- `WASH` takes whatever is left of the pair's own total, which
    this card's data never leaves anything in (every entry lands in
    `confirmed` or `unmet`), but the track stays proportional to counts in
    general rather than assuming a two-way split by construction. Nothing
    measured yet reads as one whole `WASH` segment, not a hidden zero-width
    track.
    """
    total = passed + failed
    if not total:
        return (0.0, 0.0, 1.0)
    return (passed / total, failed / total, 0.0)


class _ElidingLabel(QtWidgets.QLabel):
    """
    One line, elided rather than wrapped -- section C's warning that the
    header row wraps at 400px, so a long summary or secondary fact keeps its
    12/16 padding instead of the row falling back to a second line.
    """

    def __init__(self):
        super().__init__()
        self._full = ""

    def set_full(self, text):
        self._full = text
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        metrics = QtGui.QFontMetrics(self.font())
        QtWidgets.QLabel.setText(
            self, metrics.elidedText(self._full, QtCore.Qt.ElideRight, max(0, self.width())))


class _ProgressTrack(QtWidgets.QWidget):
    """
    B8's 8px, square-cornered, contiguous progress bar -- painted rather
    than a `QProgressBar`, since a `QProgressBar` cannot show two coloured
    ranges (passed, failed) in one track.
    """

    def __init__(self):
        super().__init__()
        self.setFixedHeight(SPACE_SM)
        self.fractions = (0.0, 0.0, 1.0)

    def set_counts(self, passed, failed):
        self.fractions = _progress_fractions(passed, failed)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        colours = (QtGui.QColor(SUCCESS), QtGui.QColor(FAIL), QtGui.QColor(*_WASH_CHANNELS))
        x = 0
        for fraction, colour in zip(self.fractions, colours):
            width = round(fraction * self.width())
            if width:
                painter.fillRect(x, 0, width, self.height(), colour)
                x += width
        painter.end()


class AssertionResultsWidget(QtWidgets.QFrame):
    """
    Figma's `TestResults` card: a header, a progress track, then one row per
    confirmed or unmet acceptance entry -- a green `circle_check` or red
    `circle_x` beside its description and a plain status word after it,
    collapsed under an `AccordionWidget` that expands to the Assertion code.

    Built once from the `ASSERTION` event's own data and never mutated after
    (`copilot_panel.py :: on_event`), the same read-only shape
    `ReviewVerdictWidget` takes.
    """

    def __init__(self, data):
        super().__init__()
        self.setObjectName("AssertionResultsCard")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        summary = summary_line(data)
        if not summary:
            return

        passed_entries = list(data.get("confirmed") or ())
        failed_entries = list(data.get("unmet") or ())

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(SPACE_MD, SPACE_COMPOSER, SPACE_MD, SPACE_COMPOSER)
        header.setSpacing(SPACE_SM)

        heading = _ElidingLabel()
        heading.setStyleSheet(css(BODY_STRONG))
        heading.set_full(summary)
        header.addWidget(heading, 1)

        percent = _percent_passed(len(passed_entries), len(failed_entries))
        if percent:
            secondary = _ElidingLabel()
            secondary.setStyleSheet(css(CAPTION))
            secondary.set_full(percent)
            header.addWidget(secondary, 0)

        outer.addLayout(header)

        track = _ProgressTrack()
        track.set_counts(len(passed_entries), len(failed_entries))
        outer.addWidget(track)

        code = data.get("code") or ""
        for description in passed_entries:
            outer.addWidget(self._row(PASSED, str(description), code))
        for description in failed_entries:
            outer.addWidget(self._row(FAILED, str(description), code))

    def _row(self, state, description, code):
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        row.setSpacing(SPACE_SM)

        mark = QtWidgets.QLabel()
        mark.setPixmap(UIHelpers.get_icon(_ICON[state], size=ICON, on=mark,
                                          colour=_COLOUR[state]).pixmap(ICON, ICON))
        row.addWidget(mark, 0, QtCore.Qt.AlignTop)

        entry = AccordionWidget(description, code=bool(code))
        if code:
            entry.append_text(code)
        row.addWidget(entry, 1)

        badge = QtWidgets.QLabel(_WORD[state])
        badge.setObjectName("AssertionRowStatus")
        badge.setStyleSheet(css(CAPTION))
        row.addWidget(badge, 0, QtCore.Qt.AlignTop)

        return container
