# FreeCadCopilot/ui/copilot_panel.py
"""
The panel: a renderer of the event stream.

It does not drive the loop. It sends one Intent, then renders whatever the
loop emits — `CopilotPanel.on_event` is a switch over event kinds and nothing
else. That is what lets the same stream be the session log, and what keeps a
frozen UI impossible: the loop runs on a worker thread and only ever emits.

The panel has no colours. FreeCAD paints its UI with one global stylesheet and
leaves QPalette at whatever the OS handed over, so `palette(base)` was a
light-theme value painted over a dark theme (ADR-0011). What is left in the
stylesheet is geometry and typography, which no theme has an opinion about.
"""
import threading

import FreeCAD

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from .chat_widgets        import TextMessageWidget, AssistantMessageWidget, CodeBlockWidget
from .multi_tab_manager   import ChatSessionManager
from .upload_widget       import UploadWidget, FileChipBar
from .accordion_widget    import AccordionWidget
from .plan_widget         import PlanWidget, DONE as PLAN_DONE, FAILED as PLAN_FAILED
from utils import settings_manager
from utils import provider_manager
from ui.ui_helpers import UIHelpers


# ── Stylesheet ────────────────────────────────────────────────────────────────

def _build_qss() -> str:
    """
    The panel stylesheet: geometry and typography, and nothing else.

    Every colour that used to be here asked `palette(...)` for it, and under
    FreeCAD's dark theme the palette still holds the OS's light values — so the
    input card was a white rectangle in a dark panel (ADR-0011). Leaving colour out
    hands these widgets to FreeCAD's global stylesheet, including themes that
    did not exist when this was written.

    Where a rule pins a dimension it also resets padding and border: Qt's
    min/max-width size the content rect, and the theme's padding is added on
    top of them.
    """
    return f"""
        /* Panel root */
        QWidget {{
            font-family: {UIHelpers.get_ui_font()};
            font-size: 13px;
        }}

        QDockWidget::title {{ padding: 0px; height: 0px; }}

        /* setMaxVisibleItems is documented to do nothing once a combo has a
           stylesheet; this is the rule that unblocks it. */
        QComboBox {{ combobox-popup: 0; }}

        /* Header icon buttons */
        QPushButton#HeaderButton {{
            background: transparent;
            border: none;
            font-size: 16px;
            padding: 0px;
            border-radius: 4px;
            min-width: 22px; max-width: 22px;
            min-height: 22px; max-height: 22px;
        }}

        /* Status line */
        QLabel#StatusLine {{
            font-size: 11px;
            padding: 0px 15px 3px 15px;
        }}

        /* The empty chat still tells the user where they stand */
        QLabel#EmptyState {{
            font-size: 12px;
            padding: 24px 20px;
        }}

        /* Input card */
        QFrame#InputContainer {{ border-radius: 12px; }}

        /* The Plan Gate keeps a container: a decision the user has to make
           must not read as another paragraph of output. */
        QFrame#GateCard {{
            border-radius: 12px;
            background-color: rgba(127, 127, 127, 40);
        }}

        QTextEdit#ChatInput {{
            border: none;
            background: transparent;
            font-size: 14px;
            padding: 4px 6px;
        }}

        /* Pill buttons */
        QPushButton#PillButton, QComboBox#PillButton {{
            border-radius: 6px;
            padding: 3px 10px;
            font-size: 11px;
        }}
        QComboBox#PillButton::drop-down {{ border: none; width: 14px; }}

        /* Circular send button */
        QPushButton#SendButton {{
            border: none;
            padding: 0px;
            border-radius: 15px;
            font-size: 15px;
            font-weight: bold;
            min-width: 30px; max-width: 30px;
            min-height: 30px; max-height: 30px;
        }}

        /* Scrollbars */
        QScrollBar:vertical {{
            border: none; background: transparent; width: 6px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            min-height: 20px; border-radius: 3px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """



# ── Paste-aware text edit ──────────────────────────────────────────────────────

class _PasteAwareTextEdit(QtWidgets.QTextEdit):
    """The composer: Enter sends, Shift+Enter inserts a newline, Ctrl+V images
    go to the UploadWidget."""

    paste_image_requested = QtCore.Signal()
    send_requested = QtCore.Signal()

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key_V and (event.modifiers() & QtCore.Qt.ControlModifier):
            mime = QtWidgets.QApplication.clipboard().mimeData()
            if mime.hasImage():
                self.paste_image_requested.emit()
                return   # consumed – don't paste raw image bytes into text field

        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) \
                and not (event.modifiers() & QtCore.Qt.ShiftModifier):
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


