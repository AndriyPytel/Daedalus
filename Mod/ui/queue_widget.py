"""
The Queue is the one line pinned above the composer that says what the run is doing now.

It only renders: it is told what happened and never asks the loop anything.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import SPACE_SM
from ui.typography import CAPTION, css


class _NowLabel(QtWidgets.QLabel):
    """
    One line, elided, whatever it is handed.

    Pinned above the composer, so it cannot wrap: the sentences that reach it
    run to ninety characters and more, and three of those lines are the
    transcript's bottom third on a docked panel.
    """

    def __init__(self):
        super().__init__("")
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

    def set_full(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self.setVisible(bool(self._full))
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        metrics = QtGui.QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, QtCore.Qt.ElideRight,
                                           max(0, self.width())))


class QueueWidget(QtWidgets.QFrame):
    """What is happening now, in one line pinned above the composer."""

    def __init__(self):
        super().__init__()
        self.setObjectName("QueueCard")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        outer.setSpacing(0)
        self._live = False
        self.setVisible(False)
        self._now_label = _NowLabel()
        self._now_label.setStyleSheet(css(CAPTION))
        outer.addWidget(self._now_label)

    def reset(self):
        """A new Intent replaces what the line says instead of adding a second Queue."""
        self._live = True
        self.setVisible(True)
        self._now_label.set_full("")

    def stand_down(self):
        """No run belongs to this chat at all: nothing is shown."""
        self._live = False
        self._now_label.set_full("")
        self.setVisible(False)

    def now(self, text):
        """
        Every sentence pinned under the transcript arrives here: the Step
        applied, the question the run is blocked on, the model it switched
        to, and how it stopped. It shows before any run has started too -- a
        provider retiring the model is said to a panel asked to do nothing yet.
        """
        self._now_label.set_full(text)
        self.setVisible(self._live or bool(text))
