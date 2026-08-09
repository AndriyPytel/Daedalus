# FreeCadCopilot/ui/multi_tab_manager.py
"""
ChatSessionManager — the stack of chat areas, one per Session.

The Sessions themselves are `core.sessions.Sessions`: the durable index, the
names, the pruning, whose transcript is due. None of that needs Qt, and it used
to be trapped in here behind one, which is why the tests that covered it had to
build a dock and reach into record dicts by hand.

What is left here is the half that is genuinely a widget. The stack holds one
chat area per Session, at the same positions, and this turns what changed into
signals. A restored Session's chat area is built empty and filled the first
time it is shown, so opening the panel with a hundred Sessions in history costs
nothing until one is read.
"""
from core.sessions import Sessions

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui


class ChatSessionManager(QtWidgets.QWidget):
    """
    Manages independent chat sessions.
    - QStackedWidget holds one chat area per Session
    - The header title label shows the active Session's name
    """

    session_changed = QtCore.Signal(int)   # emitted when the active session switches
    session_added   = QtCore.Signal(int, str) # idx, name
    session_removed = QtCore.Signal(int)      # idx
    session_renamed = QtCore.Signal(int, str) # idx, new_name

    def __init__(self, sessions=None):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack  = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)

        self.sessions = sessions if sessions is not None else Sessions()

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

        scroller = QtWidgets.QScroller.scroller(scroll.viewport())
        props = scroller.scrollerProperties()
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
            }
        """)
        container.setObjectName("scrollContainer")
        return scroll

    # ── The Sessions, through the pages that show them ───────────────────────

    def _new_page(self):
        """One chat area, in a stack page of its own, at the end."""
        area = self._make_list()

        container = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(container)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(area)

        self.stack.addWidget(container)
        return area

    def _drop_page(self, position: int):
        widget = self.stack.widget(position)
        if widget is not None:
            self.stack.removeWidget(widget)
            widget.deleteLater()

    def load(self):
        """Rebuild the Sessions from the index, each with an empty chat area."""
        count = self.sessions.load()
        while self.stack.count() < count:
            self._new_page()
        return count

    def add_new_chat(self, name: str = "New Chat", document: str = ""):
        idx = self.sessions.add(name, document)
        area = self._new_page()
        self.stack.setCurrentIndex(idx)
        self.session_added.emit(idx, name)
        return idx, area

    def begin(self, index: int, name: str) -> bool:
        """
        The Session's first Intent: it takes a name and enters the picker.

        Pruning happens here, because this is where the index grows, and it
        moves the Sessions after it — so the caller must re-read the live
        position rather than reuse the one it passed in.
        """
        session = self.sessions.at(index)
        started, dropped = self.sessions.begin(index, name)
        for position in dropped:
            self._drop_page(position)
            self.session_removed.emit(position)
        if started:
            self.session_renamed.emit(index, session["name"])
        return started

    def wants_a_title(self, index: int) -> bool:
        return self.sessions.wants_a_title(index)

    def title_arrived(self, log: str, title: str) -> bool:
        position = self.sessions.title_arrived(log, title)
        if position is None:
            return False
        self.session_renamed.emit(position, self.sessions.at(position)["name"])
        return True

    def remove_session(self, index: int):
        if not self.sessions.remove(index):
            return
        self._drop_page(index)
        self.session_removed.emit(index)

        if self.stack.count() == 0:
            self.add_new_chat("New Chat")

    def rename_session(self, index: int, new_name: str):
        if self.sessions.rename(index, new_name):
            self.session_renamed.emit(index, new_name.strip())

    def name_document(self, log: str, document: str):
        self.sessions.name_document(log, document)

    def get_current_chat_history(self) -> QtWidgets.QScrollArea | None:
        container = self.stack.currentWidget()
        if container is None:
            return None
        return container.layout().itemAt(0).widget()

    def count(self) -> int:
        return self.sessions.count()

    def get_session_info(self, index: int) -> dict | None:
        return self.sessions.at(index)

    def indexed_sessions(self) -> list:
        return self.sessions.indexed()

    def mark_restored(self, index: int) -> bool:
        return self.sessions.mark_restored(index)

    # ── Session switching ─────────────────────────────────────────────────────

    def switch_to(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            self.session_changed.emit(index)