# ── Worker objects ─────────────────────────────────────────────────────────────

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
    """
    Blocks the worker thread until the user answers, on the GUI thread.

    Three questions go through here: the Plan Gate, the Owned Object rule, and
    the parameters Plan is missing. All are asked from the loop's thread and
    must be answered by a person, so the signal crosses to the GUI thread and
    the worker waits on an Event.
    """

    plan_gate = QtCore.Signal(object)      # steps
    owned_objects = QtCore.Signal(object)  # {"delete": [...]}
    missing_values = QtCore.Signal(object)  # the question, as text

    def __init__(self):
        super().__init__()
        self._answered = threading.Event()
        self._answer = None

    # ── worker thread ────────────────────────────────────────────────────────

    def about_the_plan(self, steps):
        return self._ask(self.plan_gate, steps)

    def about_objects_we_do_not_own(self, kinds):
        return self._ask(self.owned_objects, kinds)

    def about_missing_values(self, question):
        """
        The values Plan has not been given. An empty answer means "you choose".

        `abandon` releases every waiting question with an Approval, which is
        the Plan Gate's answer and not one of these — anything that is not
        text is nobody answering.
        """
        answer = self._ask(self.missing_values, question)
        return answer if isinstance(answer, str) else ""

    def _ask(self, signal, payload):
        self._answered.clear()
        self._answer = None
        signal.emit(payload)
        self._answered.wait()
        return self._answer

    # ── GUI thread ───────────────────────────────────────────────────────────

    def answer(self, value):
        self._answer = value
        self._answered.set()

    def abandon(self):
        """Release a waiting question so Stop is not swallowed by a prompt."""
        if not self._answered.is_set():
            from core.loop import REJECT, Approval

            self._answer = Approval(REJECT)
            self._answered.set()


class LoopWorker(QtCore.QObject):
    """
    Runs one Intent through the loop on a worker thread.

    It never reports progress itself — the event stream does that. All this
    signals is that the run is over.
    """

    finished = QtCore.Signal(object)
    document_named = QtCore.Signal(str, str)   # transcript path, Document name

    def __init__(self, bridge: EventBridge, ask: AskTheUser):
        super().__init__()
        self.bridge = bridge
        self.ask = ask
        self.client = None       # rebuilt from settings when the model changes
        self.pipeline = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        self.ask.abandon()

    def _get_client(self):
        if self.client is None:
            from core.llm import client_from_settings
            self.client = client_from_settings(settings_manager.instance().get_last_model())
        return self.client

    def _get_tools(self):
        from core.apply import ApplyPipeline
        from core.freecad_tools import build_registry

        if self.pipeline is None:
            self.pipeline = ApplyPipeline(confirm=self.ask.about_objects_we_do_not_own)
        return build_registry(pipeline=self.pipeline)

    @QtCore.Slot(str, object, str)
    def do_work(self, intent: str, attachments: list, transcript: str):
        from core import events as ev
        from core.events import EventStream
        from core.loop import run
        from utils.logger import generate_crash_report, logger

        self._stop.clear()
        events = EventStream([self.bridge.subscriber()])
        try:
            events.log_to(transcript)
        except Exception:
            pass    # a missing log must not cost the user their run

        events.subscribe(lambda event: logger.error("%s: %s" % (event.kind, event.data))
                         if event.kind in (ev.BLOCKED, ev.ERROR) else None)

        tools = self._get_tools()
        try:
            self.document_named.emit(transcript,
                                     str(tools.call("snapshot").get("document") or ""))
        except Exception:
            pass    # a nameless Session in history is not worth losing a run over

        try:
            result = run(intent, llm=self._get_client(), tools=tools,
                         events=events, cancelled=self._stop.is_set,
                         attachments=attachments, gate=self.ask.about_the_plan,
                         ask=self.ask.about_missing_values)
            self.finished.emit(result)
        except Exception as exc:
            events.emit("error", message=generate_crash_report(exc, "LoopWorker.do_work()"))
            self.finished.emit(None)


class _TitleThread(QtCore.QThread):
    """One short call naming a Session, off the UI thread so the run is not held up."""

    titled = QtCore.Signal(str)

    def __init__(self, intent: str, model_id: str, parent=None):
        super().__init__(parent)
        self._intent = intent
        self._model_id = model_id

    def run(self):
        from core.session_title import generate

        self.titled.emit(generate(self._intent, self._model_id))


# ── Main Panel ─────────────────────────────────────────────────────────────────

# Events replayed per event-loop turn when a Session is opened. Small enough
# that no single turn is felt, large enough that a short Session lands in one.
RESTORE_CHUNK = 25


