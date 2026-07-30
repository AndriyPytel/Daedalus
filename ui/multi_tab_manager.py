# FreeCadCopilot/ui/multi_tab_manager.py
"""
ChatSessionManager — the Sessions, and the stack of chat areas that show them.

A Session is durable: a name, when it was created, the Document it worked on,
and the path of its event log. The index lives in the settings manager, the
transcript stays in the log the event stream already writes per event. Nothing
about a Session's content is held in a second place.

The stack holds one chat area per Session. A restored Session's chat area is
built empty and filled the first time it is shown, so opening the panel with a
hundred Sessions in history costs nothing until one is read.
"""
import os
import time
import uuid

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

# Sessions are pruned the way the addon's log files already are: oldest first,
# on a bounded count, rather than growing until the user notices.
KEEP = 50


def _discard(path):
    """Delete means gone, not hidden — but a log already missing is fine."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _log_dir() -> str:
    import FreeCAD

    directory = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "FreeCadCopilot",
                             "logs", "sessions")
    os.makedirs(directory, exist_ok=True)
    return directory


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

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack  = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)

        self._sessions: list[dict] = []

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
            }
        """)
        container.setObjectName("scrollContainer")
        return scroll

    # ── Persistence ──────────────────────────────────────────────────────────

    def _store(self):
        from utils import settings_manager

        return settings_manager.instance()

    def _save(self):
        """Write the index. The widget is this process's; the rest is durable."""
        self._store().set_sessions([
            {key: session[key] for key in ("name", "created_at", "document", "log")}
            for session in self._sessions
        ])

    def load(self):
        """
        Rebuild the Sessions from the index, newest last, and prune the tail.

        Each gets an empty chat area. Filling it is the panel's job, done on
        first show — a Session with hundreds of Steps must not be replayed
        because the panel opened.
        """
        stored = sorted(self._store().get_sessions(), key=lambda s: s.get("created_at") or 0)
        for stale in stored[:-KEEP]:
            _discard(stale.get("log"))
        for record in stored[-KEEP:]:
            self._add(record)
        if self._sessions:
            self._save()
        return self.count()

    # ── Public API ───────────────────────────────────────────────────────────

    def add_new_chat(self, tab_name: str = "New Chat", document: str = ""):
        idx, area = self._add({
            "name": tab_name,
            "created_at": time.time(),
            "document": document,
            "log": os.path.join(_log_dir(), "%s.jsonl" % uuid.uuid4().hex),
        })
        self.stack.setCurrentIndex(idx)
        self._save()
        self.session_added.emit(idx, tab_name)
        return idx, area

    def _add(self, record):
        """One Session: its record, its chat area, and the stack page holding it."""
        area = self._make_list()

        # Wrap in a plain container so QStackedWidget is happy
        container = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(container)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(area)

        idx = self.stack.addWidget(container)
        session = dict(record)
        session["widget"] = container
        session["restored"] = False
        self._sessions.append(session)
        return idx, area

    def remove_session(self, index: int):
        if not 0 <= index < len(self._sessions):
            return
        session = self._sessions.pop(index)
        _discard(session.get("log"))

        widget = session["widget"]
        self.stack.removeWidget(widget)
        widget.deleteLater()

        self._save()
        self.session_removed.emit(index)

        if self.stack.count() == 0:
            self.add_new_chat("Chat 1")

    def rename_session(self, index: int, new_name: str):
        """The log's path does not follow the name, so a rename cannot orphan it."""
        if not 0 <= index < len(self._sessions) or not new_name.strip():
            return
        self._sessions[index]["name"] = new_name.strip()
        self._save()
        self.session_renamed.emit(index, new_name.strip())

    def name_document(self, log: str, document: str):
        for session in self._sessions:
            if session["log"] == log and session["document"] != document:
                session["document"] = document
                self._save()
                return

    def get_current_chat_history(self) -> QtWidgets.QScrollArea | None:
        container = self.stack.currentWidget()
        if container is None:
            return None
        return container.layout().itemAt(0).widget()

    def count(self) -> int:
        return len(self._sessions)

    def get_session_info(self, index: int) -> dict | None:
        if 0 <= index < len(self._sessions):
            return self._sessions[index]
        return None

    def mark_restored(self, index: int) -> bool:
        """True the first time a Session is asked for; its transcript is due."""
        session = self.get_session_info(index)
        if session is None or session["restored"]:
            return False
        session["restored"] = True
        return True

    # ── Session switching ─────────────────────────────────────────────────────

    def switch_to(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            self.session_changed.emit(index)
