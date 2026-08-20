"""
Structured Assertion results (FCC-179): a pass or fail mark per acceptance
entry, above a shared block of the code that checked them.

Figma's `TestResults` (node 4593:4186, page Test Results, FCC-167 spec) is a
418x490, radius-10 card: a header, a progress strip, then the rows. The spec
is thin below that outer box -- the row set (`status=passed/skipped/failed/
running`) gives no per-row spacing or colour of its own -- so rows follow
FCC-171's type/spacing scale rather than a number the spec does not carry.
Progress and duration are left out rather than invented: `ASSERTION` fires
once, after the checks already finished, so there is nothing left running to
show a bar for and no timing in the event to print as a duration.

The checks run as one Python script, asserting every acceptance entry
together (`core/loop.py` :: `assertion`), so there is one `code` to show,
not one per entry -- every row's expansion shows the same script. A failed
run's rows are its `unmet` entries only: the script fails or passes as a
whole, so `confirmed` is always empty exactly when there is anything in
`unmet` to show, and there is no per-entry "skipped" mark here -- `uncovered`
entries are Review's to judge, not a third status this card invents one for.
"""
try:
    from PySide2 import QtCore, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.accordion_widget import AccordionWidget
from ui.design_tokens import FAIL, SPACE_MD, SPACE_SM, SPACE_XS, SUCCESS
from ui.typography import BODY_STRONG, css
from ui.ui_helpers import UIHelpers

PASSED = "passed"
FAILED = "failed"

_ICON = {PASSED: "circle_check", FAILED: "circle_x"}
_COLOUR = {PASSED: SUCCESS, FAILED: FAIL}
_MARK_SIZE = 16


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


class AssertionResultsWidget(QtWidgets.QFrame):
    """
    One row per confirmed or unmet acceptance entry, a green `circle_check`
    or red `circle_x` beside its description, collapsed under an
    `AccordionWidget` that expands to the Assertion code.

    Built once from the `ASSERTION` event's own data and never mutated after
    (`copilot_panel.py :: on_event`), the same read-only shape
    `ReviewVerdictWidget` takes.
    """

    def __init__(self, data):
        super().__init__()
        self.setObjectName("AssertionResultsCard")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_MD, SPACE_SM)
        outer.setSpacing(SPACE_XS)

        summary = summary_line(data)
        if summary:
            heading = QtWidgets.QLabel(summary)
            heading.setStyleSheet(css(BODY_STRONG))
            outer.addWidget(heading)

        code = data.get("code") or ""
        for description in data.get("confirmed") or ():
            outer.addWidget(self._row(PASSED, str(description), code))
        for description in data.get("unmet") or ():
            outer.addWidget(self._row(FAILED, str(description), code))

    def _row(self, state, description, code):
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_XS)

        mark = QtWidgets.QLabel()
        mark.setPixmap(UIHelpers.get_icon(_ICON[state], size=_MARK_SIZE, on=mark)
                       .pixmap(_MARK_SIZE, _MARK_SIZE))
        row.addWidget(mark, 0, QtCore.Qt.AlignTop)

        entry = AccordionWidget(description)
        entry.tint_toggle(_COLOUR[state])
        if code:
            entry.append_text(code)
        row.addWidget(entry, 1)

        return container