class CopilotPanel(QtWidgets.QDockWidget):
    request_work = QtCore.Signal(str, object, str)

    def __init__(self):
        super().__init__("FreeCAD Copilot")
        # QMainWindow::saveState() drops unnamed docks — without this the panel's
        # position is never restored across restarts.
        self.setObjectName("FreeCadCopilotPanel")
        self.setStyleSheet(_build_qss())

        root = QtWidgets.QWidget()
        root.setObjectName("MainContainer")
        self.setWidget(root)

        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 15)
        root_layout.setSpacing(0)

        # ── 1. Header bar ─────────────────────────────────────────────────────
        header = self._build_header()
        root_layout.addWidget(header)

        # ── 2. Chat session manager (stacked widget) ──────────────────────────
        self.chat_sessions = ChatSessionManager()
        self.chat_sessions.setObjectName("ChatArea")
        self.chat_sessions.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        root_layout.addWidget(self.chat_sessions, stretch=1)

        # ── History Popup ──
        from .history_popup import HistoryPopup
        self.history_popup = HistoryPopup(self)
        self.history_popup.session_selected.connect(self.switch_session)
        self.history_popup.session_deleted.connect(self.delete_session)

        # ── 3. Status line ────────────────────────────────────────────────────
        self.status_line = QtWidgets.QLabel("")
        self.status_line.setObjectName("StatusLine")
        self.status_line.setVisible(False)
        self.status_line.setWordWrap(True)
        # This label is outside the transcript's scroll area, so its minimum
        # width is the panel's. A Step description it cannot break — one long
        # token, or wrapping off entirely — widens the dock, and Qt does not
        # give the width back when the text goes short again.
        self.status_line.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                       QtWidgets.QSizePolicy.Preferred)
        root_layout.addWidget(self.status_line)

        # ── 4. Floating input card ─────────────────────────────────────────────
        input_card = self._build_input_card()
        root_layout.addWidget(input_card)

        # ── Worker thread ──────────────────────────────────────────────────────
        self.thread = QtCore.QThread()
        self.bridge = EventBridge()
        self.ask = AskTheUser()
        self.worker = LoopWorker(self.bridge, self.ask)
        self.worker.moveToThread(self.thread)

        self.request_work.connect(self.worker.do_work)
        self.worker.finished.connect(self.on_run_finished)
        self.worker.document_named.connect(self.on_document_named)
        self.bridge.event.connect(self.on_event)
        self.ask.plan_gate.connect(self.on_plan_gate)
        self.ask.owned_objects.connect(self.on_owned_objects)
        self.ask.missing_values.connect(self.on_missing_values)

        self._running = False
        self._empty_state = None
        self._sent_attachments: list[str] = []
        self._title_threads = []
        self._reset_render_state()

        self.thread.start()
        
        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._cleanup_thread)

        self.chat_sessions.session_changed.connect(self.update_header_title)
        self.chat_sessions.session_removed.connect(lambda _: self.update_header_title(self.chat_sessions.stack.currentIndex()))

        # Every launch opens an empty Session; the earlier ones stay in the
        # picker with their transcripts intact (FCC-42).
        self.chat_sessions.load()
        self.new_chat()

    # ── Builder helpers ────────────────────────────────────────────────────────

    def _build_header(self):
        bar = QtWidgets.QWidget()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(44)

        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)

        self.title_label = QtWidgets.QLabel("New Chat")
        self.title_label.setObjectName("HeaderTitle")
        self.title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.title_label.setToolTip("Double-click to rename this session")
        self.title_label.setCursor(QtCore.Qt.IBeamCursor)
        self.title_label.mouseDoubleClickEvent = lambda event: self.rename_current_session()
        layout.addWidget(self.title_label)

        layout.addStretch()

        self.new_btn = QtWidgets.QPushButton()
        self.new_btn.setObjectName("HeaderButton")
        self.new_btn.setFixedSize(22, 22)
        self.new_btn.setToolTip("New chat session")
        self.new_btn.setIcon(UIHelpers.get_icon("plus", size=18, on=self.new_btn))
        self.new_btn.clicked.connect(self.new_chat)
        layout.addWidget(self.new_btn)

        self.history_btn = QtWidgets.QPushButton()
        self.history_btn.setObjectName("HeaderButton")
        self.history_btn.setFixedSize(22, 22)
        self.history_btn.setToolTip("Chat history")
        self.history_btn.setIcon(UIHelpers.get_icon("history", size=18, on=self.history_btn))
        self.history_btn.clicked.connect(self.toggle_history)
        layout.addWidget(self.history_btn)

        return bar

    def _build_input_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("InputContainer")

        inner = QtWidgets.QVBoxLayout(card)
        inner.setContentsMargins(0, 6, 0, 8)
        inner.setSpacing(0)

        # ── File chip bar (hidden when empty) ──
        self.chip_bar = FileChipBar()
        inner.addWidget(self.chip_bar)

        # ── Text input ──
        self.user_input = _PasteAwareTextEdit()
        self.user_input.setObjectName("ChatInput")
        self.user_input.setPlaceholderText("Ask FreeCAD Copilot…")
        self.user_input.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.user_input.paste_image_requested.connect(
            lambda: self.upload_widget.handle_clipboard_paste()
        )
        self.user_input.send_requested.connect(self.on_send)
        self.user_input.textChanged.connect(self._fit_composer_to_text)
        inner.addWidget(self.user_input)
        self._fit_composer_to_text()

        div = QtWidgets.QFrame()
        div.setFrameShape(QtWidgets.QFrame.HLine)
        div.setStyleSheet("QFrame { margin: 0 12px; }")
        inner.addWidget(div)

        # ── Action strip ──
        strip = QtWidgets.QHBoxLayout()
        strip.setContentsMargins(8, 5, 8, 3)
        strip.setSpacing(6)

        self.upload_widget = UploadWidget()
        self.upload_widget.file_attached.connect(self.chip_bar.add_file)
        self.chip_bar.file_removed.connect(self.upload_widget.discard)
        strip.addWidget(self.upload_widget)

        # ── Model selector pill ───────────────────────────────────────────────
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setObjectName("PillButton")
        self.model_combo.setFixedHeight(24)
        self.model_combo.setToolTip("Active LLM model")
        self.model_combo.setMaxVisibleItems(12)
        self._refresh_model_combo(provider_manager.instance().get_available_models())
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        strip.addWidget(self.model_combo)

        provider_manager.instance().models_changed.connect(self._refresh_model_combo)
        strip.addStretch()

        # ── Send and Stop share one slot ──────────────────────────────────────
        self.send_button = QtWidgets.QPushButton()
        self.send_button.setObjectName("SendButton")
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.clicked.connect(self.on_send)
        strip.addWidget(self.send_button)

        self.stop_button = QtWidgets.QPushButton("■")
        self.stop_button.setObjectName("SendButton")
        self.stop_button.setToolTip("Stop after the current attempt")
        self.stop_button.clicked.connect(self.on_stop)
        self.stop_button.setVisible(False)
        strip.addWidget(self.stop_button)

        inner.addLayout(strip)
        return card

    def _fit_composer_to_text(self):
        """
        The composer is one row when empty and grows with what is typed, up to
        ~40% of the panel, after which it scrolls.

        A percentage rather than a row count: the panel is narrow and dockable,
        so a height that is comfortable at one width wraps to nonsense at
        another. Frozen at three rows, as it was, anything past the third row
        was unreachable — it neither grew nor scrolled (ADR-0011).
        """
        document = self.user_input.document()
        document.setTextWidth(self.user_input.viewport().width())
        one_row = QtGui.QFontMetrics(self.user_input.font()).lineSpacing() + 16
        ceiling = max(one_row, int(self.height() * 0.4))
        wanted = int(document.size().height()) + 16

        self.user_input.setFixedHeight(min(max(wanted, one_row), ceiling))
        self.user_input.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded if wanted > ceiling
            else QtCore.Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Qt resizes the dock while __init__ is still building it.
        if getattr(self, "user_input", None):
            self._fit_composer_to_text()   # the 40% ceiling moved with the panel

    def showEvent(self, event):
        """
        Icons are tinted once the panel is on screen.

        A widget carries the global stylesheet's colour only after Qt has
        polished it; asked in __init__ it still answers with the OS palette,
        which is black under every FreeCAD dark theme (ADR-0011).
        """
        super().showEvent(event)
        for button, icon in ((self.new_btn, "plus"),
                             (self.history_btn, "history"),
                             (self.send_button, "arrow_up")):
            button.setIcon(UIHelpers.get_icon(icon, size=18, on=button))

    # ── Model / Mode helpers ───────────────────────────────────────────────────

    def _refresh_model_combo(self, models=None):
        """
        Rebuild the model combo, grouped under provider headings.

        A heading is a row with its selectable and enabled flags cleared, so
        grouping costs a QStandardItemModel and no custom popup widget — and
        QComboBox's own type-to-jump keeps working over the whole list.
        """
        if models is None:
            models = provider_manager.instance().get_available_models()
        last_id = settings_manager.instance().get_last_model()

        self.model_combo.blockSignals(True)
        items = QtGui.QStandardItemModel(self.model_combo)
        if not models:
            items.appendRow(QtGui.QStandardItem("No models — add API key"))
        else:
            by_provider = {}
            for model in models:
                by_provider.setdefault(model["provider"], []).append(model)
            bold = QtGui.QFont()
            bold.setBold(True)
            for provider, entries in by_provider.items():
                heading = QtGui.QStandardItem(provider)
                heading.setFlags(QtCore.Qt.NoItemFlags)
                # Weight, not an indent: type-to-jump matches from the string's start.
                heading.setFont(bold)
                items.appendRow(heading)
                for model in entries:
                    row = QtGui.QStandardItem(model["display"])
                    row.setData(model["id"], QtCore.Qt.UserRole)
                    items.appendRow(row)
        self.model_combo.setModel(items)

        index = self.model_combo.findData(last_id) if last_id else -1
        if index < 0 and last_id:
            if models and not provider_manager.instance().awaiting_model_lists():
                index = self.model_combo.findData(self._retire_model(last_id))
            else:
                # The provider's real list is still on its way; showing anything
                # else here is the flash of a model the user never picked.
                row = QtGui.QStandardItem(last_id)
                row.setData(last_id, QtCore.Qt.UserRole)
                items.insertRow(0, row)
                index = 0
        if index < 0:
            index = 1 if models else 0
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)

    def _retire_model(self, model_id: str) -> str:
        """Every provider has answered and none offers ``model_id``. Say so."""
        settings = settings_manager.instance()
        # Row 1 is the first model under the first heading — the Preferences
        # default is only a preference, and it may never have been set.
        fallback = settings.get_default_model() or self.model_combo.itemData(1)
        settings.set_last_model(fallback)
        self.status_line.setText(
            "%s is no longer offered by its provider — switched to %s."
            % (model_id, fallback))
        self.status_line.setVisible(True)
        return fallback

    def _on_model_changed(self, _idx: int):
        mid = self.model_combo.currentData()
        if mid:
            settings_manager.instance().set_last_model(mid)
            if getattr(self, "worker", None):
                self.worker.client = None   # rebuilt from settings on the next send

    # ── Public slots ───────────────────────────────────────────────────────────

    def toggle_history(self):
        if self.history_popup.isVisible():
            self.history_popup.close()
        else:
            # Indexed only, and carrying its own position: a Session enters the
            # picker when it gets its name, on its first Intent. The live empty
            # Session is on screen, not in the history (FCC-42).
            self.history_popup.populate(self.chat_sessions.indexed_sessions())
            self.history_popup.drop_from_the_dock()

    def new_chat(self):
        # Its real name comes from its first Intent, so there is nothing to call
        # it yet — and "Chat 3" is what FCC-44 exists to remove.
        idx, _ = self.chat_sessions.add_new_chat("New Chat")
        self._became_the_live_session()
        self.update_header_title(idx)
        self._empty_state = self._build_empty_state()
        self.add_chat_widget(self._empty_state)

    def _reset_render_state(self):
        """What one run builds and the next must not draw into."""
        self._plan_widget = None
        self._thought = None
        self._thought_phase = None
        self._current_step = 0
        self._gate_card = None
        self._answer_card = None

    def _became_the_live_session(self):
        """
        Let go of the Session being left.

        Owned Objects are the live Session's alone — carried over, they would
        let a Step delete an object the assistant created somewhere else
        without ever asking.
        """
        self._reset_render_state()
        self._empty_state = None
        self.worker.pipeline = None

    def rename_current_session(self):
        index = self.chat_sessions.stack.currentIndex()
        info = self.chat_sessions.get_session_info(index)
        if info is None:
            return
        name, chosen = QtWidgets.QInputDialog.getText(
            self, "Rename session", "Name:", QtWidgets.QLineEdit.Normal, info["name"])
        if chosen and name.strip():
            self.chat_sessions.rename_session(index, name)
            self.update_header_title(index)

    def switch_session(self, index: int):
        """
        Open a Session, restoring its transcript the first time it is asked for.

        Refused mid-run: a run narrates into the Session it was started from,
        and moving out from under it loses track of which that was.
        """
        if self._running:
            self.status_line.setText("Finish or stop this run before switching sessions.")
            self.status_line.setVisible(True)
            return
        self.chat_sessions.switch_to(index)
        self._became_the_live_session()
        self.restore_session(index)

    def restore_session(self, index: int):
        """Replay the Session's log through the switch that rendered it live."""
        from core.events import replay

        info = self.chat_sessions.get_session_info(index)
        if info is None or not self.chat_sessions.mark_restored(index):
            return

        events, truncated = replay(info["log"])
        if not events:
            self._empty_state = self._build_empty_state()
            self.add_chat_widget(self._empty_state)
            return

        self._replay_chunk(index, events, 0, truncated)

    def _replay_chunk(self, index: int, events: list, start: int, truncated: bool):
        """
        Replay a batch, then hand the event loop a turn before the next one.

        Replaying hundreds of Steps in one pass on the GUI thread froze the
        panel for as long as it took. The first batch is still synchronous, so
        a short Session is on screen by the time this returns.

        A Session the user has already switched away from is abandoned rather
        than rendered into whatever is on the stack now.
        """
        if self.chat_sessions.stack.currentIndex() != index:
            return

        for event in events[start:start + RESTORE_CHUNK]:
            self.on_event(event)

        done = start + RESTORE_CHUNK
        if done < len(events):
            QtCore.QTimer.singleShot(
                0, lambda: self._replay_chunk(index, events, done, truncated))
            return

        if truncated:
            self.add_chat_widget(AssistantMessageWidget(
                "This transcript ends mid-event — FreeCAD closed while the run was writing. "
                "Everything above it survived."))
        self.status_line.setVisible(False)

    @QtCore.Slot(str, str)
    def on_document_named(self, transcript: str, document: str):
        """Record which Document a Session worked on, so history can say so."""
        self.chat_sessions.name_document(transcript, document)

    def delete_session(self, index: int):
        info = self.chat_sessions.get_session_info(index)
        if info is None:
            return
        confirmed = QtWidgets.QMessageBox.question(
            self, "Delete session",
            "Delete “%s” and its whole transcript?" % info["name"],
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if confirmed != QtWidgets.QMessageBox.Yes:
            return
        self.chat_sessions.remove_session(index)
        self.update_header_title(self.chat_sessions.stack.currentIndex())

    def _build_empty_state(self):
        """One muted line, so an empty panel still says where the user stands."""
        model = self.model_combo.currentText().strip() or "no model"
        label = QtWidgets.QLabel(
            "%s. The Plan Gate asks before anything is written to your Document."
            % model)
        label.setObjectName("EmptyState")
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignCenter)
        return label

    @QtCore.Slot(int)
    def update_header_title(self, index: int):
        info = self.chat_sessions.get_session_info(index)
        if info:
            self.title_label.setText(info["name"])

    # ── Sending an Intent ──────────────────────────────────────────────────────

    def _unusable_attachments(self) -> list:
        """
        Why the attached files would not reach the model, before the run starts.

        The chips stay on the bar, so the offending one can be taken off — or
        the model changed — and the same Intent sent again. A settings lookup
        that fails is not the user's problem here; the run reports it.
        """
        from core.llm import selected_model_name, unusable_attachments

        attached = self.chip_bar.files()
        if not attached:
            return []
        try:
            model = selected_model_name(self.model_combo.currentData())
        except Exception:
            return []
        return unusable_attachments(attached, model)

    def on_send(self):
        intent = self.user_input.toPlainText().strip()
        if not intent:
            return

        index = self.chat_sessions.stack.currentIndex()
        session = self.chat_sessions.get_session_info(index)
        if session is None:
            return

        unusable = self._unusable_attachments()
        if unusable:
            self.add_chat_widget(AssistantMessageWidget(
                "This would not have reached the model:\n\n"
                + "\n".join("• " + reason for reason in unusable)))
            return

        # After the attachment check, never before it: an Intent that is turned
        # away here never ran, and must not name the Session.
        self._name_session(index, session["log"], intent)

        attachments = self.chip_bar.get_and_clear()
        self._sent_attachments = attachments

        if self._empty_state is not None:
            self._empty_state.setParent(None)
            self._empty_state = None
        self.add_chat_widget(TextMessageWidget(intent))
        self.user_input.clear()

        self._reset_render_state()

        self._set_running(True)
        self.status_line.setText("Planning…")
        self.status_line.setVisible(True)
        self.request_work.emit(intent, attachments, session["log"])

    def _name_session(self, index: int, log: str, intent: str):
        """
        The Session takes its name from this Intent, and enters the picker.

        The truncated Intent goes up immediately so nothing is ever called
        "Untitled"; the generated title replaces it when it arrives, and is
        asked for again on the next Intent if this one produced none (FCC-44).
        """
        from core.session_title import fallback

        if self.chat_sessions.begin(index, fallback(intent)):
            # begin() bounds the index and may have dropped Sessions before this
            # one, so the position it had is not the position it has.
            index = self.chat_sessions.stack.currentIndex()
            self.update_header_title(index)
        if self.chat_sessions.wants_a_title(index):
            self._ask_for_a_title(log, intent)

    def _ask_for_a_title(self, log: str, intent: str):
        """Alongside the run, never in front of it."""
        thread = _TitleThread(intent, settings_manager.instance().get_naming_model(), self)
        thread.titled.connect(lambda title: self._title_arrived(log, title))
        # Qt owns the thread; without a reference it is collected mid-flight.
        self._title_threads.append(thread)
        thread.finished.connect(lambda: self._title_threads.remove(thread))
        thread.start()

    def _title_arrived(self, log: str, title: str):
        if self.chat_sessions.title_arrived(log, title):
            self.update_header_title(self.chat_sessions.stack.currentIndex())

    def on_stop(self):
        for card in ("_gate_card", "_answer_card"):
            if getattr(self, card, None) is not None:
                getattr(self, card).setEnabled(False)
                setattr(self, card, None)
        self.worker.stop()       # also releases a waiting question
        self.stop_button.setEnabled(False)
        self.status_line.setText("Stopping after the current attempt…")

    def _set_running(self, running: bool):
        self._running = running
        self.send_button.setVisible(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)

    # ── Rendering the event stream ─────────────────────────────────────────────

    @QtCore.Slot(object)
    def on_event(self, event):
        """
        The whole panel is this method.

        One branch per event kind, no calls back into the loop. If a kind is
        added and not handled here, it is simply not rendered — nothing breaks.
        """
        from core import events as ev

        data = event.data
        if event.kind == ev.THOUGHT:
            self._render_thought(data["phase"], data["text"])

        elif event.kind == ev.QUESTION:
            self.add_chat_widget(AssistantMessageWidget(data["text"]))

        elif event.kind == ev.ANSWER:
            # The user's own answer is already in the transcript; what is worth
            # saying is that nobody gave one (FCC-25).
            if data.get("by") != "user":
                self.add_chat_widget(AssistantMessageWidget(
                    "No values given, so the Plan will say what it assumed."))

        elif event.kind == ev.PLAN:
            self._render_plan(data["steps"], "Plan", data.get("reading", ""))

        elif event.kind == ev.PLAN_REVISED:
            self.add_chat_widget(AssistantMessageWidget("Revised the Plan: " + data["reason"]))
            self._render_plan(data["steps"], "Revised Plan", data.get("reading", ""))

        elif event.kind == ev.STEP_STARTED:
            self._thought = None
            self._current_step = data["index"]
            if self._plan_widget:
                self._plan_widget.highlight(data["index"])
            self.status_line.setText("Step %d/%d: %s" % (data["index"], data["total"],
                                                         data["description"]))
            self.status_line.setVisible(True)

        elif event.kind == ev.TOOL_CALL:
            self._render_tool_call(data["name"], data["arguments"])

        elif event.kind == ev.TOOL_RESULT:
            self.status_line.setText("%s → %s" % (data["name"],
                                                  data["result"].get("status", "done")))

        elif event.kind == ev.PREFLIGHT_FAILED:
            self._render_failure(data["attempt"], data["traceback"])

        elif event.kind == ev.APPLIED:
            created = ", ".join(data.get("created") or []) or "no new objects"
            self.add_chat_widget(AssistantMessageWidget(
                "✓ Applied “%s” — %s. Ctrl+Z undoes this Step." % (data["label"], created)))
            if self._plan_widget:
                self._plan_widget.mark(data["step"], PLAN_DONE)

        elif event.kind == ev.RENDER:
            self._render_image(data["path"])

        elif event.kind == ev.REVIEW_VERDICT:
            mark = "✓" if data["verdict"] == "pass" else "✗"
            self.add_chat_widget(AssistantMessageWidget(
                "%s Review: %s — %s" % (mark, data["verdict"], data["reason"])))

        elif event.kind == ev.BLOCKED:
            if self._plan_widget and self._current_step:
                self._plan_widget.mark(self._current_step, PLAN_FAILED)
            self._render_blocked(data["reason"], data.get("tried") or [])

        elif event.kind == ev.ERROR:
            row = AccordionWidget("✗ Something went wrong")
            self.add_chat_widget(row)
            row.append_text(data["message"])

    def _render_thought(self, phase, text):
        """Reasoning appends to one accordion per phase turn, as it forms."""
        if self._thought is None or self._thought_phase != phase:
            self._thought = AccordionWidget("Thinking (%s)…" % phase)
            self._thought_phase = phase
            self.add_chat_widget(self._thought)
            self._thought.start_pulsing()
        self._thought.append_text(text)
        self.status_line.setText(("Thinking: " + text.replace("\n", " "))[-90:])
        self.status_line.setVisible(True)

    def _render_plan(self, steps, title, reading=""):
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
        if reading:
            # Above the Plan, and open: correcting a misreading at the Gate is
            # the whole point, and a reading one click away is not read (FCC-47).
            how = AccordionWidget("How the drawing was read")
            self.add_chat_widget(how)
            how.append_text(reading)
            how.toggle_content()
        self._plan_widget = PlanWidget(steps, title=title)
        self.add_chat_widget(self._plan_widget)

    def _render_tool_call(self, name, arguments):
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
        if "code" in arguments:
            self.add_chat_widget(CodeBlockWidget(arguments["code"]))
        else:
            # One collapsible shape in the stream, not three (ADR-0011).
            row = AccordionWidget(name)
            self.add_chat_widget(row)
            row.append_text(str(arguments))

    def _render_failure(self, attempt, traceback):
        panel = AccordionWidget("✗ Preflight failed (attempt %d) — retrying" % attempt)
        self.add_chat_widget(panel)
        panel.append_text(traceback)

    def _render_blocked(self, reason, tried):
        self.add_chat_widget(AssistantMessageWidget("⏸  " + reason))
        if tried:
            panel = AccordionWidget("What was tried (%d)" % len(tried))
            self.add_chat_widget(panel)
            for entry in tried:
                panel.append_text(str(entry.get("code") or entry.get("review") or entry))
                if entry.get("traceback"):
                    panel.append_text("\n" + entry["traceback"])
                panel.append_text("\n" + "─" * 40 + "\n")

    def _render_image(self, path):
        label = QtWidgets.QLabel()
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            return
        label.setPixmap(pixmap.scaledToWidth(360, QtCore.Qt.SmoothTransformation))
        label.setToolTip("The render Review judged this run by: " + path)
        label.setStyleSheet("padding: 6px 12px;")
        self.add_chat_widget(label)

    # ── The two places the user keeps control ──────────────────────────────────

    @QtCore.Slot(object)
    def on_plan_gate(self, steps):
        """
        The Plan Gate. One approval covers every Step; there is no per-Step
        approval, so this appears once per Plan and the run waits on it.
        """
        self.status_line.setText("Waiting for you to approve the Plan…")
        self.status_line.setVisible(True)
        self._gate_card = self._build_gate_card(len(steps))
        self.add_chat_widget(self._gate_card)

    def _build_gate_card(self, step_count):
        from core.loop import AMEND, APPROVE, REJECT, Approval

        card = QtWidgets.QFrame()
        card.setObjectName("GateCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        prompt = QtWidgets.QLabel(
            "Run this Plan? Approving covers all %d Steps — you will not be asked again."
            % step_count)
        prompt.setWordWrap(True)
        prompt.setStyleSheet("font-size: 12px; font-weight: 600;")
        layout.addWidget(prompt)

        note = QtWidgets.QLabel("To amend it, say what to change in the box below, then Amend.")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        layout.addWidget(note)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)

        def answer(approval):
            card.setEnabled(False)
            self._gate_card = None
            self.status_line.setText("Working…")
            self.ask.answer(approval)

        approve = QtWidgets.QPushButton("✓  Approve")
        approve.setObjectName("PillButton")
        approve.clicked.connect(lambda: answer(Approval(APPROVE)))
        buttons.addWidget(approve)

        amend = QtWidgets.QPushButton("✎  Amend")
        amend.setObjectName("PillButton")
        amend.clicked.connect(lambda: self._amend(answer))
        buttons.addWidget(amend)

        reject = QtWidgets.QPushButton("✗  Reject")
        reject.setObjectName("PillButton")
        reject.clicked.connect(lambda: answer(Approval(REJECT)))
        buttons.addWidget(reject)

        buttons.addStretch()
        layout.addLayout(buttons)
        return card

    def _amend(self, answer):
        from core.loop import AMEND, Approval

        feedback = self.user_input.toPlainText().strip()
        if not feedback:
            self.user_input.setPlaceholderText("Say what to change, then press Amend…")
            self.user_input.setFocus()
            return
        self.user_input.clear()
        self.add_chat_widget(TextMessageWidget(feedback))
        answer(Approval(AMEND, feedback))

    @QtCore.Slot(object)
    def on_missing_values(self, question):
        """
        The values Plan has not been given (FCC-25).

        The question itself is already in the transcript — it arrived as an
        event. This is only the card that takes the answer, and it takes it
        from the composer, the same way Amend does.
        """
        self.status_line.setText("Waiting for the values the Plan needs…")
        self.status_line.setVisible(True)
        self._answer_card = self._build_answer_card()
        self.add_chat_widget(self._answer_card)

    def _build_answer_card(self):
        card = QtWidgets.QFrame()
        card.setObjectName("GateCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        note = QtWidgets.QLabel(
            "Answer in the box below, then Send. Or let the Copilot choose — it will "
            "write each assumed value into the Plan, so you can still correct it.")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        layout.addWidget(note)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)

        def answer(text):
            card.setEnabled(False)
            self._answer_card = None
            self.status_line.setText("Working…")
            self.ask.answer(text)

        send = QtWidgets.QPushButton("↩  Send")
        send.setObjectName("PillButton")
        send.clicked.connect(lambda: self._answer_values(answer))
        buttons.addWidget(send)

        decide = QtWidgets.QPushButton("✎  You decide")
        decide.setObjectName("PillButton")
        decide.clicked.connect(lambda: answer(""))
        buttons.addWidget(decide)

        buttons.addStretch()
        layout.addLayout(buttons)
        return card

    def _answer_values(self, answer):
        text = self.user_input.toPlainText().strip()
        if not text:
            self.user_input.setPlaceholderText("Give the values, then press Send…")
            self.user_input.setFocus()
            return
        self.user_input.clear()
        self.add_chat_widget(TextMessageWidget(text))
        answer(text)

    @QtCore.Slot(object)
    def on_owned_objects(self, kinds):
        """
        The Owned Object rule. Deletion only — a modification is covered by the
        Plan Gate, which approved a Step that said the object changes.
        """
        deletes = list(kinds.get("delete") or ())
        wanted = "delete " + ", ".join(deletes)

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Your own objects")
        box.setText("The assistant wants to " + wanted + ".")
        box.setInformativeText(
            "It did not create %s in this session, so this is your own work. "
            "Ctrl+Z will still undo it as one Step."
            % ("these" if len(deletes) > 1 else "it"))
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.No)

        allowed = box.exec_() == QtWidgets.QMessageBox.Yes
        self.add_chat_widget(AssistantMessageWidget(
            ("You allowed it to " if allowed else "You refused to let it ") + wanted + "."))
        self.ask.answer(allowed)

    @QtCore.Slot(object)
    def on_run_finished(self, result):
        self._set_running(False)
        self.upload_widget.discard(*self._sent_attachments)
        self._sent_attachments = []
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
        if result is not None and result.ok:
            self.status_line.setVisible(False)
        else:
            self.status_line.setText("Stopped. Your Document is unchanged beyond the "
                                     "Steps already applied.")
            self.status_line.setVisible(True)

    def add_chat_widget(self, widget: QtWidgets.QWidget):
        chat_history = self.chat_sessions.get_current_chat_history()
        if not chat_history:
            return None
            
        container = chat_history.widget()
        if container and container.layout():
            container.layout().addWidget(widget)
            
        QtCore.QTimer.singleShot(10, lambda: chat_history.verticalScrollBar().setValue(
            chat_history.verticalScrollBar().maximum()
        ))
        
        return widget

    def _cleanup_thread(self):
        """Safely stops the background worker thread."""
        if hasattr(self, 'thread') and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait()

    def closeEvent(self, event):
        self.upload_widget.discard_temporary()
        self._cleanup_thread()
        super().closeEvent(event)
