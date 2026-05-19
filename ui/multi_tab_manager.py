# FreeCadCopilot/ui/multi_tab_manager.py
"""
ChatSessionManager

Replaces the QTabWidget-based MultiTabManager with a headless stacked widget.
The header QComboBox (injected from copilot_panel) drives session switching.
API surface is kept compatible with the old MultiTabManager so copilot_panel
only needs minor wiring changes.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui


class ChatSessionManager(QtWidgets.QWidget):
    """
    Manages independent chat sessions.
    - QStackedWidget holds one QListWidget per session (the chat history)
    - An external QComboBox (self.session_picker) is wired to the stack
    """

    session_changed = QtCore.Signal(int)   # emitted when the active session switches
    session_added   = QtCore.Signal(int, str) # idx, name
    session_removed = QtCore.Signal(int)      # idx
    session_renamed = QtCore.Signal(int, str) # idx, new_name

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack  = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)

        # Will be assigned externally by copilot_panel after the picker is
        # created in the header bar
        self.session_picker: QtWidgets.QComboBox | None = None

        self._sessions: list[dict] = [] # list of {"name": str, "widget": QWidget, "history_idx": int}

    # ── QScrollArea style helper ──────────────────────────────────────────────

    @staticmethod
    def _make_list() -> QtWidgets.QScrollArea:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        
        container = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(container)
        vl.setAlignment(QtCore.Qt.AlignTop)
        vl.setSpacing(2)
        vl.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(container)

        # Smooth scroll to max on range expansion (new content)
        vbar = scroll.verticalScrollBar()
        anim = QtCore.QPropertyAnimation(vbar, b"value")
        anim.setDuration(250)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        scroll._scroll_anim = anim  # prevent GC

        def _on_range_changed(min_val, max_val):
            if max_val > min_val:
                scroll._scroll_anim.stop()
                scroll._scroll_anim.setStartValue(vbar.value())
                scroll._scroll_anim.setEndValue(max_val)
                scroll._scroll_anim.start()

        vbar.rangeChanged.connect(_on_range_changed)

        # Smooth Kinetic Scrolling setup
        scroller = QtWidgets.QScroller.scroller(scroll.viewport())
        props = scroller.scrollerProperties()
        # Disable rubber-banding (overshoot) on desktop
        props.setScrollMetric(QtWidgets.QScrollerProperties.VerticalOvershootPolicy, QtWidgets.QScrollerProperties.OvershootAlwaysOff)
        props.setScrollMetric(QtWidgets.QScrollerProperties.OvershootDragResistanceFactor, 1.0)
        scroller.setScrollerProperties(props)
        QtWidgets.QScroller.grabGesture(scroll.viewport(), QtWidgets.QScroller.LeftMouseButtonGesture)

        scroll.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
                outline: none;
            }
            QWidget#scrollContainer {
                background-color: transparent;
            }
            QScrollBar:vertical {
                border: none;
                width: 6px;
            }
            QScrollBar::handle:vertical {
                border-radius: 3px;
                min-height: 20px;
                background: palette(mid);
            }
        """)
        container.setObjectName("scrollContainer")
        return scroll

    # ── Public API (compatible with old MultiTabManager) ──────────────────────

    def add_new_chat(self, tab_name: str = "New Chat"):
        lw  = self._make_list()

        # Wrap in a plain container so QStackedWidget is happy
        container = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(container)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(lw)

        idx = self.stack.addWidget(container)
        self.stack.setCurrentIndex(idx)
        
        session_data = {
            "name": tab_name,
            "widget": container,
            "state": {"history": []},
            "created_at": QtCore.QDateTime.currentDateTime()
        }
        self._sessions.append(session_data)
        
        if self.session_picker is not None:
            self.session_picker.addItem(tab_name)
            self.session_picker.setCurrentIndex(idx)

        self.session_added.emit(idx, tab_name)
        return idx, lw

    def remove_session(self, index: int):
        if 0 <= index < len(self._sessions):
            # Remove from data
            session = self._sessions.pop(index)
            widget = session["widget"]
            
            # Remove from stack
            self.stack.removeWidget(widget)
            widget.deleteLater()
            
            # Update picker if it exists
            if self.session_picker is not None:
                self.session_picker.removeItem(index)
            
            self.session_removed.emit(index)
            
            # If we deleted the last one, create a fresh one
            if self.stack.count() == 0:
                self.add_new_chat("New Chat")

    def rename_session(self, index: int, new_name: str):
        if 0 <= index < len(self._sessions):
            self._sessions[index]["name"] = new_name
            if self.session_picker is not None:
                self.session_picker.setItemText(index, new_name)
            self.session_renamed.emit(index, new_name)

    def get_current_chat_history(self) -> QtWidgets.QScrollArea | None:
        container = self.stack.currentWidget()
        if container is None:
            return None
        return container.layout().itemAt(0).widget()

    def get_current_state(self) -> dict:
        idx = self.stack.currentIndex()
        if 0 <= idx < len(self._sessions):
            return self._sessions[idx]["state"]
        return {}

    def update_current_state(self, state: dict):
        idx = self.stack.currentIndex()
        if 0 <= idx < len(self._sessions):
            self._sessions[idx]["state"] = state

    def count(self) -> int:
        return len(self._sessions)

    def get_session_info(self, index: int) -> dict | None:
        if 0 <= index < len(self._sessions):
            return self._sessions[index]
        return None

    # ── Session switching ─────────────────────────────────────────────────────

    def switch_to(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            self.session_changed.emit(index)
