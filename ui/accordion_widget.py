# FreeCadCopilot/ui/accordion_widget.py
from PySide.QtGui import QWidget, QVBoxLayout, QPushButton, QTextEdit
from PySide.QtCore import Qt

class AccordionWidget(QWidget):
    """
    A simple collapsible accordion widget for displaying thinking chains or verbose logs.
    """
    def __init__(self, title="Thinking..."):
        super().__init__()
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.layout)

        self.toggle_button = QPushButton(title)
        self.toggle_button.setStyleSheet("text-align: left; padding: 5px; font-weight: bold;")
        self.toggle_button.clicked.connect(self.toggle_content)
        self.layout.addWidget(self.toggle_button)

        self.content_area = QTextEdit()
        self.content_area.setReadOnly(True)
        self.content_area.setMaximumHeight(150)
        self.content_area.setVisible(False)
        self.layout.addWidget(self.content_area)

    def append_text(self, text):
        self.content_area.append(text)

    def toggle_content(self):
        is_visible = self.content_area.isVisible()
        self.content_area.setVisible(not is_visible)
        if is_visible:
             self.toggle_button.setText(self.toggle_button.text().replace("▼", "▶") if "▼" in self.toggle_button.text() else "▶ " + self.toggle_button.text().replace("▶ ", ""))
        else:
             self.toggle_button.setText(self.toggle_button.text().replace("▶", "▼") if "▶" in self.toggle_button.text() else "▼ " + self.toggle_button.text().replace("▼ ", ""))
