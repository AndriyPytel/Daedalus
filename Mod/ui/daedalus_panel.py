"""The panel is a renderer of the event stream: it does not drive the loop, it
sends one Intent then renders whatever the loop emits,
`DaedalusPanel.on_event` a switch over event kinds and nothing else, so the
same stream is the session log and a frozen UI is impossible since the loop
runs on a worker thread and only emits. The panel has no colours, since
FreeCAD paints its UI with one global stylesheet and leaves QPalette at
whatever the OS handed over, so what is left in the stylesheet is geometry
and typography. `RESTORE_CHUNK` bounds events replayed per event-loop turn
when a Session opens, `ATTACHMENT_WIDTH` bounds an attached picture's
width, `FOLDS_OVER` is how long a narrated line may be before it folds into a row
and `TITLE_KEPT` how much of its sentence titles it, while every
`sdk` import is deferred to the method that needs it since the SDK
reaches `core`, which imports FreeCAD's Qt bindings not yet guaranteed to
exist at import time.
"""
import threading

import FreeCAD

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from .chat_widgets        import TextMessageWidget, AssistantMessageWidget
from .multi_tab_manager   import ChatSessionManager
from .upload_widget       import FileChip, UploadWidget, FileChipBar
from .accordion_widget    import AccordionWidget
from .queue_widget        import QueueWidget
from ui.design_tokens     import (CONTROL, CONTROL_DENSE, HAIRLINE, ICON, ICON_DENSE, RADIUS_CONTROL,
                                  RADIUS_SURFACE, RUNNING, SPACE_COMPOSER, SPACE_MD,
                                  SPACE_SM, SPACE_XS, WASH)
from ui.typography        import BODY, BODY_STRONG, CAPTION, LABEL, css
from bridge import provider_manager
from sdk import Sessions, log_id
from ui.ui_helpers import UIHelpers


def _build_qss() -> str:
    """
    The panel stylesheet holds only geometry and typography, since every colour
asking `palette(...)` came back a light-theme value under FreeCAD's dark
theme, so leaving colour out hands these widgets to FreeCAD's global
stylesheet including themes that did not exist when this was written. Three
rules off the user's Figma file decide most of it: separation takes the
cheapest form that still reads, nothing, then a one-pixel hairline with no
fill, then a wash, then a filled surface only for the composer, the Plan,
and a results card; state is never a repaint, so a control keeps its fill
and swaps its icon and nothing here has a `:disabled` or an error colour;
and exactly one control on screen carries the accent fill, Send or the
primary answer to whatever is being asked, with the keyboard focus as the
one exception, drawn in the accent on the hairline the composer already has
so it costs no layout. Where a rule pins a dimension it also resets padding
and border, since Qt's min/max-width size the content rect and the theme's
padding is added on top of them.
"""
    return f"""
        /* Panel root */
        QWidget {{
            font-family: {UIHelpers.get_ui_font()};
            {css(BODY)}
        }}

        QDockWidget::title {{ padding: 0px; height: 0px; }}

        /* The header, ruled off from the transcript under it */
        QFrame#HeaderBar {{
            border: none;
            border-bottom: 1px solid {HAIRLINE};
        }}

        QLabel#HeaderTitle {{ {css(BODY_STRONG)} }}

        /* Icon-only actions: one hit-target size, a wash under the pointer,
           and no other state. */
        QPushButton#IconButton {{
            background: transparent;
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px;
            min-width: {CONTROL}px; max-width: {CONTROL}px;
            min-height: {CONTROL}px; max-height: {CONTROL}px;
        }}

        QPushButton#IconButton:hover {{ background-color: {WASH}; }}

        /* Usage/cost running total */
        QLabel#UsageTotal {{
            {css(CAPTION)}
            padding: 0px {SPACE_XS}px;
        }}

        /* The empty chat still tells the user where they stand */
        QLabel#EmptyState {{
            {css(CAPTION)}
            padding: {SPACE_MD}px;
        }}

        /* The composer: a surface, so a fill and a hairline both, and the one
           twelve in the spacing scale around the text. */
        QFrame#InputContainer {{
            background-color: {WASH};
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_SURFACE}px;
            margin: 0px {SPACE_MD}px {SPACE_MD}px {SPACE_MD}px;
        }}

        /* Which of the panel's fields the keyboard is in. The hairline is
           already there, so the ring costs no layout: the same one pixel,
           repainted in the accent. */
        QFrame#InputContainer[focused="true"] {{
            border: 1px solid {RUNNING};
        }}

        QTextEdit#ChatInput {{
            border: none;
            background: transparent;
            {css(BODY)}
            padding: {SPACE_COMPOSER}px;
        }}

        /* The Queue, pinned above the composer: a hairline and no fill --
           the fills belong to what the reader stops on, and this is read at a
           glance rather than stopped on. */
        QFrame#QueueCard {{
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_SURFACE}px;
            margin: 0px {SPACE_MD}px {SPACE_SM}px {SPACE_MD}px;
        }}

        /* What is being asked is asked in the open: the Confirmation this
           follows carries no card at all, and the accent on its answer is
           what marks it out. */
        QFrame#GateCard {{ border: none; }}

        /* An action offered beside a primary one: a label, a hairline, and no
           fill of its own. */
        QPushButton#PillButton {{
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px {SPACE_SM}px;
            min-width: 0px;
            {css(LABEL)}
        }}

        QPushButton#PillButton:hover {{ background-color: {WASH}; }}

        /* A control that is picked between rather than pressed: the composer's
           strip, where the attach button sets the idiom -- no edge, no fill,
           and a wash under the pointer. An outline here would fence off what
           is a current value rather than a thing to press. */
        QPushButton#PickerButton {{
            background: transparent;
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px {SPACE_XS}px;
            min-width: 0px;
            {css(LABEL)}
        }}

        QPushButton#PickerButton:hover {{ background-color: {WASH}; }}

        /* A call the run made, in the shape Figma's Tool card takes: a
           hairline, no fill, and the control radius rather than a surface's. */
        QFrame#ToolCard {{
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
        }}

        /* The one accent-filled control on screen -- Send, or the answer the
           card being shown is asking for. The glyph takes the colour the
           theme pairs with that fill; left at the ordinary foreground it is
           the one contrast a theme guarantees, broken by hand. */
        QPushButton#PrimaryButton, QPushButton#ReviewApprove {{
            background-color: {RUNNING};
            color: palette(highlighted-text);
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: {SPACE_SM}px {SPACE_MD}px;
            min-width: 0px;
            {css(LABEL)}
        }}

        /* Send and Stop: the glyph alone, in the idiom the attach button and
           the pickers beside them already use. A filled pill here was the one
           thing in the composer's strip wearing a colour, and it read as a
           second primary action. */
        QPushButton#SendButton {{
            background: transparent;
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px;
            min-width: {CONTROL}px; max-width: {CONTROL}px;
            min-height: {CONTROL}px; max-height: {CONTROL}px;
        }}

        QPushButton#SendButton:hover {{ background-color: {WASH}; }}

        /* Scrollbars */
        QScrollBar:vertical {{
            border: none; background: transparent; width: 6px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            min-height: 20px; border-radius: 3px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


def _format_cost(usd: float) -> str:
    """The running total, in dollars. Four decimal places for calls under a cent."""
    if usd and usd < 0.01:
        return "$%.4f" % usd
    return "$%.2f" % usd


def _format_elapsed(seconds: float) -> str:
    """
    Wall-clock duration, zero-padded to M:SS when under an hour, H:MM:SS when over.

    90 seconds -> "1:30"
    3660 seconds -> "1:01:00"
    45 seconds -> "0:45"
    """
    if seconds < 0:
        seconds = 0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def _folded(line: str) -> "QtWidgets.QWidget":
    """
    *line* as a closed row titled by its first sentence.

    The title is a sentence rather than a label of our own, because what is
    being folded is prose the run wrote and the panel cannot summarise: the
    first sentence is the one part of it written to be read first.
    """
    head = line.strip().split(". ")[0].split("\n")[0].strip()
    row = AccordionWidget(head[:TITLE_KEPT] + ("…" if len(head) > TITLE_KEPT else ""))
    row.append_text(line)
    return row


def _beside_the_intent(widget) -> "QtWidgets.QWidget":
    """
    *widget* pushed to the right margin, where the user's own turn stands.

    What the run was shown is part of what the user said, so an attachment
    lines up under their bubble rather than at the left margin the run
    answers from.
    """
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addStretch(1)
    layout.addWidget(widget)
    return row


def _at_the_left_margin(widget) -> "QtWidgets.QWidget":
    """
    *widget* held to the left margin, where the run's own turn stands.

    A chip sizes itself to its name, and a layout given one with nothing beside
    it centres it in the column; the stretch on the right is what keeps it
    lined up with the sentence above it.
    """
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    layout.addStretch(1)
    return row



class _PasteAwareTextEdit(QtWidgets.QTextEdit):
    """The composer: Enter sends, Shift+Enter a newline, Ctrl+V images go
    to the UploadWidget. A pasted image is consumed here, not handed on,
    so its raw bytes never land in the text field.
    `focus_changed` draws the card's own focus ring since Qt has no
    `:focus-within`; the ring belongs on the card, not this widget.
    """

    paste_image_requested = QtCore.Signal()
    send_requested = QtCore.Signal()
    focus_changed = QtCore.Signal(bool)

    def focusInEvent(self, event: QtGui.QFocusEvent):
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event: QtGui.QFocusEvent):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key_V and (event.modifiers() & QtCore.Qt.ControlModifier):
            mime = QtWidgets.QApplication.clipboard().mimeData()
            if mime.hasImage():
                self.paste_image_requested.emit()
                return

        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) \
                and not (event.modifiers() & QtCore.Qt.ShiftModifier):
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class EventBridge(QtCore.QObject):
    """
    Carries loop events from the worker thread to the GUI thread.

    The loop knows nothing about Qt: it emits into an EventStream, this
    subscribes, and Qt's queued connection does the thread hop.
    """

    event = QtCore.Signal(object)

    def subscriber(self):
        return self.event.emit


class AskTheUser(QtCore.QObject):
    """Blocks the worker thread until the user answers, on the GUI thread: the Owned Object rule is asked from the
