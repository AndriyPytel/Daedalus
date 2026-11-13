"""
ChatSessionManager is the stack of chat areas, one per Session. The
Sessions themselves are `core.sessions.Sessions`, the durable index with
the names, the pruning and whose transcript is due, none of which needs Qt;
what is left here is the half that is genuinely a widget, the stack holding
one chat area per Session, found by the Session's id. A restored Session's chat area is built empty and filled the
first time it is shown, so opening the panel with a hundred Sessions in
history costs nothing until one is read.
"""
from sdk import Sessions
from ui.design_tokens import SPACE_MD, SPACE_SM

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

    session_changed = QtCore.Signal(str)

    def __init__(self, sessions=None):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack  = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)

        self.sessions = sessions if sessions is not None else Sessions()
        self._pages = {}

    @staticmethod
    def _make_list() -> QtWidgets.QScrollArea:
        """
        One scrolling column for a Session's transcript: it ends in a stretch
rather than being laid out with `AlignTop`, since an aligned item is given
only its sizeHint and a wrapped label's sizeHint is a guess made at a width
this column does not have, which would leave every message clipped short of
its own text with the reserved space blank; the view follows the run only
from the bottom, measured against the range the user last saw rather than
the one the new message just created, so a user scrolled back keeps their
place; and the scroll animation is held on the scroll area because nothing
else references it and Qt would otherwise collect it mid-flight. Kinetic
scrolling is not grabbed on the viewport, since a `QScroller` holding the
left button takes the press before the labels do, so a drag over a message
pans the column instead of selecting its text, panning sideways far enough
that the Gate's Build button leaves the panel until another drag brings it
back, while the wheel and the bar scroll this column normally. The gutter
belongs to this column rather than to each widget in it, `SPACE_MD` on all
four sides the way AI Elements' Conversation carries the padding its
messages fill (set on the widgets instead, the two would add up wherever a
card follows a message), and the gap between them is the default `SPACE_SM`
rather than the `SPACE_MD` between two turns, since here a turn is a dozen
rows added to this column one at a time and that gap would otherwise repeat
between every line of one.
"""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        
        container = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(container)
        vl.setSpacing(SPACE_SM)
        vl.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        vl.addStretch(1)
        scroll.setWidget(container)

        vbar = scroll.verticalScrollBar()
        anim = QtCore.QPropertyAnimation(vbar, b"value")
        anim.setDuration(250)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        scroll._scroll_anim = anim

        scroll._last_max = vbar.maximum()

        def _is_following():
            return (scroll._scroll_anim.state() == QtCore.QAbstractAnimation.Running
                    or vbar.value() >= scroll._last_max - 4)

        def _on_range_changed(min_val, max_val):
            following = _is_following()
            scroll._last_max = max_val
            if max_val > min_val and following:
                scroll._scroll_anim.stop()
                scroll._scroll_anim.setStartValue(vbar.value())
                scroll._scroll_anim.setEndValue(max_val)
                scroll._scroll_anim.start()

        def _the_user_took_over(_action):
            """A wheel, a drag or a key beats the animation still chasing the end."""
            scroll._scroll_anim.stop()

        vbar.actionTriggered.connect(_the_user_took_over)
        vbar.rangeChanged.connect(_on_range_changed)

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

    def _new_page(self, session_id: str):
        """One chat area, in a stack page of its own, at the end."""
        area = self._make_list()

        container = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(container)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(area)

        self.stack.addWidget(container)
        self._pages[session_id] = container
        return area

    def drop_page(self, session_id: str):
        widget = self._pages.pop(session_id, None)
        if widget is not None:
            self.stack.removeWidget(widget)
            widget.deleteLater()

    def current_id(self) -> str | None:
        current = self.stack.currentWidget()
        return next((sid for sid, page in self._pages.items() if page is current), None)

    def clear_page(self, session_id: str):
        """
        Empty a Session's page back to nothing, in place, for a Session resynced
from its transcript after running while it was not on screen: the page
already carries whatever it showed the last time it was open, and replaying
into it unemptied would land the whole transcript a second time after that.
"""
        container = self._pages.get(session_id)
        if container is None or container.layout() is None:
            return
        layout = container.layout()
        old_area = layout.itemAt(0).widget() if layout.count() else None
        if old_area is not None:
            layout.removeWidget(old_area)
            old_area.deleteLater()
        layout.addWidget(self._make_list())

    def load(self):
        """Rebuild the Sessions from the index, each with an empty chat area."""
        self.sessions.load()
        for session_id in self.sessions.ids():
            self._new_page(session_id)

    def add_new_chat(self, name: str = "New Chat", document: str = ""):
        session_id = self.sessions.add(name, document)
        area = self._new_page(session_id)
        self.stack.setCurrentWidget(self._pages[session_id])
        return session_id, area

    def get_current_chat_history(self) -> QtWidgets.QScrollArea | None:
        container = self.stack.currentWidget()
        if container is None:
            return None
        return container.layout().itemAt(0).widget()

    def switch_to(self, session_id: str):
        if session_id in self._pages:
            self.stack.setCurrentWidget(self._pages[session_id])
            self.session_changed.emit(session_id)
