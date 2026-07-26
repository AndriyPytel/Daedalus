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
from utils.config_manager import ConfigManager
from utils.settings_manager import SettingsManager
from utils.provider_manager import ProviderManager
from utils.prompt_manager   import PromptManager
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
            font-family: {ConfigManager.get_ui_font()};
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

    Two questions go through here: the Plan Gate, and the Owned Object rule.
    Both are asked from the loop's thread and must be answered by a person, so
    the signal crosses to the GUI thread and the worker waits on an Event.
    """

    plan_gate = QtCore.Signal(object)      # steps
    owned_objects = QtCore.Signal(object)  # {"modify": [...], "delete": [...]}

    def __init__(self):
        super().__init__()
        self._answered = threading.Event()
        self._answer = None

    # ── worker thread ────────────────────────────────────────────────────────

    def about_the_plan(self, steps):
        return self._ask(self.plan_gate, steps)

    def about_objects_we_do_not_own(self, kinds):
        return self._ask(self.owned_objects, kinds)

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
            self.client = client_from_settings(SettingsManager.instance().get_last_model())
        return self.client

    def _get_tools(self):
        from core.apply import ApplyPipeline
        from core.freecad_tools import build_registry

        if self.pipeline is None:
            # One pipeline for the Session, so Owned Objects are remembered
            # across every Intent the user sends against this Document.
            self.pipeline = ApplyPipeline(confirm=self.ask.about_objects_we_do_not_own)
        return build_registry(pipeline=self.pipeline)

    @QtCore.Slot(str)
    def do_work(self, intent: str):
        from core import events as ev
        from core.events import EventStream
        from core.loop import run
        from utils.logger import generate_crash_report, get_session_log_path, logger

        self._stop.clear()
        events = EventStream([self.bridge.subscriber()])
        try:
            events.log_to(get_session_log_path() + ".events.jsonl")
        except Exception:
            pass    # a missing log must not cost the user their run

        # Why a run stopped belongs in the session log too, not only in the
        # FreeCAD console where it scrolls away.
        events.subscribe(lambda event: logger.error("%s: %s" % (event.kind, event.data))
                         if event.kind in (ev.BLOCKED, ev.ERROR) else None)

        try:
            result = run(intent, llm=self._get_client(), tools=self._get_tools(),
                         events=events, cancelled=self._stop.is_set,
                         gate=self.ask.about_the_plan)
            self.finished.emit(result)
        except Exception as exc:
            events.emit("error", message=generate_crash_report(exc, "LoopWorker.do_work()"))
            self.finished.emit(None)


# ── Main Panel ─────────────────────────────────────────────────────────────────

class CopilotPanel(QtWidgets.QDockWidget):
    request_work = QtCore.Signal(str)

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
        self.history_popup.session_selected.connect(self.chat_sessions.switch_to)
        self.history_popup.session_deleted.connect(self.chat_sessions.remove_session)

        # ── 3. Status line ────────────────────────────────────────────────────
        self.status_line = QtWidgets.QLabel("")
        self.status_line.setObjectName("StatusLine")
        self.status_line.setVisible(False)
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
        self.bridge.event.connect(self.on_event)
        self.ask.plan_gate.connect(self.on_plan_gate)
        self.ask.owned_objects.connect(self.on_owned_objects)

        # Per-run render state, reset by on_send.
        self._plan_widget = None
        self._thought = None
        self._thought_phase = None
        self._current_step = 0
        self._gate_card = None
        self._empty_state = None

        self.thread.start()
        
        # Ensure thread is cleaned up when application quits
        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._cleanup_thread)

        # Wire events
        self.chat_sessions.session_changed.connect(self.update_header_title)
        self.chat_sessions.session_removed.connect(lambda _: self.update_header_title(self.chat_sessions.stack.currentIndex()))

        self.new_chat()

    # ── Builder helpers ────────────────────────────────────────────────────────

    def _build_header(self):
        bar = QtWidgets.QWidget()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(44)

        import os
        icon_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Resources", "icons")

        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)

        # Chat Title Label
        self.title_label = QtWidgets.QLabel("New Chat")
        self.title_label.setObjectName("HeaderTitle")
        self.title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # "New chat" icon
        self.new_btn = QtWidgets.QPushButton()
        self.new_btn.setObjectName("HeaderButton")
        self.new_btn.setFixedSize(22, 22)
        self.new_btn.setToolTip("New chat session")
        self.new_btn.setIcon(UIHelpers.get_icon("plus", size=18, on=self.new_btn))
        self.new_btn.clicked.connect(self.new_chat)
        layout.addWidget(self.new_btn)

        # History button
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

        # Divider
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
        strip.addWidget(self.upload_widget)

        # ── Model selector pill ───────────────────────────────────────────────
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setObjectName("PillButton")
        self.model_combo.setFixedHeight(24)
        self.model_combo.setToolTip("Active LLM model")
        self.model_combo.setMaxVisibleItems(12)
        self._refresh_model_combo(ProviderManager.instance().get_available_models())
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        strip.addWidget(self.model_combo)

        # Wire live updates from ProviderManager
        ProviderManager.instance().models_changed.connect(self._refresh_model_combo)
        strip.addStretch()

        # ── Send and Stop share one slot ──────────────────────────────────────
        # Only one is ever visible, so the strip does not shift as a run starts.
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
            models = ProviderManager.instance().get_available_models()
        last_id = SettingsManager.instance().get_last_model()

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

        # Restore selection — the default model catches a last-used one that a
        # refreshed list no longer offers; index 1 catches both missing, since
        # index 0 is a provider heading and cannot be chosen.
        for wanted in (last_id, SettingsManager.instance().get_default_model()):
            index = self.model_combo.findData(wanted)
            if index >= 0:
                break
        else:
            index = 1 if models else 0
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)

    def _on_model_changed(self, _idx: int):
        mid = self.model_combo.currentData()
        if mid:
            SettingsManager.instance().set_last_model(mid)
            if getattr(self, "worker", None):
                self.worker.client = None   # rebuilt from settings on the next send

    # ── Public slots ───────────────────────────────────────────────────────────

    def toggle_history(self):
        if self.history_popup.isVisible():
            self.history_popup.close()
        else:
            # Refresh list
            sessions = []
            for i in range(self.chat_sessions.count()):
                info = self.chat_sessions.get_session_info(i)
                if info:
                    sessions.append(info)
            self.history_popup.populate(sessions)
            
            # Position in the center of the screen
            self.history_popup.show_center()

    def new_chat(self):
        n = self.chat_sessions.count() + 1
        idx, _ = self.chat_sessions.add_new_chat(f"Chat {n}")
        self.update_header_title(idx)
        self._empty_state = self._build_empty_state()
        self.add_chat_widget(self._empty_state)

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

    def on_send(self):
        intent = self.user_input.toPlainText().strip()
        if not intent:
            return

        self.chip_bar.get_and_clear()
        self.upload_widget.get_and_clear_files()

        if self._empty_state is not None:
            self._empty_state.setParent(None)
            self._empty_state = None
        self.add_chat_widget(TextMessageWidget(intent))
        self.user_input.clear()

        self._plan_widget = None
        self._thought = None
        self._thought_phase = None
        self._current_step = 0
        self._gate_card = None

        self._set_running(True)
        self.status_line.setText("Planning…")
        self.status_line.setVisible(True)
        self.request_work.emit(intent)

    def on_stop(self):
        if self._gate_card is not None:
            self._gate_card.setEnabled(False)
            self._gate_card = None
        self.worker.stop()       # also releases a waiting question
        self.stop_button.setEnabled(False)
        self.status_line.setText("Stopping after the current attempt…")

    def _set_running(self, running: bool):
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

        elif event.kind == ev.PLAN:
            self._render_plan(data["steps"], "Plan")

        elif event.kind == ev.PLAN_REVISED:
            self.add_chat_widget(AssistantMessageWidget("Revised the Plan: " + data["reason"]))
            self._render_plan(data["steps"], "Revised Plan")

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

    def _render_plan(self, steps, title):
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
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
    def on_owned_objects(self, kinds):
        """
        The Owned Object rule. Asked whatever the Plan Gate already approved,
        because these objects are the user's own work.
        """
        wanted = []
        if kinds.get("modify"):
            wanted.append("modify " + ", ".join(kinds["modify"]))
        if kinds.get("delete"):
            wanted.append("delete " + ", ".join(kinds["delete"]))

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Your own objects")
        box.setText("The assistant wants to " + " and ".join(wanted) + ".")
        box.setInformativeText(
            "It did not create %s in this session, so this is your own work. "
            "Ctrl+Z will still undo it as one Step."
            % ("these" if sum(len(v) for v in kinds.values()) > 1 else "it"))
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.No)

        allowed = box.exec_() == QtWidgets.QMessageBox.Yes
        self.add_chat_widget(AssistantMessageWidget(
            ("You allowed it to " if allowed else "You refused to let it ")
            + " and ".join(wanted) + "."))
        self.ask.answer(allowed)

    @QtCore.Slot(object)
    def on_run_finished(self, result):
        self._set_running(False)
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
            
        # Give Qt a tiny moment to process layout before scrolling
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
        self._cleanup_thread()
        super().closeEvent(event)