loop's thread and answered by a person, so the signal crosses to the GUI
thread and the worker waits on an Event. A run parked here waits on a
person who may be away, so `_ask` pages them through FreeCAD's Notification
Area before blocking.
"""

    owned_objects = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self._answered = threading.Event()
        self._answer = None

    def about_objects_we_do_not_own(self, kinds):
        """
        Whether the assistant may delete something the user made themselves.

        `abandon` releases this with an Approval, like every other waiting
        question, and an Approval is truthy — so what makes Stop a refusal
        rather than consent is `ApplyPipeline`, which takes nothing but an
        explicit yes. It is the choke point every asker routes through, so the
        guard is there rather than repeated in each of them.
        """
        return self._ask(self.owned_objects, kinds,
                         "Daedalus wants to delete something it did not create and "
                         "needs your permission.")

    def _ask(self, signal, payload, notify):
        """
        Emit the question, then block until someone answers it: a receiver in this
object's own thread answers inside `emit`, so the Event is already set and
the wait returns at once, while a receiver anywhere else answers later,
over a queued connection, and the wait is what holds the worker until it
does. The dangerous case is asked from this object's own thread with nobody
to answer inline, since the queued answer can only be delivered by an event
loop this very thread is now blocked out of, so `wait()` never returns and
the process deadlocks with no traceback and no CPU to show for it; refusing
to wait names the fault instead. *notify* reaches FreeCAD's own
Notification Area once it is clear the wait is real, since an answer
already sitting in `_answered`, the same-thread case above and a test's
direct connection, is not a person walking away from a two-hour run and
does not need paging back.
"""
        self._answered.clear()
        self._answer = None
        signal.emit(payload)
        if not self._answered.is_set() and QtCore.QThread.currentThread() == self.thread():
            raise RuntimeError(
                "%s was asked on the thread that has to answer it; "
                "no event loop can deliver the answer." % signal
            )
        if not self._answered.is_set():
            from bridge.logger import notify_user
            notify_user(notify)
        self._answered.wait()
        return self._answer

    def answer(self, value):
        self._answer = value
        self._answered.set()

    def abandon(self):
        """Release a waiting question so Stop is not swallowed by a prompt."""
        if not self._answered.is_set():
            self._answer = False
            self._answered.set()


class LoopWorker(QtCore.QObject):
    """
    Runs one Intent through the loop on a worker thread: it never reports
progress itself, the event stream does that, and all this signals is that
the run is over, with `finished` carrying the transcript the run belongs to
so the panel can connect a bound method of its own rather than a lambda
closing over it. This object lives on the worker thread, and Qt uses the
*sender* as the context of a connection made to a plain callable, so an
`AutoConnection` to a lambda compares the worker thread against itself,
resolves to a direct call, and runs the panel's slot on the worker thread,
measured as sixteen `QObject::setParent` (different thread) warnings, one
`QObject::killTimer` (another thread) warning, and a segfault inside
`queue_widget.py :: _NowLabel.set_full`; a bound method of a main-thread
QObject gives Qt that QObject as the context instead, which is what makes
the hop queued, and `Qt.QueuedConnection` on the lambda does not help since
it still posts to the context's thread, which is still this object. The
client is held rather than built per run, and is dropped so that the next
run rebuilds it from settings when the model changes.
"""

    finished = QtCore.Signal(object, str)
    document_named = QtCore.Signal(str, str, str)

    def __init__(self, bridge: EventBridge, ask: AskTheUser):
        super().__init__()
        self.bridge = bridge
        self.ask = ask
        self.sdk_client = None
        self._document = None
        self._stop = threading.Event()
        self._abort = threading.Event()

    @property
    def document(self):
        return self._document

    @document.setter
    def document(self, value):
        self._document = value
        self.sdk_client = None

    @property
    def client(self):
        """The LLM client, for the panel to clear when the model changes."""
        if self.sdk_client is None:
            return None
        return self.sdk_client._llm

    @client.setter
    def client(self, value):
        if self.sdk_client is not None:
            self.sdk_client.llm = value

    @property
    def pipeline(self):
        """The ApplyPipeline, for abort() to call shutdown()."""
        if self.sdk_client is None:
            return None
        return self.sdk_client.pipeline

    def stop(self):
        """
        Ends the run without waiting out a provider call already in flight.

        `cancelled()` is only polled between provider calls
        (`core/agent/solo.py`), and one
        of those calls is a `claude` subprocess under a ceiling of up to 1500
        seconds (`core/llm/claude.py :: _CEILING`). Killing this run's own live
        processes ends that call at once, the same as `abort`, while leaving
        the kernel alone so the in-flight Step can still checkpoint.
        """
        self._stop.set()
        if self.sdk_client is not None:
            self.sdk_client.kill_provider()
        self.ask.abandon()

    def abort(self):
        """Kills the kernel now, discarding whatever Step is in flight.
        Where `stop` lets the kernel checkpoint the Step on its own, this
        also calls `pipeline.shutdown` -- the same call a Session's own
        teardown makes -- so the `Kernel.check` call in flight comes back a
        crash and `core/kernel.py`'s reopen-the-checkpoint recovery keeps it
        out of the Document. `Kernel.kill` only drops the process, so the
        next Step run restarts it; `sdk_client.kill_provider()` ends this
        run's own `claude` process the same way `stop` does.
        """
        self._abort.set()
        if self.sdk_client is not None:
            self.sdk_client.kill_provider()
        if self.pipeline is not None:
            self.pipeline.shutdown()
        self._stop.set()
        self.ask.abandon()

    def _get_sdk_client(self):
        """
        The SDK Client for this Document, its own `Kernel()` (`Client`'s
        default) and all: created once, reused for every Intent, and dropped
        only when the model changes so the next run rebuilds it from settings.
        """
        if self.sdk_client is None:
            from bridge.main_thread import on_main_thread
            from sdk import Client
            self.sdk_client = Client(
                document=self._document,
                confirm=self.ask.about_objects_we_do_not_own,
                dispatch=on_main_thread)
        return self.sdk_client

    @QtCore.Slot(str, object, str)
    def do_work(self, intent: str, attachments: list, transcript: str):
        """Runs one Intent and emits the result: a missing transcript log must not
cost the user their run, nor must a Session that cannot be named after its
Document, so both fail quietly, and a model that cannot be resolved is a
known outcome that belongs in the transcript as a sentence, not a
traceback.
"""
        from sdk import events as ev, EventStream, build_registry, get_domain
        from bridge.logger import generate_crash_report, logger

        self._stop.clear()
        self._abort.clear()
        events = EventStream([self.bridge.subscriber()])
        try:
            events.log_to(transcript)
        except Exception as exc:
            logger.error("could not open transcript %s: %s" % (transcript, exc))

        events.subscribe(lambda event: logger.error("%s: %s" % (event.kind, event.data))
                         if event.kind in (ev.BLOCKED, ev.ERROR) else None)

        try:
            sdk_client = self._get_sdk_client()
        except ValueError as exc:
            events.emit(ev.ERROR, message=str(exc))
            self.finished.emit(None, transcript)
            return

        tools = build_registry(document=self._document, pipeline=sdk_client.pipeline,
                              dispatch=sdk_client.dispatch)
        try:
            self.document_named.emit(transcript,
                                     str(tools.call("snapshot").get("document") or ""),
                                     str(getattr(self._document, "Uid", "") or ""))
        except Exception:
            pass  # except-pass-allowed: the panel may already be closing, nothing needs the signal

        sdk_client.pipeline.start_run()
        try:
            domain = sdk_client.domain_for(intent, get_domain(), events)
            result = sdk_client.send(
                intent,
                attachments=attachments,
                cancelled=self._stop.is_set,
                events=events, domain=domain)
            self.finished.emit(result, transcript)
        except Exception as exc:
            events.emit("error", message=generate_crash_report(exc, "LoopWorker.do_work()"))
            self.finished.emit(None, transcript)
        finally:
            sdk_client.pipeline.end_run()
class _TitleThread(QtCore.QThread):
    """One short call naming a Session, off the UI thread so the run is not held up."""

    titled = QtCore.Signal(str)

    def __init__(self, intent: str, model_id: str, parent=None):
        super().__init__(parent)
        self._intent = intent
        self._model_id = model_id

    def run(self):
        from sdk import generate

        self.titled.emit(generate(self._intent, self._model_id))


RESTORE_CHUNK = 25

ATTACHMENT_WIDTH = 320

FOLDS_OVER = 400

TITLE_KEPT = 70

PICKER_SHARE = 0.35


class _SessionWorker(QtCore.QObject):
    """
    One Session's own `LoopWorker`, on its own `QThread`: standing up one of
