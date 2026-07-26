# FreeCadCopilot/ui/copilot_panel.py
"""
CopilotPanel (Round 2 – Antigravity UI)

Key changes vs Round 1
─────────────────────
- All colours derived from QPalette – adapts to FreeCAD ProDark + Light themes
- Removed fixed Execution Layer block → single `StatusLine` label (hides when idle)
- QTabWidget removed → ChatSessionManager + QComboBox header picker
- "×" (close) header button removed (FreeCAD dock already provides one)
- FileChipBar lives inside the input card, above QTextEdit
- Clipboard paste (Ctrl+V with image) handled via PasteAwareTextEdit
- step_callback wires the pipeline's live progress into the status line
"""
import FreeCAD

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from .chat_widgets        import TextMessageWidget, AssistantMessageWidget
from .multi_tab_manager   import ChatSessionManager
from .upload_widget       import UploadWidget, FileChipBar
from .accordion_widget    import AccordionWidget
from utils.config_manager import ConfigManager
from utils.settings_manager import SettingsManager
from utils.provider_manager import ProviderManager
from utils.prompt_manager   import PromptManager
from ui.ui_helpers import UIHelpers


# ── Stylesheet ────────────────────────────────────────────────────────────────

def _build_qss() -> str:
    """
    Return the panel stylesheet.
    All colours use Qt's native palette() keyword so the panel inherits
    the active FreeCAD theme (ProDark, Light, etc.) with zero Python-side
    palette calls — avoids PySide2 QPalette.color() signature issues.
    """
    return f"""
        /* Panel root */
        QWidget {{
            font-family: {ConfigManager.get_ui_font()};
            font-size: 13px;
        }}

        QDockWidget::title {{
            background: palette(window);
            padding: 0px; height: 0px;
        }}

        /* Header bar */
        QWidget#HeaderBar {{
            background-color: palette(window);
            border-bottom: 1px solid palette(mid);
        }}

        /* Session picker */
        QComboBox#SessionPicker {{
            background-color: palette(button);
            border: 1px solid palette(mid);
            border-radius: 6px;
            padding: 2px 8px;
            font-size: 12px;
            color: palette(text);
            min-width: 90px;
            max-width: 140px;
        }}
        QComboBox#SessionPicker::drop-down {{ border: none; width: 16px; }}
        
        /* Dropdown popups (applies to all comboboxes in panel) */
        QComboBox QAbstractItemView {{
            background-color: palette(window);
            color: palette(text);
            border: 1px solid palette(mid);
            selection-background-color: palette(highlight);
            selection-color: palette(highlightedtext);
            outline: none;
        }}

        /* Header icon buttons */
        QPushButton#HeaderButton {{
            background: transparent;
            border: none;
            color: palette(text);
            font-size: 16px;
            padding: 0px;
            border-radius: 4px;
            min-width: 22px; max-width: 22px;
            min-height: 22px; max-height: 22px;
        }}
        QPushButton#HeaderButton:hover {{
            background-color: palette(midlight);
            color: palette(text);
        }}

        /* Status line */
        QLabel#StatusLine {{
            font-size: 11px;
            color: palette(mid);
            padding: 0px 15px 3px 15px;
        }}

        /* Chat area */
        QWidget#ChatArea {{
            background-color: palette(base);
        }}

        /* Input card */
        QFrame#InputContainer {{
            background-color: palette(base);
            border: 1px solid palette(mid);
            border-radius: 12px;
        }}

        QTextEdit#ChatInput {{
            border: none;
            background: transparent;
            font-size: 14px;
            padding: 4px 6px;
            color: palette(text);
        }}

        /* Pill buttons */
        QPushButton#PillButton {{
            background-color: palette(button);
            border: 1px solid palette(mid);
            border-radius: 6px;
            padding: 3px 10px;
            font-size: 11px;
            color: palette(buttontext);
        }}
        QPushButton#PillButton:hover {{ background-color: palette(midlight); }}

        /* Circular send button */
        QPushButton#SendButton {{
            background-color: palette(highlight);
            color: palette(highlightedtext);
            border: none;
            border-radius: 15px;
            font-size: 15px;
            font-weight: bold;
            min-width: 30px; max-width: 30px;
            min-height: 30px; max-height: 30px;
        }}
        QPushButton#SendButton:hover  {{ background-color: palette(dark); }}
        QPushButton#SendButton:disabled {{ background-color: palette(mid); }}

        /* Scrollbars */
        QScrollBar:vertical {{
            border: none; background: transparent; width: 6px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: palette(mid); min-height: 20px; border-radius: 3px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """



