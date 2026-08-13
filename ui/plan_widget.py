"""
The Plan, as a list with the current Step highlighted.

Purely a renderer: it is told what to show and never asks the loop anything.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

_MARK = {PENDING: "○", RUNNING: "▸", DONE: "✓", FAILED: "✗"}


class PlanWidget(QtWidgets.QFrame):
    """One row per Step. `highlight(n)` moves the marker; earlier rows go done."""

    def __init__(self, steps=(), title="Plan"):
        super().__init__()
        self.setObjectName("PlanCard")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 24, 8)
        outer.setSpacing(4)

        self._title = QtWidgets.QLabel(title)
        self._title.setStyleSheet("font-weight: 600; font-size: 12px;")
        outer.addWidget(self._title)

        self._rows_layout = QtWidgets.QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 2, 0, 0)
        self._rows_layout.setSpacing(2)
        outer.addLayout(self._rows_layout)

        self._rows = []
        self._steps = []
        self._states = []
        self.set_steps(steps)

    def set_steps(self, steps):
        for row in self._rows:
            row.setParent(None)
        self._rows, self._steps, self._states = [], list(steps), []

        for position in range(len(self._steps)):
            row = QtWidgets.QLabel()
            row.setWordWrap(True)
            row.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self._rows_layout.addWidget(row)
            self._rows.append(row)
            self._states.append(PENDING)
            self._paint(position)

    def highlight(self, index):
        """1-based, matching the Step numbering in the event stream."""
        for position in range(len(self._rows)):
            if position < index - 1 and self._states[position] == RUNNING:
                self._set_state(position, DONE)
        if 1 <= index <= len(self._rows):
            self._set_state(index - 1, RUNNING)

    def mark(self, index, state):
        if 1 <= index <= len(self._rows):
            self._set_state(index - 1, state)

    def _set_state(self, position, state):
        self._states[position] = state
        self._paint(position)

    def _paint(self, position):
        """
        Pending and done carry no colour — the ○ / ✓ marker already says which,
        and a colour of our own is a light-theme value under a dark theme (ADR-0011).
        Running and failed do: those two must read the same in every theme, and
        palette(highlight) is the one role FreeCAD's themes keep meaningful.
        """
        state = self._states[position]
        colour = {RUNNING: "palette(highlight)", FAILED: "#f44336"}.get(state, "")
        style = "font-weight:%s;" % ("600" if state == RUNNING else "400")
        if colour:
            style = "color:%s; %s" % (colour, style)
        self._rows[position].setText(
            '<span style="%s">%d. %s &nbsp;%s</span>'
            % (style, position + 1, _MARK[state], self._steps[position])
        )
        self._rows[position].setStyleSheet("font-size: 12px; padding: 1px 0px;")