these per Session, rather than a single global worker, is what makes two
open Documents genuinely parallel, since each Session's Tool calls and
Apply are bound to its own `worker.document`, set once from whichever
Document is in focus when the Session's first Intent is sent, rather than
to `FreeCAD.ActiveDocument` at the moment of the call, which is whichever
Document the user happens to be looking at right then; a Preflight Worker
comes along for free, living on `worker.pipeline`, this Session's own.
`running` and `sent_attachments` are carried here so a
Session backgrounded mid-run keeps its own state rather than sharing the
one the panel happens to be looking at; `dirty` is set when an event lands for
this Session while it is not the one on screen, so the panel resyncs from
the transcript rather than trusting stale widgets; `pending_question` is
the one live Ask this Session is blocked on, if any, so switching back to a
Session parked on its Owned Object question puts the same card up again. One file, one
Intent: a second Intent while `running` is already `True` is refused at the
composer, never queued here.
"""

    request_work = QtCore.Signal(str, object, str)

    def __init__(self, bridge: EventBridge, ask: AskTheUser):
        super().__init__()
        self.bridge = bridge
        self.ask = ask
        self.thread = QtCore.QThread()
        self.worker = LoopWorker(bridge, ask)
        self.worker.moveToThread(self.thread)
        self.request_work.connect(self.worker.do_work)
        self.running = False
        self.sent_attachments: list = []
        self.dirty = False
        self.pending_question = None
        self.thread.start()

    def shut_down(self, grace_ms: int):
        """
        Stop the run, whatever it is in the middle of, and end the thread.

        A Step in flight is parked inside `bridge.main_thread.on_main_thread`,
        waiting on a job only the main thread's event loop can take -- and
        this runs on that thread, not pumping it, so `bridge.main_thread.stopping`
        hands the run back its own thread for as long as this teardown lasts,
        since a run unwinding asks the main thread again after the release.
        FreeCAD deletes its Documents before it emits `aboutToQuit`, so this is
        the first teardown a closing FreeCAD reaches, through
        `slotDeletedDocument`, not the panel's `_cleanup_thread`.
        """
        from bridge.main_thread import stopping

        with stopping():
            self.worker.stop()
            if self.worker.pipeline is not None:
                self.worker.pipeline.shutdown()
            if self.thread.isRunning():
                self.thread.quit()
                if not self.thread.wait(grace_ms):
                    self.thread.terminate()
                    self.thread.wait(1000)


class DaedalusPanel(QtWidgets.QDockWidget):
    """The dock is the panel's widgets, its worker thread, and the event stream
