try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.ui_helpers import UIHelpers

class HistoryItem(QtWidgets.QFrame):
    """A single row in the history list with selectable title and delete button."""
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
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        self.title_label = QtWidgets.QLabel(name)
        self.title_label.setObjectName("HistoryTitle")
        self.title_label.setStyleSheet("font-weight: 500; font-size: 13px; background: transparent;")
        # The list is scanned by name, so the name takes the room and the time
        # is what gives way — see resizeEvent.
        self.title_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                       QtWidgets.QSizePolicy.Preferred)

        self.time_label = QtWidgets.QLabel(self._format_relative_time(created_at))
        self.time_label.setObjectName("HistoryTime")
        self.time_label.setStyleSheet("font-size: 11px; background: transparent;")
        self.time_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        # An effect, not a colour: the theme decides what the text is, this only
        # says the time is worth less than the name (ADR-0011).
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
                font-size: 14px;
            }
        """)
        self.del_btn.clicked.connect(lambda: self.deleted.emit(self.index))
        layout.addWidget(self.del_btn)

        self.setStyleSheet("""
            QFrame#HistoryItem {
                border-radius: 8px;
                background-color: transparent;
            }
            QFrame#HistoryItem:hover {
                background-color: rgba(127, 127, 127, 40);
            }
        """)

    def showEvent(self, event):
        """
        The trash is tinted once the row is on screen, like every other icon.

        Asked in __init__ the button still answers with the OS palette, which
        is light under every FreeCAD dark theme (ADR-0011).
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
    Antigravity-style floating popup with session list.
    """
    session_selected = QtCore.Signal(int)
    session_deleted = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("HistoryPopup")
        self.setFixedHeight(400)

        # Opaque, so the theme paints it: translucent needed a colour of its own (ADR-0011).
        container = QtWidgets.QFrame(self)
        container.setObjectName("PopupContainer")
        frame = QtWidgets.QVBoxLayout(self)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.addWidget(container)

        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(8, 12, 8, 12)
        vbox.setSpacing(10)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Select a conversation")
        self.search_input.setObjectName("HistorySearch")
        self.search_input.setStyleSheet("""
            QLineEdit#HistorySearch {
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
        """)
        self.search_input.textChanged.connect(self.filter_items)
        vbox.addWidget(self.search_input)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
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
        Rebuild the list from `(position, session)` pairs.

        The position is given rather than counted, because the picker shows a
        subset — only the Sessions that have had an Intent — and the row it
        emits has to name the Session in the manager, not the row in this list
        (FCC-42). Counting was how a filter would open the wrong Session.
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

    def drop_from_the_dock(self):
        """Down from the top of the copilot dock, exactly as wide as it is."""
        dock = self.parentWidget()
        area = dock.widget() or dock
        self.setFixedWidth(area.width())
        self.move(area.mapToGlobal(QtCore.QPoint(0, 0)))
        self.show()
        self.search_input.setFocus()
