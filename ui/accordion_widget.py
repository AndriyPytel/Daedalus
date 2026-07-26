# FreeCadCopilot/ui/accordion_widget.py
# type: ignore  # PySide2 imported via FreeCAD shim – static analyser can't resolve it
"""
One collapsible row in the transcript.

Not a card. Thinking, tool calls and errors all take this shape: a toggle line
and, under it, wrapped text laid out in the column itself. What it used to be —
a QTextEdit inside the chat's own scroll area, animated to a magic 1000 — is
what truncated long thinking and what stole the mouse wheel from the chat (ADR-0011).
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
from utils.config_manager import ConfigManager


class AccordionWidget(QtWidgets.QWidget):
    """A toggle row with text that shows and hides beneath it."""

    def __init__(self, title: str = "Thinking..."):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 2, 24, 2)
        layout.setSpacing(0)

        self.toggle_button = QtWidgets.QPushButton("▶  " + title)
        self.toggle_button.setStyleSheet(
            "QPushButton { text-align: left; padding: 4px 0px; border: none; "
            "background: transparent; font-size: 12px; font-weight: 600; }")
        self.toggle_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggle_button.clicked.connect(self.toggle_content)
        layout.addWidget(self.toggle_button)

        self.content_area = QtWidgets.QLabel()
        self.content_area.setWordWrap(True)
        # A traceback says `<module>`, and QLabel guesses rich text by default.
        self.content_area.setTextFormat(QtCore.Qt.PlainText)
        self.content_area.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.content_area.setStyleSheet(
            "QLabel { font-family: %s; font-size: 11px; padding: 2px 0px 6px 14px; }"
            % ConfigManager.get_monospace_font())
        self.content_area.setVisible(False)
        layout.addWidget(self.content_area)

        self._is_expanded = False
        self._anim = None
        self._effect = None

    # ── Public API ────────────────────────────────────────────────────────────

    def append_text(self, text: str):
        # Straight concatenation: reasoning arrives as fragments and carries
        # its own newlines. A paragraph per call, as the old QTextEdit did,
        # broke a streamed sentence across a dozen lines.
        self.content_area.setText(self.content_area.text() + text)

    def set_title(self, title: str):
        self.toggle_button.setText(("▼  " if self._is_expanded else "▶  ") + title)

    def start_pulsing(self):
        """The 'still working' signal — the one animation worth keeping."""
        self._effect = QtWidgets.QGraphicsOpacityEffect(self.toggle_button)
        self.toggle_button.setGraphicsEffect(self._effect)
        self._anim = QtCore.QPropertyAnimation(self._effect, b"opacity")
        self._anim.setDuration(1400)
        self._anim.setKeyValueAt(0, 1.0)
        self._anim.setKeyValueAt(0.5, 0.45)
        self._anim.setKeyValueAt(1, 1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._anim.start()

    def stop_pulsing(self):
        if self._anim:
            self._anim.stop()
            self._anim = None
        if self._effect:
            self._effect.setOpacity(1.0)
            self.toggle_button.setGraphicsEffect(None)
            self._effect = None

    def toggle_content(self):
        self._is_expanded = not self._is_expanded
        self.content_area.setVisible(self._is_expanded)
        raw = self.toggle_button.text()
        self.toggle_button.setText(raw.replace("▶  ", "▼  ", 1) if self._is_expanded
                                   else raw.replace("▼  ", "▶  ", 1))