rendered into a transcript, carrying an object name since
`QMainWindow::saveState()` drops unnamed docks, losing the panel's restored
position. The status line sits outside the transcript's scroll area, so an
unbreakable Step widens the dock and Qt never gives it back. One
`_SessionWorker` per Session lives in `self._workers`, keyed by the
Session's id.
"""

    _ON_EVENT_DISPATCH = None
    _intent_on_screen = None

    @classmethod
    def _dispatch_table(cls, attr, build):
        """
        Build-once-per-class cache of the dispatch table `on_event` reads.
        """
        table = getattr(cls, attr)
        if table is None:
            table = build()
            setattr(cls, attr, table)
        return table

    def __init__(self):
        super().__init__("Daedalus")
        self.setObjectName("DaedalusPanel")
        self.setStyleSheet(_build_qss())

        root = QtWidgets.QWidget()
        root.setObjectName("MainContainer")
        self.setWidget(root)

        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = self._build_header()
        root_layout.addWidget(header)

        self.sessions = Sessions()
        self.chat_sessions = ChatSessionManager(self.sessions)
        self.chat_sessions.setObjectName("ChatArea")
        self.chat_sessions.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        root_layout.addWidget(self.chat_sessions, stretch=1)

        from .history_popup import HistoryPopup
        self.history_popup = HistoryPopup(self)
        self.history_popup.session_selected.connect(self.switch_session)
        self.history_popup.session_deleted.connect(self.delete_session)

        self._queue_widget = QueueWidget()
        root_layout.addWidget(self._queue_widget)

        input_card = self._build_input_card()
        root_layout.addWidget(input_card)

        self._workers: dict[str, _SessionWorker] = {}

        self._running = False
        self._empty_state = None
        self._title_threads = []
        self._reset_render_state()

        FreeCAD.addDocumentObserver(self)

        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._cleanup_thread)

        self.chat_sessions.session_changed.connect(self.update_header_title)

        self.chat_sessions.load()
        self.new_chat()

    @property
    def worker(self) -> LoopWorker:
        return self._worker_for(self.chat_sessions.current_id()).worker

    @property
    def thread(self) -> QtCore.QThread:
        return self._worker_for(self.chat_sessions.current_id()).thread

    @property
    def bridge(self) -> EventBridge:
        return self._worker_for(self.chat_sessions.current_id()).bridge

    @property
    def ask(self) -> AskTheUser:
        return self._worker_for(self.chat_sessions.current_id()).ask

    def _worker_for(self, session_id: str) -> _SessionWorker:
        """This Session's own worker, built lazily -- most Sessions never run
        again, avoiding a QThread that still needs stopping on exit.
        `bridge`/`ask` stay on the GUI thread; only `worker` moves onto its
        own QThread (`_SessionWorker.__init__`), so its signals need bound
        methods, not lambdas -- see `LoopWorker`.
        """
        bundle = self._workers.get(session_id)
        if bundle is None:
            bundle = _SessionWorker(EventBridge(), AskTheUser())
            bundle.worker.finished.connect(self.on_run_finished)
            bundle.worker.document_named.connect(self.on_document_named)
            bundle.bridge.event.connect(
                lambda event, session_id=session_id: self._route_event(session_id, event))
            bundle.ask.owned_objects.connect(
                lambda payload, session_id=session_id:
                    self._route_ask(session_id, "owned_objects", payload))
            self._workers[session_id] = bundle
        return bundle

    def _bind_document(self, bundle: _SessionWorker):
        """Bind this Session's worker to whichever Document is in focus, once.
        Read here, not left to `build_registry`'s fallback: only at the
        Session's first Intent are "whichever is in focus" and "the Document
        this Session is about" guaranteed the same. Goes through
        `core.freecad_apply.active_document`, never `FreeCAD.ActiveDocument`.
        """
        if bundle.worker.document is None:
            from sdk import active_document
            bundle.worker.document = active_document()

    def _route_event(self, session_id: str, event):
        """An event from *session_id*'s own worker: render it if that Session is on
        screen, otherwise just remember it happened. The event reached its
        transcript on disk already (`LoopWorker.do_work` logs it directly),
        so `switch_session` resyncs from it next time this Session is shown.
        """
        if session_id == self.chat_sessions.current_id():
            self.on_event(event)
            return
        bundle = self._workers.get(session_id)
        if bundle:
            bundle.dirty = True

    def _route_ask(self, session_id: str, kind: str, payload):
        """A question from *session_id*'s own worker: put the card up if that Session
        is on screen, otherwise hold it until the user switches back to it.
        Recorded either way: the panel resets render state on every switch,
        so a card left open is gone the instant the user looks away.
        """
        bundle = self._workers.get(session_id)
        if bundle:
            bundle.pending_question = (kind, payload)
        if session_id == self.chat_sessions.current_id():
            self._dispatch_ask(kind, payload)

    def _dispatch_ask(self, kind: str, payload):
        {"owned_objects": self.on_owned_objects}[kind](payload)

    def _build_header(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(44)

        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_SM, SPACE_SM)
        layout.setSpacing(SPACE_XS)

        self.title_label = QtWidgets.QLabel("New Chat")
        self.title_label.setObjectName("HeaderTitle")
        self.title_label.setToolTip("Double-click to rename this session")
        self.title_label.setCursor(QtCore.Qt.IBeamCursor)
        self.title_label.mouseDoubleClickEvent = lambda event: self.rename_current_session()
        layout.addWidget(self.title_label)

        layout.addStretch()

        self.usage_label = QtWidgets.QLabel("$0.00  0:00")
        self.usage_label.setObjectName("UsageTotal")
        layout.addWidget(self.usage_label)

        self.new_btn = QtWidgets.QPushButton()
        self.new_btn.setObjectName("IconButton")
        self.new_btn.setFixedSize(CONTROL, CONTROL)
        self.new_btn.setToolTip("New chat session")
        self.new_btn.setIcon(UIHelpers.get_icon("plus", size=ICON, on=self.new_btn))
        self.new_btn.clicked.connect(self.new_chat)
        layout.addWidget(self.new_btn)

        self.history_btn = QtWidgets.QPushButton()
        self.history_btn.setObjectName("IconButton")
        self.history_btn.setFixedSize(CONTROL, CONTROL)
        self.history_btn.setToolTip("Chat history")
        self.history_btn.setIcon(UIHelpers.get_icon("history", size=ICON, on=self.history_btn))
        self.history_btn.clicked.connect(self.toggle_history)
        layout.addWidget(self.history_btn)

        return bar

    def _build_input_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("InputContainer")
        card.setProperty("focused", "false")
        self._input_card = card

        inner = QtWidgets.QVBoxLayout(card)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self.chip_bar = FileChipBar()
        inner.addWidget(self.chip_bar)

        self.user_input = _PasteAwareTextEdit()
        self.user_input.setObjectName("ChatInput")
        self.user_input.setPlaceholderText("Describe what you want to build…")
        self.user_input.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.user_input.paste_image_requested.connect(
            lambda: self.upload_widget.handle_clipboard_paste()
        )
        self.user_input.send_requested.connect(self.on_send)
        self.user_input.focus_changed.connect(self._ring_the_composer)
        self.user_input.textChanged.connect(self._fit_composer_to_text)
        inner.addWidget(self.user_input)
        self._fit_composer_to_text()

        strip = QtWidgets.QHBoxLayout()
        strip.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        strip.setSpacing(SPACE_SM)

        self.upload_widget = UploadWidget()
        self.upload_widget.file_attached.connect(self.chip_bar.add_file)
        self.chip_bar.file_removed.connect(self.upload_widget.discard)
        strip.addWidget(self.upload_widget)

        from .model_selector import ChoicePopup, ModelSelectorPopup

        picker = QtWidgets.QHBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(0)

        self.model_button = QtWidgets.QPushButton()
        self.model_button.setObjectName("PickerButton")
        self.model_button.setFixedHeight(CONTROL_DENSE)
        self.model_button.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                        QtWidgets.QSizePolicy.Fixed)
        self.model_button.setToolTip("Active LLM model")
        self.model_button.setLayoutDirection(QtCore.Qt.RightToLeft)
        self._model_display = ""
        self.model_button.clicked.connect(self._open_model_selector)
        picker.addWidget(self.model_button)

        self.model_selector = ModelSelectorPopup(self)
        self.model_selector.model_chosen.connect(self._on_model_selected)

        self.effort_button = QtWidgets.QPushButton()
        self.effort_button.setObjectName("PickerButton")
        self.effort_button.setFixedHeight(CONTROL_DENSE)
        self.effort_button.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                         QtWidgets.QSizePolicy.Fixed)
        self.effort_button.setToolTip("How hard the model is asked to think")
        self.effort_button.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.effort_button.clicked.connect(self._open_effort_selector)
        picker.addWidget(self.effort_button)

        self.domain_button = QtWidgets.QPushButton()
        self.domain_button.setObjectName("PickerButton")
        self.domain_button.setFixedHeight(CONTROL_DENSE)
        self.domain_button.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                         QtWidgets.QSizePolicy.Fixed)
        self.domain_button.setToolTip("What the run builds: Auto infers it from the Intent and the Document")
        self.domain_button.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.domain_button.clicked.connect(self._open_domain_selector)
        picker.addWidget(self.domain_button)

        strip.addLayout(picker)

        self.domain_selector = ChoicePopup(self)
        self.domain_selector.chosen.connect(
            lambda label: self._on_domain_selected(label.lower()))
        self._show_domain()

        self.effort_selector = ChoicePopup(self)
        self.effort_selector.chosen.connect(self._on_effort_selected)
        self._show_effort()

        self._models = []
        self._current_model_id = None
        self._refresh_models(provider_manager.instance().get_available_models())
        provider_manager.instance().models_changed.connect(self._refresh_models)
        strip.addStretch()
        strip.addSpacing(SPACE_MD)

        self.send_button = QtWidgets.QPushButton()
        self.send_button.setObjectName("SendButton")
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.clicked.connect(self.on_send)
        strip.addWidget(self.send_button)

        self.stop_button = QtWidgets.QPushButton()
        self.stop_button.setObjectName("SendButton")
        self.stop_button.setIcon(UIHelpers.get_icon("square", size=ICON, on=self.stop_button))
        self.stop_button.setToolTip("Stop after the current attempt")
        self.stop_button.clicked.connect(self.on_stop)
        self.stop_button.setVisible(False)
        strip.addWidget(self.stop_button)

        self.abort_button = QtWidgets.QPushButton()
        self.abort_button.setObjectName("SendButton")
        self.abort_button.setIcon(UIHelpers.get_icon("circle_x", size=ICON, on=self.abort_button))
        self.abort_button.setToolTip("Abort now, discarding the Step in progress")
        self.abort_button.clicked.connect(self.on_abort)
        self.abort_button.setVisible(False)
        strip.addWidget(self.abort_button)

        inner.addLayout(strip)
        return card

    def _ring_the_composer(self, focused: bool):
        """The composer says which of the panel's fields the keyboard is in.
        A dynamic property and a repolish, not a stylesheet of the card's
        own: a second `setStyleSheet` would drop the panel-wide rule giving
        it fill, radius and margin; Qt only re-reads a property selector
        when asked to repolish.
        """
        self._input_card.setProperty("focused", "true" if focused else "false")
        self._input_card.style().unpolish(self._input_card)
        self._input_card.style().polish(self._input_card)

    def _fit_composer_to_text(self):
        """The composer is one row empty and grows with typing, up to ~40% of the
panel, after which it scrolls: a percentage, not a row count, since a
height comfortable at one dock width wraps to nonsense at another, unlike a
fixed three rows where anything past the third was unreachable.
"""
        document = self.user_input.document()
        document.setTextWidth(self.user_input.viewport().width())
        padding = 2 * SPACE_COMPOSER
        one_row = QtGui.QFontMetrics(self.user_input.font()).lineSpacing() + padding
        ceiling = max(one_row, int(self.height() * 0.4))
        wanted = int(document.size().height()) + padding

        self.user_input.setFixedHeight(min(max(wanted, one_row), ceiling))
        self.user_input.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded if wanted > ceiling
            else QtCore.Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        """
        Refits the composer and the model pill, both of which are sized as a
        fraction of the panel and so move with it.

        Each is looked for rather than assumed: Qt resizes the dock while
        __init__ is still building it.
        """
        super().resizeEvent(event)
        if getattr(self, "user_input", None):
            self._fit_composer_to_text()
        if getattr(self, "_model_display", None):
            self._fit_the_model_pill()

    def showEvent(self, event):
        """
        Icons are tinted once the panel is on screen: a widget carries the global
stylesheet's colour only after Qt has polished it, and asked in `__init__`
it still answers with the OS palette, which is black under every FreeCAD
dark theme.
"""
        super().showEvent(event)
        for button, icon, size in ((self.new_btn, "plus", ICON),
                                   (self.history_btn, "history", ICON),
                                   (self.send_button, "send", ICON),
                                   (self.stop_button, "square", ICON),
                                   (self.abort_button, "circle_x", ICON),
                                   (self.model_button, "chevron_down", ICON_DENSE)):
            button.setIcon(UIHelpers.get_icon(icon, size=size, on=button))

    def _refresh_models(self, models=None):
        """Recompute which model the button shows: the stored id once its
        provider confirms it, held bare mid-fetch, else `_retire_model`'s
        fallback once every provider answered and none offers it, else the
        first model on offer. Nothing stored is only shown, never written --
        a display refresh must not overwrite a choice the run's own fallback
        would otherwise read honestly.
        """
        if models is None:
            models = provider_manager.instance().get_available_models()
        self._models = models
        last_id = self._orchestrator_row().get("model", "")

        if not models:
            self._set_current_model(None, "No models — add API key")
            return

        entry = next((m for m in models if m["id"] == last_id), None) if last_id else None
        if entry is None and last_id:
            if provider_manager.instance().awaiting_model_lists():
                entry = {"id": last_id, "display": last_id, "provider": ""}
            else:
                fallback_id = self._retire_model(last_id)
                entry = next((m for m in models if m["id"] == fallback_id), None)
        if entry is None:
            entry = models[0]

        self._set_current_model(entry["id"], entry["display"])

    def _set_current_model(self, model_id, display: str):
        """
        The model pill, and the effort pill beside it: whether the choice of
        how hard to think means anything is a property of the model, so it is
        re-read every time the model changes.
        """
        self._current_model_id = model_id
        self._model_display = display
        self.model_button.setToolTip("Active LLM model — %s" % display)
        self._fit_the_model_pill()
        self._show_effort()

    def _fit_the_model_pill(self):
        """
        The model's name is elided to `PICKER_SHARE` of the panel's width, the name
