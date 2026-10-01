try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import (CONTROL_DENSE, RADIUS_CONTROL, RADIUS_SURFACE, SPACE_SM,
                              SPACE_XS, WASH)
from ui.typography import BODY, CAPTION, LABEL, css
from ui.ui_helpers import UIHelpers

class HistoryItem(QtWidgets.QFrame):
    """
    A single row in the history list with selectable title and delete button:
the list is scanned by name, so the name takes the room and the time gives
way, see `resizeEvent`, and the time is dimmed by an opacity effect rather
than a colour of its own, since the theme decides what the text is and this
only says the time is worth less than the name. Name and time read `LABEL`
and `CAPTION` off the type scale, and the hover wash reads
`RADIUS_CONTROL`/`WASH` off the same module, the pair `ModelSelectorRow`
already hovers a popup row with, reused rather than a second version of the
same idea. Figma names no Session Picker component of its own, so this is a
design-language translation, not a component the spec hands over.
"""
    selected = QtCore.Signal(int)
    deleted = QtCore.Signal(int)

    def __init__(self, index, name, created_at: float, document: str = ""):
        super().__init__()
        self.index = index
        self.setObjectName("HistoryItem")
        self.setFixedHeight(28)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(document)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(SPACE_SM, 0, SPACE_SM, 0)
        layout.setSpacing(SPACE_SM)

        self.title_label = QtWidgets.QLabel(name)
        self.title_label.setObjectName("HistoryTitle")
        self.title_label.setStyleSheet(css(LABEL) + " background: transparent;")
        self.title_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                       QtWidgets.QSizePolicy.Preferred)

        self.time_label = QtWidgets.QLabel(self._format_relative_time(created_at))
        self.time_label.setObjectName("HistoryTime")
        self.time_label.setStyleSheet(css(CAPTION) + " background: transparent;")
        self.time_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        dim = QtWidgets.QGraphicsOpacityEffect(self.time_label)
        dim.setOpacity(0.6)
        self.time_label.setGraphicsEffect(dim)

        layout.addWidget(self.title_label, stretch=1)
        layout.addWidget(self.time_label)

        self.del_btn = QtWidgets.QPushButton()
        self.del_btn.setObjectName("DeleteButton")
        self.del_btn.setFixedSize(24, 24)
        self.del_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.del_btn.setStyleSheet("""
            QPushButton#DeleteButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 0px;
                min-width: %dpx; max-width: %dpx;
                font-size: 14px;
            }
        """ % (CONTROL_DENSE, CONTROL_DENSE))
        self.del_btn.clicked.connect(lambda: self.deleted.emit(self.index))
        layout.addWidget(self.del_btn)

        self.setStyleSheet("""
            QFrame#HistoryItem {{
                border-radius: {radius}px;
                background-color: transparent;
            }}
            QFrame#HistoryItem:hover {{
                background-color: {wash};
            }}
        """.format(radius=RADIUS_CONTROL, wash=WASH))

    def showEvent(self, event):
        """
        The trash is tinted once the row is on screen, like every other icon: asked
in `__init__` the button still answers with the OS palette, which is light
under every FreeCAD dark theme.
"""
        super().showEvent(event)
        self.del_btn.setIcon(UIHelpers.get_icon("trash", size=14, on=self.del_btn))

    def _format_relative_time(self, created_at: float) -> str:
        dt = QtCore.QDateTime.fromSecsSinceEpoch(int(created_at or 0))
        now = QtCore.QDateTime.currentDateTime()
        secs = dt.secsTo(now)
        if secs < 60: return "Just now"
        if secs < 3600: return f"{secs // 60}m ago"
        if secs < 86400: return f"{secs // 3600}h ago"
        return dt.toString("MMM d")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        margins = self.layout().contentsMargins()
        room = (self.width() - margins.left() - margins.right()
                - self.del_btn.width() - 2 * self.layout().spacing()
                - self.title_label.sizeHint().width())
        self.time_label.setVisible(room >= self.time_label.sizeHint().width())

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.selected.emit(self.index)
        super().mousePressEvent(event)

