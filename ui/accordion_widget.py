# FreeCadCopilot/ui/accordion_widget.py
# type: ignore  # PySide2 imported via FreeCAD shim – static analyser can't resolve it
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
from utils.config_manager import ConfigManager


class AccordionWidget(QtWidgets.QWidget):
    """
    Collapsible accordion for thinking chains / verbose logs.
    Antigravity aesthetic: beige toggle, blue left-accent, muted content area.
    """

    def _update_styles(self):
        """Re-resolve colors and apply to sub-widgets."""
        pal = self.palette()
        bg     = pal.color(QtGui.QPalette.Window).name()
        fg     = pal.color(QtGui.QPalette.Text).name()
        mid    = pal.color(QtGui.QPalette.Mid).name()
        high   = pal.color(QtGui.QPalette.Highlight).name()
        
        # Toggle button QSS
        current_border = high if self._anim or self._is_expanded else mid
        current_fg     = high if self._anim or self._is_expanded else fg
        
        self.toggle_button.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 8px 12px;
                font-size: 12px;
                font-weight: 600;
                color: {current_fg};
                background-color: {bg};
                border: none;
                border-left: 3px solid {current_border};
                border-radius: 0px;
            }}
            QPushButton:hover {{ background-color: palette(midlight); }}
        """)

        self.content_area.setStyleSheet(f"""
            QTextEdit {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {mid};
                border-top: none;
                border-radius: 0px 0px 4px 4px;
                padding: 8px 12px;
                font-family: {ConfigManager.get_monospace_font()};
                font-size: 11px;
            }}
        """)

    def __init__(self, title: str = "Thinking..."):
        super().__init__()
        self._main_layout = QtWidgets.QVBoxLayout()
        self._main_layout.setContentsMargins(12, 4, 12, 4)
        self._main_layout.setSpacing(0)
        self.setLayout(self._main_layout)

        self.toggle_button = QtWidgets.QPushButton("▶  " + title)
        self.toggle_button.clicked.connect(self.toggle_content)
        self._main_layout.addWidget(self.toggle_button)

        self.content_area = QtWidgets.QTextEdit()
        self.content_area.setReadOnly(True)
        # We start fully collapsed via maximumHeight = 0 and fluid size policy
        self.content_area.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.MinimumExpanding)
        self.content_area.setMinimumHeight(0)
        self.content_area.setMaximumHeight(0)
        self.content_area.setVisible(True) # Keep visible to let layout calculate, but constrained
        self._main_layout.addWidget(self.content_area)

        self._is_expanded = False
        self._target_height = 150
        self._anim   = None
        self._effect = None

        self._update_styles()

        # Height expansion animation
        self._height_anim = QtCore.QPropertyAnimation(self.content_area, b"maximumHeight")
        self._height_anim.setDuration(250)
        self._height_anim.setEasingCurve(QtCore.QEasingCurve.OutQuart)
        self._height_anim.finished.connect(self._on_anim_finished)

    def _on_anim_finished(self):
        if not self._is_expanded:
            self.content_area.setVisible(False)

    # ── Public API ────────────────────────────────────────────────────────────

    def append_text(self, text: str):
        self.content_area.append(text)

    def set_title(self, title: str):
        prefix = "▼  " if self._is_expanded else "▶  "
        self.toggle_button.setText(prefix + title)

    def start_pulsing(self):
        self._update_styles()
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
        self._update_styles()

    def toggle_content(self):
        self._is_expanded = not self._is_expanded
        
        # Animate height
        self._height_anim.stop()
        if self._is_expanded:
            self.content_area.setVisible(True)
            self._height_anim.setStartValue(self.content_area.height())
            self._height_anim.setEndValue(1000)  # Large enough to fit content
        else:
            self._height_anim.setStartValue(self.content_area.height())
            self._height_anim.setEndValue(0)
        self._height_anim.start()
        self._update_styles()

        if self.parentWidget() and self.parentWidget().layout():
            self.parentWidget().layout().activate()
        self.layout().activate()

        # Update text chevron
        raw = self.toggle_button.text()
        if self._is_expanded:
            self.toggle_button.setText(raw.replace("▶  ", "▼  ", 1))
        else:
            self.toggle_button.setText(raw.replace("▼  ", "▶  ", 1))