in full moving to the tooltip: the pill sizes itself to its own text, and a
provider's name for a model is not a name, `claude-sonnet-4-5-20250929` and
`gemini-2.5-flash-preview-05-20` being the ids the OpenAI-shaped routes
hand back where a display name is missing, at which length the pill and the
effort pill beside it are the whole strip and Send is pushed to the edge of
a docked panel; the attach button that sets the idiom for that row is 28
pixels wide. It is a share of the panel rather than a fixed ceiling,
because the panel is dockable and floats at any width, so a ceiling
comfortable at one width is either wasteful or useless at the other. The
share is of the whole pill, not of its text, since the chevron beside the
name and the stylesheet's padding are what the strip actually loses, so the
button is asked what its own chrome costs rather than told, and the number
left over is what the name is elided to.
"""
        metrics = QtGui.QFontMetrics(self.model_button.font())
        self.model_button.setText(self._model_display)
        chrome = (self.model_button.sizeHint().width()
                  - metrics.horizontalAdvance(self._model_display))
        room = max(CONTROL, int(self.width() * PICKER_SHARE) - chrome)
        self.model_button.setText(
            metrics.elidedText(self._model_display, QtCore.Qt.ElideRight, room))

    def _retire_model(self, model_id: str) -> str:
        """
        Every provider has answered and none offers ``model_id``. Say so.

        The fallback is the Preferences default, or the first model on offer,
        because that default is only a preference and may never have been set.
        """
        from sdk import get_default_model

        fallback = get_default_model() or self._models[0]["id"]
        self._store_model(fallback)
        self._queue_widget.now(
            "%s is no longer offered by its provider — switched to %s."
            % (model_id, fallback))
        return fallback

    def _orchestrator_row(self) -> dict:
        """
        The orchestrator's row of the Preferences role table -- the one row
        the composer's two pills edit; every other role is set in Preferences
        alone.
        """
        from sdk import ORCHESTRATOR, get_role_model

        return get_role_model(ORCHESTRATOR) or {}

    def _store_on_the_orchestrator(self, **fields):
        """
        Write *fields* onto that row.

        Through `set_role_table`, whose write the Preferences page hears while
        it is open.
        """
        from sdk import ORCHESTRATOR, get_role_table, set_role_table

        table = get_role_table()
        for row in table:
            if row.get("role") == ORCHESTRATOR:
                row.update(fields)
        set_role_table(table)

    def _open_model_selector(self):
        self.model_selector.open_at(self._models, self.model_button)

    def _store_model(self, model_id: str):
        """Where a chosen model is kept: the orchestrator's row of the role
        table, and the shared last-used model any role-less caller falls
        back to (`settings.get_llm_credentials`).
        The row carries the provider too: a model resolves only through the
        provider that offers it, and is read back with no model list to hand.
        """
        entry = next((m for m in self._models if m["id"] == model_id), None)
        self._store_on_the_orchestrator(
            model=model_id, provider=(entry or {}).get("provider", ""))
        from sdk import set_last_model

        set_last_model(model_id)

    def _on_model_selected(self, model_id: str):
        """Drops the client so the next send rebuilds it from settings."""
        self._store_model(model_id)
        if getattr(self, "worker", None):
            self.worker.client = None
        entry = next((m for m in self._models if m["id"] == model_id), None)
        self._set_current_model(model_id, entry["display"] if entry else model_id)

    def _open_effort_selector(self):
        """
        Offers the levels the routes take, unless this model cannot reason at
        all: a picker that changes nothing is a picker that lies, so the
        trigger says so in its tooltip and does not open.
        """
        from sdk import EFFORTS, effort_is_honoured

        if not effort_is_honoured(self._orchestrator_row().get("model", "")):
            return
        self.effort_selector.open_at(list(EFFORTS), self.effort_button)

    def _on_effort_selected(self, level: str):
        """Drops the client so the next send is asked to think the new way."""
        self._store_on_the_orchestrator(effort=level)
        if getattr(self, "worker", None):
            self.worker.client = None
        self._show_effort()

    def _show_effort(self):
        """The pill carries the level the orchestrator's row is set to, and
        says when the model will ignore it.
        An empty or outdated level falls back to the single stored dial,
        same as `client_from_settings`. A model with no reasoning to spend
        cannot honour it, so the pill says so (`core.llm.effort_is_honoured`).
        """
        from sdk import EFFORTS, effort_is_honoured, get_effort

        row = self._orchestrator_row()
        honoured = effort_is_honoured(row.get("model", ""))
        level = row.get("effort", "")
        self.effort_button.setText(level if level in EFFORTS else get_effort())
        self.effort_button.setEnabled(honoured)
        self.effort_button.setToolTip(
            "How hard the model is asked to think" if honoured
            else "This model does no reasoning of its own")
        self.effort_button.setIcon(
            UIHelpers.get_icon("chevron_down", size=ICON_DENSE, on=self.effort_button))

    def _open_domain_selector(self):
        from sdk import CHOICES, domain_label

        self.domain_selector.open_at([domain_label(choice) for choice in CHOICES],
                                     self.domain_button)

    def _on_domain_selected(self, choice: str):
        from sdk import set_domain

        set_domain(choice)
        self._show_domain()

    def _show_domain(self):
        from sdk import domain_label, get_domain

        self.domain_button.setText(domain_label(get_domain()))
        self.domain_button.setIcon(
            UIHelpers.get_icon("chevron_down", size=ICON_DENSE, on=self.domain_button))

    def toggle_history(self):
        """The picker holds only indexed Sessions, each carrying its own
        position: a Session enters on its first Intent (when it gets its
        name), and the live empty Session is on screen, not in the history.

        It shows the Document in front of the user, not the archive: a chat
        belongs to the file it was about. Everything is still reachable -- a
        Document nothing has been said about, or one fully pruned, falls
        back to the whole list rather than an empty popup.
        """
        if self.history_popup.isVisible():
            self.history_popup.close()
        else:
            self.history_popup.populate(self._sessions_worth_showing())
            self.history_popup.drop_from_the_dock()

    def _sessions_worth_showing(self) -> list:
        """The indexed Sessions of the Document in focus, or all of them."""
        from sdk import active_document

        try:
            identity = str(getattr(active_document(), "Uid", "") or "")
        except Exception:
            identity = ""
        rows = self.sessions.indexed()
        if not identity:
            return rows
        here = [info for info in rows if info.get("document_id") == identity]
        return here or rows

    def new_chat(self):
        """
        Opens an empty Session. Its real name comes from its first Intent, so
        there is nothing to call it yet.
        """
        session_id, _ = self.chat_sessions.add_new_chat("New Chat")
        self._became_the_live_session()
        self.update_header_title(session_id)
        self._empty_state = self._build_empty_state()
        self.add_chat_widget(self._empty_state)

    def _reset_render_state(self):
        """
        What one run builds and the next must not draw into.

        There is room for one question at a time: the run is blocked on the
        answer, so it cannot have asked a second thing while the first is on
        screen.
        """
        self._live_rows = {}
        self._question_card = None
        self._answers_to_enter = None
        self._total_usd = 0.0
        self._domain_usd = 0.0
        self._run_start_time = None
        self._last_event_time = None
        self._last_tool_call_time = None
        self._last_tool_call_widget = None
        self._last_tool_call_name = None
        self._update_usage_total()

    def _update_usage_total(self):
        """
        Redraw the header's total: what the run cost and what it took.

        Elapsed is the `event.at` delta from the Intent to the last event.
        """
        elapsed_seconds = 0.0
        if self._run_start_time is not None and self._last_event_time is not None:
            elapsed_seconds = self._last_event_time - self._run_start_time

        time_str = _format_elapsed(elapsed_seconds)
        self.usage_label.setText(f"{_format_cost(self._total_usd)}  {time_str}")

    def _became_the_live_session(self):
        """Let go of the render state of the Session being left.
        Owned Objects need no clearing: each Session keeps its own
        `_SessionWorker`, so its Owned Objects reach nowhere else. The
        Queue, pinned above the composer, stands down here too; the replay
        that follows restores what the opened Session had running.
        """
        self._reset_render_state()
        self._queue_widget.stand_down()
        self._empty_state = None

    def rename_current_session(self):
        session_id = self.chat_sessions.current_id()
        info = self.sessions.at(session_id)
        if info is None:
            return
        name, chosen = QtWidgets.QInputDialog.getText(
            self, "Rename session", "Name:", QtWidgets.QLineEdit.Normal, info["name"])
        if chosen and name.strip():
            self.sessions.rename(session_id, name)
            self.update_header_title(session_id)

    def switch_session(self, session_id: str):
        """Open a Session, resyncing what is shown to what actually happened.

        Each Session runs on its own `_SessionWorker`, so switching away from
        one still going leaves it running rather than blocking or losing it.
        What was missed while away is on that Session's own transcript, and
        `restore_session` reads it back in -- the same chunked replay a
        reopened historical Session uses, asked for again since a Session
        that kept running unseen has more transcript than last shown.
        """
        self.chat_sessions.switch_to(session_id)
        self._became_the_live_session()
        bundle = self._workers.get(session_id)
        self.restore_session(session_id, resync=bool(bundle and bundle.dirty))
        if bundle is None:
            self._set_running(False)
            return
        bundle.dirty = False
        self._set_running(bundle.running)
        if bundle.pending_question is not None:
            kind, payload = bundle.pending_question
            self._dispatch_ask(kind, payload)

    def restore_session(self, session_id: str, resync: bool = False):
        """
        Replay the Session's log through the switch that rendered it live.

        `resync` bypasses "already restored" and re-reads the whole transcript
        regardless — for a Session whose worker kept running while it was not
        on screen, so what were stale widgets are rebuilt from what the
        transcript now actually holds. `clear_page` first, or the
        replay would land its widgets after whatever this Session showed the
        last time it was open, doubling everything that had already rendered.
        """
        from sdk import replay

        info = self.sessions.at(session_id)
        if info is None:
            return
        due = self.sessions.mark_restored(session_id)
        if not resync and not due:
            return
        if resync:
            self.chat_sessions.clear_page(session_id)

        events, truncated = replay(info["log"])
        if not events:
            self._empty_state = self._build_empty_state()
            self.add_chat_widget(self._empty_state)
            return

        self._replay_chunk(session_id, events, 0, truncated)

    def _replay_chunk(self, session_id: str, events: list, start: int, truncated: bool):
        """Replay a batch, then hand the event loop a turn before the next one:
