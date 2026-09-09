"""
The Review verdict card: Review's own evidence, above Approve / Reject /
Start Over (FCC-178).

Figma's Confirmation page names only the two primary actions literally --
`_ConfirmationActions` (node 4346:15375) is a bordered button beside a filled
one, no icon, no third button. Start Over is the epic's own addition
(FCC-169's Solution Notes: "a third, less prominent action alongside
Approve/Reject"), so it reuses `PillButton`'s own chrome rather than a shape
Figma never drew, subdued the way `AccordionWidget.mark_as_reasoning` subdues
reasoning -- an opacity effect, not a colour of its own (ADR-0011).

`_THUMBNAIL` sizes the four viewport screenshots to a row across the panel:
Figma's Confirmation page gives no screenshot size of its own, so this reads
`copilot_panel.ATTACHMENT_WIDTH` (320px, "wide enough to recognise the
drawing, narrow enough that it is not the whole transcript") as the panel's
own idea of a picture's width instead of inventing a second one, split four
ways with `SPACE_SM` gaps between.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import SPACE_SM, SPACE_XS
from ui.typography import BODY, CAPTION, css

APPROVE = "approve"
REJECT = "reject"
START_OVER = "start_over"

_THUMBNAIL = (320 - 3 * SPACE_SM) // 4


class ReviewVerdictWidget(QtWidgets.QFrame):
    """
    Review's four views, the acceptance-checks summary and Review's own
    verdict, above the three buttons that answer it.

    Read-only once built: `copilot_panel.py :: on_revision` builds one per
    Ask, from whatever `_update_review_evidence` already read off the stream,
    so nothing here changes after construction the way `PlanWidget`'s rows
    do.
    """

    decided = QtCore.Signal(str)

    def __init__(self, view_paths=(), assertion_line="", verdict_line=""):
        super().__init__()
        self.setObjectName("ReviewVerdictCard")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        outer.setSpacing(SPACE_SM)

        views_row = QtWidgets.QHBoxLayout()
        views_row.setSpacing(SPACE_XS)
        shown = False
        for path in view_paths:
            picture = QtGui.QPixmap(path or "")
            if picture.isNull():
                continue
            thumbnail = QtWidgets.QLabel()
            thumbnail.setPixmap(picture.scaledToWidth(
                _THUMBNAIL, QtCore.Qt.SmoothTransformation))
            views_row.addWidget(thumbnail)
            shown = True
        if shown:
            views_row.addStretch(1)
            outer.addLayout(views_row)

        if assertion_line:
            assertion_label = QtWidgets.QLabel(assertion_line)
            assertion_label.setWordWrap(True)
            assertion_label.setStyleSheet(css(CAPTION))
            outer.addWidget(assertion_label)

        verdict_label = QtWidgets.QLabel(verdict_line)
        verdict_label.setWordWrap(True)
        verdict_label.setStyleSheet(css(BODY))
        outer.addWidget(verdict_label)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(SPACE_SM)

        reject = QtWidgets.QPushButton("Reject")
        reject.setObjectName("PillButton")
        reject.clicked.connect(lambda: self.decided.emit(REJECT))
        buttons.addWidget(reject)

        approve = QtWidgets.QPushButton("Approve")
        approve.setObjectName("ReviewApprove")
        approve.clicked.connect(lambda: self.decided.emit(APPROVE))
        buttons.addWidget(approve)

        buttons.addStretch(1)

        start_over = QtWidgets.QPushButton("Start Over")
        start_over.setObjectName("PillButton")
        start_over.setFlat(True)
        subdued = QtWidgets.QGraphicsOpacityEffect(start_over)
        subdued.setOpacity(0.6)
        start_over.setGraphicsEffect(subdued)
        start_over.clicked.connect(lambda: self.decided.emit(START_OVER))
        buttons.addWidget(start_over)

        outer.addLayout(buttons)
