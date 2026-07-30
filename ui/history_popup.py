try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

class HistoryItem(QtWidgets.QFrame):
    """A single row in the history list with selectable title and delete button."""
    selected = QtCore.Signal(int)
    deleted = QtCore.Signal(int)

    def __init__(self, index, name, created_at: float, document: str = ""):
        super().__init__()
        self.index = index
        self.setObjectName("HistoryItem")
        self.setFixedHeight(48)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        # Title and timestamp container
        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 8, 0, 8)

        self.title_label = QtWidgets.QLabel(name)
        self.title_label.setObjectName("HistoryTitle")
        self.title_label.setStyleSheet("font-weight: 500; font-size: 13px; background: transparent;")
        
        subtitle = self._format_relative_time(created_at)
        if document:
            subtitle += " · " + document
        self.time_label = QtWidgets.QLabel(subtitle)
        self.time_label.setObjectName("HistoryTime")
        self.time_label.setStyleSheet("font-size: 11px; opacity: 0.7; background: transparent;")
        
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.time_label)
        layout.addLayout(text_layout, stretch=1)

        # Delete button
        import os
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Resources", "icons", "trash.svg")
        self.del_btn = QtWidgets.QPushButton()
        if os.path.exists(icon_path):
            self.del_btn.setIcon(QtGui.QIcon(icon_path))
        else:
            self.del_btn.setText("🗑")
        
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

    def _format_relative_time(self, created_at: float) -> str:
        dt = QtCore.QDateTime.fromSecsSinceEpoch(int(created_at or 0))
        now = QtCore.QDateTime.currentDateTime()
        secs = dt.secsTo(now)
        if secs < 60: return "Just now"
        if secs < 3600: return f"{secs // 60}m ago"
        if secs < 86400: return f"{secs // 3600}h ago"
        return dt.toString("MMM d")

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
        self.setFixedWidth(280)
        self.setFixedHeight(400)

        # Opaque, so the theme paints it: translucent needed a colour of its own (ADR-0011).
        container = QtWidgets.QFrame(self)
        container.setObjectName("PopupContainer")
        container.setGeometry(0, 0, 280, 400)

        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(8, 12, 8, 12)
        vbox.setSpacing(10)

        # Search bar
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

        # Scroll area for items
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

    def populate(self, sessions_info: list):
        """Rebuild the list from session data."""
        # Clear existing
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        for i, info in enumerate(sessions_info):
            if info is None:
                continue
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
        # Populate will be called again by panel to refresh

    def filter_items(self, text):
        text = text.lower()
        for i in range(self.list_layout.count() - 1):
            widget = self.list_layout.itemAt(i).widget()
            if isinstance(widget, HistoryItem):
                widget.setVisible(text in widget.title_label.text().lower())

    def show_center(self):
        # Center on the primary screen
        # In PySide, QApplication.desktop() returns QDesktopWidget
        screen_geo = QtWidgets.QApplication.desktop().screenGeometry()
        center = screen_geo.center()
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        self.show()
        self.search_input.setFocus()