replaying hundreds of Steps in one pass on the GUI thread froze the panel
for as long as it took, and the first batch is still synchronous, so a
short Session is on screen by the time this returns. A Session already
switched away from is abandoned rather than rendered.
"""
        if self.chat_sessions.current_id() != session_id:
            return

        for event in events[start:start + RESTORE_CHUNK]:
            self.on_event(event)

        done = start + RESTORE_CHUNK
        if done < len(events):
            QtCore.QTimer.singleShot(
                0, lambda: self._replay_chunk(session_id, events, done, truncated))
            return

        if truncated:
            self.add_chat_widget(AssistantMessageWidget(
                "This transcript ends mid-event — FreeCAD closed while the run was writing. "
                "Everything above it survived."))
        self._queue_widget.now("")

    @QtCore.Slot(str, str)
    def on_document_named(self, transcript: str, document: str, document_id: str):
        """
        Record which Document a Session worked on, so history can say so and
        so the panel can recognise that Document again.

        The name is what the picker shows; the id is the Document's own `Uid`,
        which is what it is recognised by — every unsaved Document in FreeCAD
        is called "Unnamed" (`slotActivateDocument`).
        """
        self.sessions.name_document(log_id(transcript), document, document_id)

    def delete_session(self, session_id: str):
        info = self.sessions.at(session_id)
        if info is None:
            return
        confirmed = QtWidgets.QMessageBox.question(
            self, "Delete session",
            "Delete “%s” and its whole transcript?" % info["name"],
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if confirmed != QtWidgets.QMessageBox.Yes:
            return
        self.sessions.remove(session_id)
        self.chat_sessions.drop_page(session_id)
        if not self.sessions.count():
            self.chat_sessions.add_new_chat("New Chat")
        bundle = self._workers.pop(session_id, None)
        if bundle is not None:
            bundle.shut_down(self.SHUTDOWN_GRACE_MS)
        self.update_header_title(self.chat_sessions.current_id())

    def _build_empty_state(self):
        """One muted line, so an empty panel still says where the user stands."""
        model = self.model_button.text().strip() or "no model"
        label = QtWidgets.QLabel(
            "%s. Describe what to build." % model)
        label.setObjectName("EmptyState")
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignCenter)
        return label

    @QtCore.Slot(str)
    def update_header_title(self, session_id: str):
        info = self.sessions.at(session_id)
        if info:
            self.title_label.setText(info["name"])

    def _unusable_attachments(self) -> list:
        """
        Why the attached files would not reach the model, before the run starts.

        The chips stay on the bar, so the offending one can be taken off — or
        the model changed — and the same Intent sent again. A settings lookup
        that fails is not the user's problem here; the run reports it.
        """
        from sdk import selected_model_name, unusable_attachments

        attached = self.chip_bar.files()
        if not attached:
            return []
        try:
            model = selected_model_name(self._current_model_id)
        except Exception:
            return []
        return unusable_attachments(attached, model)

    def on_send(self):
        """
        Sends the composer's text as an Intent, or answers a waiting question.

        One file, one Intent: a Document already running -- this Session's or
        another bound to the same Document -- refuses the Intent at the
        composer, before the Session is named or the composer cleared.
        """
        if self._answers_to_enter is not None:
            self._answer_with(self._answers_to_enter)
            return

        intent = self.user_input.toPlainText().strip()
        if not intent:
            return

        session = self.sessions.at(self.chat_sessions.current_id())
        if session is None:
            return

        unusable = self._unusable_attachments()
        if unusable:
            self.add_chat_widget(AssistantMessageWidget(
                "This would not have reached the model:\n\n"
                + "\n".join("• " + reason for reason in unusable)))
            return

        bundle = self._worker_for(session["id"])
        self._bind_document(bundle)

        if self._document_busy(bundle):
            self.add_chat_widget(AssistantMessageWidget(
                "This Document already has a run in flight. Wait for it to "
                "finish, or work from a different open Document."))
            return

        self._name_session(session["id"], intent)

        attachments = self.chip_bar.get_and_clear()
        bundle.sent_attachments = attachments

        if self._empty_state is not None:
            self._empty_state.setParent(None)
            self._empty_state = None
        self.user_input.clear()

        self._reset_render_state()
        bundle.running = True
        self._set_running(True)
        self.add_chat_widget(TextMessageWidget(intent))
        self._intent_on_screen = intent
        self._queue_widget.now("Routing\u2026")
        bundle.request_work.emit(intent, attachments, session["log"])

    def _document_busy(self, bundle: _SessionWorker) -> bool:
        """
        Whether *bundle*'s own Document already has a run in flight -- this
        Session's, or another Session bound to the same live Document. An
        unbound Document (`None`) never collides with another unbound one.
        """
        if bundle.running:
            return True
        doc = bundle.worker.document
        if doc is None:
            return False
        return any(other is not bundle and other.running and other.worker.document is doc
                   for other in self._workers.values())

    def _name_session(self, session_id: str, intent: str):
        """The Session takes its name from this Intent, and enters the picker.
        The truncated Intent shows immediately so nothing is ever "Untitled";
        the generated title replaces it when it arrives, found again by the
        same id.
        """
        from sdk import fallback

        started, dropped = self.sessions.begin(session_id, fallback(intent))
        for gone in dropped:
            self.chat_sessions.drop_page(gone)
        if started:
            self.update_header_title(session_id)
        if self.sessions.wants_a_title(session_id):
            self._ask_for_a_title(session_id, intent)

    def _ask_for_a_title(self, session_id, intent: str):
        """
        Alongside the run, never in front of it.

        Qt owns the thread, so it is kept in a list: without a reference it is
        collected mid-flight.
        """
        from sdk import get_naming_model

        thread = _TitleThread(intent, get_naming_model(), self)
        thread.titled.connect(lambda title: self._title_arrived(session_id, title))
        self._title_threads.append(thread)
        thread.finished.connect(lambda: self._title_threads.remove(thread))
        thread.start()

    def _title_arrived(self, session_id, title: str):
        if self.sessions.title_arrived(session_id, title):
            self.update_header_title(self.chat_sessions.current_id())

    def on_stop(self):
        """
        Ends the run, and disables any question card with it: a card left
        clickable after Stop answers a run that is already over.

        Reaches the on-screen Session's worker through `self._workers` rather
        than the `worker` property (`_worker_for`), which would build and
        register a fresh idle bundle for a Session with nothing running.
        """
        if self._question_card is not None:
            self._question_card.setEnabled(False)
            self._question_card = None
            self._answers_to_enter = None
        bundle = self._workers.get(self.chat_sessions.current_id())
        if bundle is not None:
            bundle.pending_question = None
            bundle.worker.stop()
        self.stop_button.setEnabled(False)
        self.abort_button.setEnabled(False)
        self._queue_widget.now("Stopping after the current attempt…")

    def on_abort(self):
        """
        Kills the kernel now, discarding whatever Step is in flight — where
        Stop lets it finish.

        Same `self._workers` lookup as `on_stop`, for the same reason: the
        `worker` property would build a fresh idle bundle for a Session with
        no run in flight rather than reach the one that is.
        """
        if self._question_card is not None:
            self._question_card.setEnabled(False)
            self._question_card = None
            self._answers_to_enter = None
        bundle = self._workers.get(self.chat_sessions.current_id())
        if bundle is not None:
            bundle.pending_question = None
            bundle.worker.abort()
        self.stop_button.setEnabled(False)
        self.abort_button.setEnabled(False)
        self._queue_widget.now("Aborting…")

    def _set_running(self, running: bool):
        """
        Stop and Abort come up for the run; Send and the composer stay.

        A run holds the Document, not the conversation: the Gate's answer is
        typed here, and so is the next Intent, which queues behind the run in
        flight rather than being refused (`on_send`).
        """
        self._running = running
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self.abort_button.setVisible(running)
        self.abort_button.setEnabled(running)

    @QtCore.Slot(object)
    def on_event(self, event):
        """The whole panel is this method: what an event *says* is `core.narrate`'s,
