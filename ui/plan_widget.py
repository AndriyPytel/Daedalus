"""
The Plan: editable until Build locks it, then a read-only progress display.

Editing happens directly on the Steps (FCC-177), not through a typed
amendment: clicking a Step's text edits it in place, "+ Add Step" appends one
already open for editing, the trash icon on a row removes it, and dragging a
row -- `QListWidget`'s own InternalMove, the base component rather than a
hand-rolled drag -- reorders the Plan. `steps()` is what a Build click reads
to answer the Gate with.

`lock()` ends all of that -- called from the Gate's Build button, and called
again for safety by `highlight()`/`mark()` themselves, since a restored or
already-finished Session replays straight to those without a Build click ever
happening. From there this is what the widget was before FCC-177: one row per
Step, `highlight()` moves the running marker forward, `mark()` sets one row's
state directly, and both take the same 1-based index the event stream does.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import FAIL, RUNNING as RUNNING_COLOUR, SPACE_SM, SPACE_XS
from ui.typography import BODY, BODY_STRONG, css
from ui.ui_helpers import UIHelpers

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

_ICON = {PENDING: "circle", RUNNING: "circle_dot", DONE: "circle_check", FAILED: "circle_x"}
_MARK_COLOUR = {RUNNING: RUNNING_COLOUR, FAILED: FAIL}

_MARKER_SIZE = 14
_TRASH_SIZE = 14


class _StepDelegate(QtWidgets.QStyledItemDelegate):
    """
    Paints the trash icon a row's own text and drag handling leave no room
    for, and answers a click on it -- the one part of a `QListWidgetItem`
    plain text and an icon cannot carry on their own.
    """

    def __init__(self, widget):
        super().__init__(widget)
        self._widget = widget

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if self._widget.locked:
            return
        icon = UIHelpers.get_icon("trash", size=_TRASH_SIZE, on=self._widget)
        icon.paint(painter, self._trash_rect(option.rect))

    def editorEvent(self, event, model, option, index):
        if not self._widget.locked and event.type() == QtCore.QEvent.MouseButtonRelease \
                and self._trash_rect(option.rect).contains(event.pos()):
            self._widget.remove_step(index.row())
            return True
        return super().editorEvent(event, model, option, index)

    def _trash_rect(self, row_rect):
        y = row_rect.center().y() - _TRASH_SIZE // 2
        x = row_rect.right() - _TRASH_SIZE - SPACE_XS
        return QtCore.QRect(x, y, _TRASH_SIZE, _TRASH_SIZE)


class PlanWidget(QtWidgets.QFrame):
    """One row per Step, editable until `lock()`, marked with `highlight()`/`mark()` after."""

    def __init__(self, steps=(), title="Plan"):
        super().__init__()
        self.setObjectName("PlanCard")
        self.locked = False
        self._states = []

        self._outer = QtWidgets.QVBoxLayout(self)
        self._outer.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
        self._outer.setSpacing(SPACE_XS)

        self._title = QtWidgets.QLabel(title)
        self._title.setStyleSheet(css(BODY_STRONG))
        self._outer.addWidget(self._title)

        self._list = QtWidgets.QListWidget()
        self._list.setObjectName("PlanSteps")
        self._list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self._list.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked
                                   | QtWidgets.QAbstractItemView.SelectedClicked)
        self._delegate = _StepDelegate(self)
        self._list.setItemDelegate(self._delegate)
        self._outer.addWidget(self._list)

        self._add_button = QtWidgets.QPushButton(" Add Step")
        self._add_button.setObjectName("PlanAddStep")
        self._add_button.setFlat(True)
        self._add_button.setIcon(UIHelpers.get_icon("plus", size=14, on=self._add_button))
        self._add_button.clicked.connect(self.add_step)
        self._outer.addWidget(self._add_button)

        self._rows_container = None
        self._rows = []
        self.set_steps(steps)

    def set_steps(self, steps):
        """Replaces the Plan wholesale -- only ever called before `lock()`."""
        if self.locked:
            return
        self._list.clear()
        self._states = []
        for description in steps:
            self._append_item(str(description))

    def steps(self):
        """The Plan as it stands right now, edited or as-generated."""
        return [self._list.item(row).text() for row in range(self._list.count())]

    def add_step(self):
        if self.locked:
            return
        item = self._append_item("")
        self._list.setCurrentItem(item)
        self._list.editItem(item)

    def remove_step(self, row):
        if self.locked or not (0 <= row < self._list.count()):
            return
        self._list.takeItem(row)
        del self._states[row]

    def lock(self):
        """
        Ends editing and switches to the read-only marker display
        `highlight()`/`mark()` paint on -- built here, from `steps()`, rather
        than kept live from the start: nothing before Build needs a colour.
        """
        if self.locked:
            return
        self.locked = True
        current_steps = self.steps()
        self._list.setVisible(False)
        self._add_button.setVisible(False)

        self._rows_container = QtWidgets.QWidget()
        rows = QtWidgets.QVBoxLayout(self._rows_container)
        rows.setContentsMargins(0, SPACE_XS, 0, 0)
        rows.setSpacing(SPACE_XS)
        self._steps = current_steps
        self._states = [PENDING] * len(current_steps)
        self._rows = []
        for position, description in enumerate(current_steps):
            icon, text = self._row(rows)
            self._rows.append((icon, text))
            self._paint(position)
        self._outer.addWidget(self._rows_container)

    def highlight(self, index):
        """1-based, matching the Step numbering in the event stream."""
        self.lock()
        for position in range(len(self._states)):
            if position < index - 1 and self._states[position] == RUNNING:
                self._set_state(position, DONE)
        if 1 <= index <= len(self._states):
            self._set_state(index - 1, RUNNING)

    def mark(self, index, state):
        self.lock()
        if 1 <= index <= len(self._states):
            self._set_state(index - 1, state)

    def showEvent(self, event):
        """
        Icons are tinted once the widget is on screen, matching `QueueWidget`
        (FCC-172): a widget only carries the global stylesheet's colour once
        Qt has polished it.
        """
        super().showEvent(event)
        if self.locked:
            for position in range(len(self._states)):
                self._paint(position)
        else:
            for row in range(self._list.count()):
                self._list.item(row).setIcon(
                    UIHelpers.get_icon("circle", size=_MARKER_SIZE, on=self._list))
            self._add_button.setIcon(
                UIHelpers.get_icon("plus", size=14, on=self._add_button))

    def _append_item(self, text):
        item = QtWidgets.QListWidgetItem(text)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsDragEnabled)
        item.setIcon(UIHelpers.get_icon("circle", size=_MARKER_SIZE, on=self._list))
        self._list.addItem(item)
        self._states.append(PENDING)
        return item

    def _row(self, layout):
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_XS)
        icon = QtWidgets.QLabel()
        text = QtWidgets.QLabel()
        text.setWordWrap(True)
        text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row.addWidget(icon, 0, QtCore.Qt.AlignTop)
        row.addWidget(text, 1)
        layout.addWidget(container)
        return icon, text

    def _set_state(self, position, state):
        self._states[position] = state
        self._paint(position)

    def _paint(self, position):
        """
        Pending and done carry no colour -- the icon already says which, and a
        colour of our own is a light-theme value under a dark theme
        (ADR-0011). Running and failed do: those two must read the same in
        every theme, and `palette(highlight)` is the one role FreeCAD's themes
        keep meaningful.
        """
        icon_label, text_label = self._rows[position]
        state = self._states[position]
        icon_label.setPixmap(
            UIHelpers.get_icon(_ICON[state], size=_MARKER_SIZE, on=text_label)
            .pixmap(_MARKER_SIZE, _MARKER_SIZE))
        style = css(BODY_STRONG if state == RUNNING else BODY)
        colour = _MARK_COLOUR.get(state)
        if colour:
            style = "color:%s; %s" % (colour, style)
        text_label.setStyleSheet(style)
        text_label.setText("%d.  %s" % (position + 1, self._steps[position]))