# ── Paste-aware text edit ──────────────────────────────────────────────────────

class _PasteAwareTextEdit(QtWidgets.QTextEdit):
    """QTextEdit that hands Ctrl+V images off to the UploadWidget."""

    paste_image_requested = QtCore.Signal()

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key_V and (event.modifiers() & QtCore.Qt.ControlModifier):
            mime = QtWidgets.QApplication.clipboard().mimeData()
            if mime.hasImage():
                self.paste_image_requested.emit()
                return   # consumed – don't paste raw image bytes into text field
        super().keyPressEvent(event)


# ── Worker objects ─────────────────────────────────────────────────────────────

class TokenEmitter(QtCore.QObject):
    signal_token_received = QtCore.Signal(str)
    signal_reasoning_received = QtCore.Signal(str)

class StepEmitter(QtCore.QObject):
    signal_step = QtCore.Signal(str, int, int)   # (name, idx, total)

class AiWorker(QtCore.QObject):
    """Streams a reply on the worker thread; the panel only ever sees signals."""

    finished = QtCore.Signal(dict)
    error    = QtCore.Signal(str)

    def __init__(self, token_emitter: TokenEmitter, step_emitter: StepEmitter):
        super().__init__()
        self.token_emitter = token_emitter
        self.step_emitter  = step_emitter
        self.client = None   # injectable seam; built from settings on first use

    def _get_client(self):
        if self.client is None:
            from core.llm import client_from_settings
            self.client = client_from_settings(SettingsManager.instance().get_last_model())
        return self.client

    @QtCore.Slot(str)
    def do_work(self, user_input: str):
        from core.llm import REASONING

        try:
            reply = {"text": "", "reasoning": ""}
            for chunk in self._get_client().stream([{"role": "user", "content": user_input}]):
                if chunk.kind == REASONING:
                    reply["reasoning"] += chunk.text
                    self.token_emitter.signal_reasoning_received.emit(chunk.text)
                else:
                    reply["text"] += chunk.text
                    self.token_emitter.signal_token_received.emit(chunk.text)
            self.finished.emit(reply)
        except Exception as exc:
            from utils.logger import generate_crash_report
            self.error.emit(generate_crash_report(exc, "AiWorker.do_work()"))


# ── Main Panel ─────────────────────────────────────────────────────────────────