what is left here is where a line goes and what shape it takes, a
code block, a picture, streaming reasoning, or the document path as a
link, with no calls back into the loop. `_ON_EVENT_DISPATCH` lists only the
kinds drawn differently from prose, True ending here and False falling
through to `narrate`; a kind the stream does not register draws nothing and
is logged once. `INTENT` takes the user's bubble unless Send already put
it up, so a prompt shows once whether live or restored.
"""
        from bridge.logger import logger
        from sdk import events as ev
        from sdk import narrate

        data = event.data

        if event.kind not in ev.KINDS:
            logger.warning("unrecognized event kind %r" % event.kind)
            return

        if event.kind == ev.INTENT:
            self._total_usd, self._domain_usd = self._domain_usd, 0.0
            self._run_start_time = event.at
            self._last_event_time = event.at
            self._update_usage_total()
            if data.get("text", "") != self._intent_on_screen:
                self.add_chat_widget(TextMessageWidget(data.get("text", "")))
            self._intent_on_screen = None
            self._queue_widget.reset()
            return

        handler = DaedalusPanel._dispatch_table("_ON_EVENT_DISPATCH", lambda: {
            ev.CALL_STARTED: DaedalusPanel._oe_call_started,
            ev.COST: DaedalusPanel._oe_cost,
            ev.THOUGHT: DaedalusPanel._oe_thought,
            ev.WRITING: DaedalusPanel._oe_writing,
            ev.ATTACHED: DaedalusPanel._oe_attached,
            ev.TOOL_CALL: DaedalusPanel._oe_tool_call,
            ev.TOOL_RESULT: DaedalusPanel._oe_tool_result,
        }).get(event.kind)
        if handler is not None and handler(self, event, data):
            return

        self._say(narrate(event))

    def _oe_call_started(self, event, data):
        return True

    def _oe_cost(self, event, data):
        self._total_usd += data.get("usd", 0.0)
        if data.get("phase") == "domain":
            self._domain_usd += data.get("usd", 0.0)
        self._last_event_time = event.at
        self._update_usage_total()
        self._the_thinking_stops(data.get("phase"))
        return True

    def _oe_thought(self, event, data):
        self._render_thought(data["phase"], data["text"])
        return True

    def _oe_writing(self, event, data):
        self._render_writing(data["phase"], data["text"])
        return True

    def _oe_attached(self, event, data):
        self._render_attachments(data["files"])
        return True

    def _oe_tool_call(self, event, data):
        self._render_tool_call(data["name"], data["arguments"], event.at)
        return True

    def _oe_tool_result(self, event, data):
        self._time_the_tool_call(event.at)
        return False

    def _say(self, narration):
        """Put a narrated event where it belongs: a line in the transcript, a
        collapsible row, or the Queue's line about what is happening now.
        A line past `FOLDS_OVER` folds into a row: one such paragraph ran to
        1,900 characters, four screens between the user and the Plan they
        opened the panel to read. Folded, its first sentence titles the row.
        """
        if narration.line:
            self.add_chat_widget(_folded(narration.line)
                                 if len(narration.line) > FOLDS_OVER
                                 else AssistantMessageWidget(narration.line))

        if narration.title:
            row = AccordionWidget(narration.title, code=True)
            self.add_chat_widget(row)
            row.append_text(narration.body)

        if narration.status:
            self._queue_widget.now(narration.status)

    def _stopped(self, turn):
        """The row of one turn, left as a record rather than a clock."""
        row = self._live_rows.pop(turn, None)
        if row:
            row.stop_pulsing()

    def _the_thinking_stops(self, phase=None):
        """Every live row of a phase, or the whole run where none is named.
        Every route out of a live row goes through here: unstopped, a row
        keeps a `QTimer` for the panel's life and can read past the header's
        own total (once 35:05 in a 34-minute run). `COST` ends a call and
        stops the count; it names its phase since pages now run at once.
        """
        for turn in list(self._live_rows):
            if phase is None or turn[1] == phase:
                self._stopped(turn)

    def _render_thought(self, phase, text):
        """Reasoning appends to one accordion per phase turn, as it forms.
        Only the chat scroll renders it, so a long chain of thought reads
        whole, not clipped. A wordless chunk says the model reasons with
        words withheld (`core/llm.py`); the row still pulses, alive, but
        does not open onto nothing.
        """
        self._one_row_per_turn("Thinking (%s)…" % phase, ("thought", phase), text,
                               reasoning=True)

    def _render_writing(self, phase, text):
        """The reply as it forms, in a row of its own beside the reasoning.
        Its own row since the two counts read apart: the reply is what the
        phase is held to, and accounts for the larger wait -- 22.4 of a run's
        23.6 minutes of provider time fell after the last reasoning chunk. It
        wears no brain: not the model deciding, the answer.
        """
        self._stopped(("thought", phase))
        self._one_row_per_turn("Writing (%s)…" % phase, ("writing", phase), text)

    def _one_row_per_turn(self, title, turn, text, reasoning=False):
        """One accordion per turn of a phase, opened on its first chunk: Design fans
out one call per page, streamed interleaved, so a single live row is
claimed by whichever page spoke last, once showing "Thinking (design: page
1)" for 0:01 on a page that thought 3:03. A turn ends at its own `COST`,
letting the phase open a fresh row next.
"""
        row = self._live_rows.get(turn)
        if row is None:
            row = self._live_rows[turn] = AccordionWidget(title)
            if reasoning:
                row.mark_as_reasoning()
            row.opens_onto_nothing()
            self.add_chat_widget(row)
            row.start_pulsing()
        if text:
            row.append_text(text)

    def _render_attachments(self, files):
        """What the run was shown, in the chat beside the Intent: the picture itself,
not a filename, since the chips come off the bar on Send. A restored
Session decides the shape, a clipboard paste being a temp file deleted when
the run ends so the record outlives its file, and gone means the name,
never a broken image or a raised exception.
"""
        for attached in files:
            name = attached.get("name") or ""
            picture = QtGui.QPixmap(attached.get("path") or "")
            if picture.isNull():
                self.add_chat_widget(_beside_the_intent(
                    FileChip(name or attached.get("path") or "", removable=False)))
                continue
            label = QtWidgets.QLabel()
            label.setPixmap(picture.scaledToWidth(
                min(ATTACHMENT_WIDTH, picture.width()),
                QtCore.Qt.SmoothTransformation))
            label.setToolTip(name)
            self.add_chat_widget(_beside_the_intent(label))

    def _render_tool_call(self, name, arguments, event_time):
        """A call takes one collapsible shape in the stream, not three, naming its
Step: open, four Steps of code fill the whole panel and the run reads idle
between them, while closed, the scrolling line says what is running and the
code stays one click away. Duration is added at `TOOL_RESULT`, from
`event.at` deltas.
"""
        self._the_thinking_stops()
        row = AccordionWidget(name, code=True, boxed=True)
        self.add_chat_widget(row)
        row.append_text(arguments["code"] if "code" in arguments else str(arguments))

        self._last_tool_call_time = event_time
        self._last_tool_call_widget = row
        self._last_tool_call_name = name

    def _time_the_tool_call(self, at):
        """How long the call that just answered took, on its own closed row.
        On the row, not the transcript, since a run takes nine of these and
        a line each would be nine lines saying a number. `TOOL_RESULT` still
        narrates after this since the loop's calls are sequential, so the
        row timed is always the last opened.
        """
        if not (self._last_tool_call_widget and self._last_tool_call_time):
            return
        self._last_tool_call_widget.toggle_button.setText(
            "%s  (%.1fs)" % (self._last_tool_call_name, at - self._last_tool_call_time))
        self._last_tool_call_widget = None
        self._last_tool_call_time = None
        self._last_tool_call_name = None

    @QtCore.Slot(object)
    def on_owned_objects(self, kinds):
        """The Owned Object rule: deletion only, since a modification is covered by
