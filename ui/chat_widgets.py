try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
from utils.config_manager import ConfigManager


class TextMessageWidget(QtWidgets.QWidget):
    """A chat bubble for user text."""

    def __init__(self, text: str = ""):
        super().__init__()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(12, 6, 36, 6)

        self.text_label = QtWidgets.QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        
        # User message: with bubble (border/background)
        self.text_label.setStyleSheet("""
            QLabel {
                background-color: palette(button);
                color: palette(buttontext);
                border: 1px solid palette(mid);
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 13px;
                line-height: 1.5;
            }
        """)

        layout.addWidget(self.text_label)
        self.setLayout(layout)

    def get_text(self):
        return self.text_label.text()

    def set_text(self, text: str):
        self.text_label.setText(text)

    def append_text(self, text: str):
        self.text_label.setText(self.text_label.text() + text)


class AssistantMessageWidget(QtWidgets.QWidget):
    """Plain text layout for assistant responses (no bubble)."""

    def __init__(self, text: str = ""):
        super().__init__()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(12, 8, 36, 8)

        self.text_label = QtWidgets.QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        
        # Assistant message: No bubble, just clean typography
        self.text_label.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: palette(text);
                padding: 4px 6px;
                font-size: 13px;
                line-height: 1.6;
            }
        """)

        layout.addWidget(self.text_label)
        self.setLayout(layout)

    def append_text(self, text: str):
        self.text_label.setText(self.text_label.text() + text)


class CodeBlockWidget(QtWidgets.QWidget):
    """
    Displays the code a Step ran.

    Read-only: nothing here executes. A Step reaches the Document through the
    apply pipeline after the Plan Gate, never from a button in the transcript.
    """

    def __init__(self, script: str):
        super().__init__()
        self.script = script

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(12, 6, 36, 6)
        layout.setSpacing(6)

        # ── Code display ──────────────────────────────────────────────────────
        self.code_edit = QtWidgets.QTextEdit()
        self.code_edit.setPlainText(script)
        self.code_edit.setReadOnly(True)
        # Fetch QPalette.Base and make it effectively a bit darker for the block
        # Since we cannot use palette().color() reliably in pure QSS without python logic,
        # we resolve the current default palette here.
        base_color = self.palette().color(QtGui.QPalette.Base).darker(110).name()
        
        self.code_edit.setStyleSheet(f"""
            QTextEdit {{
                font-family: {ConfigManager.get_monospace_font()};
                font-size: 12px;
                background-color: {base_color};
                color: palette(text);
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 10px;
            }}
        """)
        layout.addWidget(self.code_edit)
        self.setLayout(layout)