class CopilotPanel(QtWidgets.QDockWidget):
    request_work = QtCore.Signal(str)

    def __init__(self):
        super().__init__("FreeCAD Copilot")
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
        self.thread        = QtCore.QThread()
        self.token_emitter = TokenEmitter()
        self.step_emitter  = StepEmitter()
        self.worker        = AiWorker(self.token_emitter, self.step_emitter)
        self.worker.moveToThread(self.thread)

        self.request_work.connect(self.worker.do_work)
        self.worker.finished.connect(self.on_ai_response_received)
        self.worker.error.connect(self.on_ai_error)
        self.token_emitter.signal_token_received.connect(self.on_token_received)
        self.token_emitter.signal_reasoning_received.connect(self.on_reasoning_received)
        self.step_emitter.signal_step.connect(self.on_step_update)

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
        self.title_label.setStyleSheet("font-weight: 600; font-size: 13px; color: palette(text);")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # "New chat" icon
        self.new_btn = QtWidgets.QPushButton()
        self.new_btn.setObjectName("HeaderButton")
        self.new_btn.setFixedSize(22, 22)
        self.new_btn.setToolTip("New chat session")
        self.new_btn.setIcon(UIHelpers.get_icon("plus", size=18))
        self.new_btn.clicked.connect(self.new_chat)
        layout.addWidget(self.new_btn)

        # History button
        self.history_btn = QtWidgets.QPushButton()
        self.history_btn.setObjectName("HeaderButton")
        self.history_btn.setFixedSize(22, 22)
        self.history_btn.setToolTip("Chat history")
        self.history_btn.setIcon(UIHelpers.get_icon("refresh", size=18)) # Using refresh as a clock/history sub
        self.history_btn.clicked.connect(self.toggle_history)
        layout.addWidget(self.history_btn)

        return bar

    def _build_input_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("InputContainer")

        shadow = QtWidgets.QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QtGui.QColor(0, 0, 0, 30))
        card.setGraphicsEffect(shadow)

        card_layout = QtWidgets.QHBoxLayout()
        card_layout.setContentsMargins(15, 0, 15, 0)

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
        self.user_input.setFixedHeight(68)
        self.user_input.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.user_input.paste_image_requested.connect(
            lambda: self.upload_widget.handle_clipboard_paste()
        )
        inner.addWidget(self.user_input)

        # Divider
        div = QtWidgets.QFrame()
        div.setFrameShape(QtWidgets.QFrame.HLine)
        div.setStyleSheet("QFrame { color: palette(mid); margin: 0 12px; }")
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
        self.model_combo.setStyleSheet("""
            QComboBox#PillButton {
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QComboBox#PillButton::drop-down { border: none; width: 14px; }
        """)
        self._refresh_model_combo(ProviderManager.instance().get_available_models())
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        strip.addWidget(self.model_combo)

        # Wire live updates from ProviderManager
        ProviderManager.instance().models_changed.connect(self._refresh_model_combo)
        strip.addStretch()

        self.send_button = QtWidgets.QPushButton()
        self.send_button.setIcon(UIHelpers.get_icon("arrow_up", size=18, color="palette(highlighted-text)"))
        self.send_button.setObjectName("SendButton")
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.clicked.connect(self.on_send)
        strip.addWidget(self.send_button)

        inner.addLayout(strip)
        return card

    # ── Model / Mode helpers ───────────────────────────────────────────────────

    def _refresh_model_combo(self, models=None):
        """Rebuild model combo from ProviderManager; restore last-used selection."""
        if models is None:
            models = ProviderManager.instance().get_available_models()
        last_id = SettingsManager.instance().get_last_model()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not models:
            self.model_combo.addItem("No models — add API key", userData=None)
        else:
            for m in models:
                self.model_combo.addItem(m["display"], userData=m["id"])
        # Restore selection
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == last_id:
                self.model_combo.setCurrentIndex(i)
                break
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

    @QtCore.Slot(int)
    def update_header_title(self, index: int):
        info = self.chat_sessions.get_session_info(index)
        if info:
            self.title_label.setText(info["name"])

    @QtCore.Slot(str, int, int)
    def on_step_update(self, step_name: str, step_idx: int, total: int):
        self.status_line.setText(f"Step {step_idx}/{total}: {step_name}…")
        self.status_line.setVisible(True)

    def on_send(self):
        user_text = self.user_input.toPlainText().strip()
        if not user_text:
            return

        # Collect attached files (chip bar is source of truth)
        files = self.chip_bar.get_and_clear()
        # Also drain any pending from upload_widget's own list (legacy compat)
        files += self.upload_widget.get_and_clear_files()

        context_msg = f" [+{len(files)} file{'s' if len(files) != 1 else ''}]" if files else ""

        self.add_chat_widget(TextMessageWidget(f"You: {user_text}{context_msg}"))
        self.user_input.clear()



        self.current_stream_widget = AssistantMessageWidget("")
        self.add_chat_widget(self.current_stream_widget)

        self.send_button.setEnabled(False)
        self.status_line.setText("Preparing…")
        self.status_line.setVisible(True)
        self.request_work.emit(user_text)

    def on_token_received(self, token: str):
        if hasattr(self, "current_stream_widget") and self.current_stream_widget:
            self.current_stream_widget.append_text(token)

    def on_reasoning_received(self, token: str):
        self.status_line.setText(("Thinking: " + token.replace("\n", " "))[-90:])
        self.status_line.setVisible(True)

    def on_ai_response_received(self, response: dict):
        self.send_button.setEnabled(True)
        self.status_line.setVisible(False)

        if not response.get("text") and not response.get("reasoning"):
            self.current_stream_widget.text_label.setText("(the model returned nothing)")

    def on_ai_error(self, error_message: str):
        from utils.logger import logger
        logger.error(f"FreeCAD Copilot Error: {error_message}")
        self.send_button.setEnabled(True)
        self.status_line.setVisible(False)
        self.add_chat_widget(AssistantMessageWidget(error_message))

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