the Plan Gate, which approved a Step that said the object changes. The same
card as every other question, not a modal box, so it stays inside the
transcript rather than outside it, with what is said about it, before and
after, on the stream from `ApplyPipeline`.
"""
        deletes = list(kinds.get("delete") or ())

        self._put_the_question(
            "Waiting for you to allow a deletion\u2026",
            "Delete %s?" % ", ".join(deletes),
            "%s did not create %s in this session, so this is your own work. Ctrl+Z will "
            "still undo it as one Step."
            % ("The assistant", "these" if len(deletes) > 1 else "it"),
            (("Allow", lambda: True),
             ("Refuse", lambda: False)))

    def _put_the_question(self, waiting_for, prompt, note, choices, enter=None):
        """
        Ask the user the one way this panel asks anything: four questions reach
here, the Plan Gate, what to do about a result that did not pass, the
values Plan was not given, and permission to delete the user's own object,
once four bespoke cards, three the same forty lines with different words
and the fourth a modal box that answered outside the transcript entirely. A
`choice` is a label and a callable returning the answer to hand back to the
worker thread, which is blocked waiting for it, and returning `None` leaves
the card up, which is how a composer with nothing in it nags instead of
answering; `enter` is the choice the composer's Enter key takes where there
is one, only the question answered by typing having it, since Enter at the
Plan Gate would amend a Plan the user was reading, not approve it. The
first choice is the one the card is asking for, so it is drawn as the
panel's one filled button and laid out last, the buttons right aligned
reading outwards from the corner the eye ends at, and the question is asked
without a card of its own, since the design system's own Confirmation is
plain rows and two buttons, marked out only by the filled button, the one
on screen while it is up.
"""
        self._queue_widget.now(waiting_for)

        card = QtWidgets.QFrame()
        card.setObjectName("GateCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_SM)

        if prompt:
            asked = QtWidgets.QLabel(prompt)
            asked.setWordWrap(True)
            asked.setStyleSheet(css(LABEL))
            layout.addWidget(asked)

        explained = QtWidgets.QLabel(note)
        explained.setWordWrap(True)
        explained.setStyleSheet(css(CAPTION))
        layout.addWidget(explained)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(SPACE_SM)
        buttons.addStretch()
        for position, (label, decide) in reversed(list(enumerate(choices))):
            button = QtWidgets.QPushButton(label)
            button.setObjectName("PrimaryButton" if position == 0 else "PillButton")
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, d=decide: self._answer_with(d))
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self._question_card = card
        self._answers_to_enter = enter
        self.add_chat_widget(card)

    def _answer_with(self, decide):
        """Close the card on an answer; leave it up when there is not one yet. An
answered card is spent, the accent leaving (one control wears it), subdued
by an opacity effect rather than a colour, and demotion is an `objectName`
change and repolish, since a stylesheet rule outranks Qt's disabled
palette.
"""
        answer = decide()
        if answer is None:
            return

        card, self._question_card = self._question_card, None
        self._answers_to_enter = None
        if card is not None:
            for button in card.findChildren(QtWidgets.QPushButton):
                if button.objectName() in ("PrimaryButton", "ReviewApprove"):
                    button.setObjectName("PillButton")
                    button.style().unpolish(button)
                    button.style().polish(button)
            card.setEnabled(False)
            spent = QtWidgets.QGraphicsOpacityEffect(card)
            spent.setOpacity(0.45)
            card.setGraphicsEffect(spent)
        self._queue_widget.now("Working\u2026")
        bundle = self._workers.get(self.chat_sessions.current_id())
        if bundle is not None:
            bundle.pending_question = None
        self.ask.answer(answer)

    def _from_composer(self, nag, into):
        """Take the answer from the composer, echo it, and shape it.

        None when there is nothing typed: an empty answer here is a slip, not
        a decision -- the placeholder says what is wanted and the card stays
        up. Questions where "nothing" is a real answer offer it as a button.
        """
        said = self.user_input.toPlainText().strip()
        if not said:
            self.user_input.setPlaceholderText(nag)
            self.user_input.setFocus()
            return None

        self.user_input.clear()
        self.add_chat_widget(TextMessageWidget(said))
        return into(said)

    def on_run_finished(self, result, log: str):
        """Ends the run; *log* names which Session finished, its worker keeps
running regardless, so only the widgets are guarded behind "is this the
Session on screen".
"""
        session_id = log_id(log)
        bundle = self._workers.get(session_id)
        if bundle is None:
            return
        bundle.running = False
        self.upload_widget.discard(*bundle.sent_attachments)
        bundle.sent_attachments = []
        if session_id != self.chat_sessions.current_id():
            bundle.dirty = True
            return

        self._the_thinking_stops()
        self._set_running(False)
        if result is not None and result.ok:
            self._queue_widget.now("")
        else:
            self._queue_widget.now("Stopped. Your Document is unchanged beyond the "
                                   "Steps already applied.")

    def add_chat_widget(self, widget: QtWidgets.QWidget):
        """
        Adds a widget to the transcript, before the trailing stretch, which is
        what keeps a short transcript from being spread down the whole panel.
        Following the bottom is the scroll area's own business, and only when
        the user has not scrolled back to read.
        """
        chat_history = self.chat_sessions.get_current_chat_history()
        if not chat_history:
            return None
            
        container = chat_history.widget()
        if container and container.layout():
            layout = container.layout()
            layout.insertWidget(max(layout.count() - 1, 0), widget)

        return widget

    SHUTDOWN_GRACE_MS = 3000

    def _cleanup_thread(self):
        """
        Stop every Session's worker, whatever each is in the middle of: `quit()`
only asks a thread's event loop to end, and a run is not waiting for
events, it is inside a call, parked on a question or a request in flight,
so waiting on that without first releasing the question hangs FreeCAD on
exit with no window left to answer from, while the headless FreeCAD behind
each Session's own Preflight Worker is this panel's child process and goes
with it. A Step in flight is inside `bridge.main_thread.on_main_thread`,
waiting on a job only this thread's event loop can take, and since this
thread is here, not pumping it, nothing would take that job, so
`bridge.main_thread.closing()` comes first, releasing every dispatch
already waiting and refusing the ones the unwinding run makes on its way
out, since without it the wait below expires and a thread in the middle of
the Document is ended from outside; every worker is told to stop before any
thread is waited on, since a Session parked on a question blocks its own
thread until released and waiting on one before releasing the next would
serialise what should be a handful of independent, already-blocked threads.
`SHUTDOWN_GRACE_MS` is what a run in flight is given to notice it was
stopped before the thread is ended from outside, long enough for a request
already sent to unwind, and a `_TitleThread` in `self._title_threads` has
no stop flag to set, since `core.session_title.generate` is one blocking
call, not a loop, so the grace period here is the only chance it gets to
finish on its own before this panel stops tracking it.
"""
        from bridge.main_thread import closing

        closing()
        FreeCAD.removeDocumentObserver(self)
        for bundle in self._workers.values():
            bundle.worker.stop()
            if bundle.worker.pipeline is not None:
                bundle.worker.pipeline.shutdown()
        for bundle in self._workers.values():
            if bundle.thread.isRunning():
                bundle.thread.quit()
                if not bundle.thread.wait(self.SHUTDOWN_GRACE_MS):
                    bundle.thread.terminate()
                    bundle.thread.wait(1000)
        for thread in list(self._title_threads):
            if thread.isRunning():
                if not thread.wait(self.SHUTDOWN_GRACE_MS):
                    thread.terminate()
                    thread.wait(1000)

    def slotDeletedDocument(self, document):
        """A Document closing ends whichever Session was bound to it.

        FreeCAD calls every registered observer's `slotDeletedDocument` by
        this exact name -- no base class to inherit, just the method
        present. The Session itself, its transcript and its place in the
        picker are untouched; only the worker is torn down, the same as
        `delete_session` tears one down, keeping nothing running against a
        closed Document.
        """
        for session_id, bundle in list(self._workers.items()):
            if bundle.worker.document is document:
                bundle.shut_down(self.SHUTDOWN_GRACE_MS)
                del self._workers[session_id]

    def slotActivateDocument(self, document):
        """The MDI tab the user brought forward in FreeCAD moves the panel too: the
Document's `Uid` matches against every Session's recorded `"document_id"`,
last one first, so a Document worked on by more than one Session resolves
to whichever ran most recently, and a Document no Session has worked on
leaves the panel where it was. `Uid`, not `Name`, since FreeCAD calls every
unsaved Document "Unnamed", so matching on the name opened a new Document's
chat onto an older one.
"""
        identity = str(getattr(document, "Uid", "") or "")
        if not identity:
            return
        for session_id in reversed(self.sessions.ids()):
            if self.sessions.at(session_id).get("document_id") == identity:
                if session_id != self.chat_sessions.current_id():
                    self.switch_session(session_id)
                return

    def closeEvent(self, event):
        self.upload_widget.discard_temporary()
        self._cleanup_thread()
        super().closeEvent(event)
