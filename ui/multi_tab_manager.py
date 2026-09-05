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
from copilot.sdk import Sessions
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

    session_changed = QtCore.Signal(int)
    session_added   = QtCore.Signal(int, str)
    session_removed = QtCore.Signal(int)
    session_renamed = QtCore.Signal(int, str)

    def __init__(self, sessions=None):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack  = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)

        self.sessions = sessions if sessions is not None else Sessions()

    @staticmethod
    def _make_list() -> QtWidgets.QScrollArea:
        """
        One scrolling column for a Session's transcript.

        The column ends in a stretch rather than being laid out with AlignTop:
        an aligned item is given its sizeHint and nothing else, and a wrapped
        label's sizeHint is a guess made at a width this column does not have,
        so every message comes out short of its own text, clipped, with the
        reserved space left blank.

        The view follows the run only from the bottom, so a user scrolled back
        to read keeps their place. "At the bottom" is measured against the
        range the user last saw, not the one the new message just created.

        The scroll animation is held on the scroll area because nothing else
        references it and Qt would otherwise collect it mid-flight.

        The gutter belongs to this column, not to each widget in it: SPACE_MD
        on all four sides, the way AI Elements' Conversation carries the
        padding its messages fill. Set on the widgets instead, the two add up
        wherever a card follows a message, and a widget that forgets sits edge
        to edge beside one that does not.

        The gap between them is the default SPACE_SM rather than the SPACE_MD
        that design language puts between two turns: a turn there is one
        widget holding its own sub-blocks, and here a turn is a dozen rows
        added to this column one at a time, so the gap between turns would be
        the gap between every line of one.
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

    def clear_page(self, position: int):
        """
        Empty a Session's page back to nothing, in place.

        For a Session resynced from its transcript after running while it was
        not on screen (FCC-181): the page already carries whatever it showed
        the last time it was open, and replaying into it unemptied would land
        the whole transcript a second time after that.
        """
        container = self.stack.widget(position)
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

    def title_arrived(self, session_id, title: str) -> bool:
        position = self.sessions.title_arrived(session_id, title)
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

    def name_document(self, session_id, document: str, document_id: str = ""):
        self.sessions.name_document(session_id, document, document_id)

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

    def switch_to(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            self.session_changed.emit(index)