class HistoryPopup(QtWidgets.QFrame):
    """
    ``PopupContainer`` is opaque and carries no border: a translucent wash
composites onto whatever bare top-level window it is handed rather than the
dock this popup floats over, which is white regardless of FreeCAD's own
theme, and the user asked twice for no border around a popup like this.
`drop_from_the_dock` resolves the fill against the dock itself, the one
widget always at hand there, once FreeCAD's global stylesheet has painted
it. `CEILING` caps the popup the way `QueueWidget` caps its own open list,
sized to its content up to a fraction of the dock's own height, so a short
session list is a short popup rather than a fixed 400px box with empty room
and an oversized scrollbar.
"""
    CEILING = 0.6

    session_selected = QtCore.Signal(int)
    session_deleted = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("HistoryPopup")

        self._container = QtWidgets.QFrame(self)
        self._container.setObjectName("PopupContainer")
        frame = QtWidgets.QVBoxLayout(self)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.addWidget(self._container)

        vbox = QtWidgets.QVBoxLayout(self._container)
        vbox.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        vbox.setSpacing(SPACE_SM)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Select a conversation")
        self.search_input.setObjectName("HistorySearch")
        self.search_input.setStyleSheet("""
            QLineEdit#HistorySearch {{
                border-radius: {radius}px;
                padding: {pad_v}px {pad_h}px;
                {type}
            }}
        """.format(radius=RADIUS_CONTROL, pad_v=SPACE_XS, pad_h=SPACE_SM, type=css(BODY)))
        self.search_input.textChanged.connect(self.filter_items)
        vbox.addWidget(self.search_input)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("background: transparent;")
        
        self.list_container = QtWidgets.QWidget()
        self.list_container.setObjectName("ListContainer")
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QtWidgets.QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch()
        
        self.scroll.setWidget(self.list_container)
        vbox.addWidget(self.scroll)

    def populate(self, rows: list):
        """
        Rebuild the list from `(position, session)` pairs: the position is given
rather than counted, because the picker shows a subset -- only the Sessions
that have had an Intent -- and the row it emits has to name the Session in
the manager, not the row in this list. Counting was how a filter would open
the wrong Session.
"""
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, info in rows:
            item = HistoryItem(i, info["name"], info.get("created_at"),
                               info.get("document") or "")
            item.selected.connect(self._on_selected)
            item.deleted.connect(self._on_deleted)
            self.list_layout.insertWidget(self.list_layout.count() - 1, item)
            
    def _on_selected(self, index):
        self.session_selected.emit(index)
        self.close()

    def _on_deleted(self, index):
        self.session_deleted.emit(index)

    def filter_items(self, text):
        text = text.lower()
        for i in range(self.list_layout.count() - 1):
            widget = self.list_layout.itemAt(i).widget()
            if isinstance(widget, HistoryItem):
                widget.setVisible(text in widget.title_label.text().lower())

    def _content_height(self):
        """
        How tall the popup would be with every row it holds on screen right
        now, measured off the search bar and the list rather than a cached
        `sizeHint` -- a row `populate` just inserted has not had its layout
        request answered yet and would still measure as it did before the
        insertion (the same read `ui/model_selector.py::_laid_out` avoids,
        for the same reason).
        """
        self.list_layout.activate()
        container_layout = self._container.layout()
        container_layout.activate()
        margins = container_layout.contentsMargins()
        return (margins.top() + margins.bottom()
                + self.search_input.sizeHint().height()
                + container_layout.spacing()
                + self.list_container.sizeHint().height())

    def drop_from_the_dock(self):
        """
        Down from the top of the Daedalus dock, exactly as wide as it is, and tall
enough for its content up to `CEILING` of the dock's own height, never a
fixed 400px box regardless of a two-row history. The fill is resolved
against *area* -- the dock's own widget, already painted by FreeCAD's
global stylesheet -- rather than the search input or a row, neither of
which carries a background of its own to read back.
        """
        dock = self.parentWidget()
        area = dock.widget() or dock
        self._container.setStyleSheet(
            "QFrame#PopupContainer { background-color: %s; border-radius: %dpx; }"
            % (UIHelpers.surface_colour(area), RADIUS_SURFACE))
        self.setFixedWidth(area.width())
        self.setFixedHeight(min(self._content_height(), int(area.height() * self.CEILING)))
        self.move(area.mapToGlobal(QtCore.QPoint(0, 0)))
        self.show()
        self.search_input.setFocus()
