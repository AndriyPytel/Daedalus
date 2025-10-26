"""
Main copilot panel (QDockWidget)
"""
from PySide2.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QListWidget, QListWidgetItem, QDialog
)
from PySide2.QtCore import Qt
from PySide2.QtGui import QFont

from .chat_widgets import TextMessage, CodeBlockWidget
from .settings_dialog import SettingsDialog


class CopilotPanel(QDockWidget):
    """Main dockable panel for FreeCadCopilot."""
    
    def __init__(self, parent=None):
        """Initialize the copilot panel.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("FreeCad Copilot")
        self.setAllowedAreas(Qt.AllDockWidgetAreas)
        
        # Create main widget
        main_widget = QWidget(self)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Status bar at top
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(5, 0, 5, 0)
        
        self.status_label = QLabel("Status: Offline")
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: #dc3545;
                color: white;
                padding: 5px 10px;
                border-radius: 3px;
                font-weight: bold;
            }
        """)
        
        # Settings button
        self.settings_button = QPushButton("⚙ Settings")
        self.settings_button.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 5px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        self.settings_button.clicked.connect(self._open_settings)
        
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        status_layout.addWidget(self.settings_button)
        
        # Chat history (using QListWidget for dynamic widget insertion)
        self.chat_list = QListWidget()
        self.chat_list.setAlternatingRowColors(False)
        self.chat_list.setSelectionMode(QListWidget.NoSelection)
        self.chat_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #cccccc;
                border-radius: 3px;
                background-color: white;
            }
            QListWidget::item {
                border: none;
                padding: 2px;
            }
        """)
        
        # Input area
        input_layout = QHBoxLayout()
        
        self.user_input = QTextEdit()
        self.user_input.setMaximumHeight(60)
        self.user_input.setPlaceholderText("Type your command here... (Press Ctrl+Enter to send)")
        # Handle Ctrl+Enter to send
        self.user_input.keyPressEvent = self._handle_key_press
        
        self.send_button = QPushButton("Send")
        self.send_button.setMinimumWidth(80)
        self.send_button.setStyleSheet("""
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 8px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
            QPushButton:disabled {
                background-color: #6c757d;
            }
        """)
        self.send_button.clicked.connect(self._on_send_clicked)
        
        input_layout.addWidget(self.user_input)
        input_layout.addWidget(self.send_button)
        
        # Assemble layout
        layout.addLayout(status_layout)
        layout.addWidget(self.chat_list)
        layout.addLayout(input_layout)
        
        main_widget.setLayout(layout)
        self.setWidget(main_widget)
        
        # Welcome message
        self._add_message("Welcome to FreeCad Copilot! Configure settings and start chatting.", is_user=False)
    
    def _handle_key_press(self, event):
        """Handle key press events in user input.
        
        Args:
            event: QKeyEvent
        """
        # Call original keyPressEvent
        QTextEdit.keyPressEvent(self.user_input, event)
        
        # Send on Ctrl+Enter
        if event.key() == Qt.Key_Return and (event.modifiers() & Qt.ControlModifier):
            self._on_send_clicked()
    
    def _on_send_clicked(self):
        """Handle send button click."""
        text = self.user_input.toPlainText().strip()
        if not text:
            return
        
        # Add user message
        self._add_message(text, is_user=True)
        
        # Clear input
        self.user_input.clear()
        
        # TODO: Send to AI handler
        print(f"User message: {text}")
        
        # Mock AI response for testing
        self._mock_ai_response()
    
    def _mock_ai_response(self):
        """Mock AI response for testing the UI."""
        self._add_message("I understand. Here's how we can create a box:", is_user=False)
        
        # Add a mock code block
        code1 = """import Part
box = Part.makeBox(10, 10, 10)
Part.show(box)"""
        self._add_code_block(code1)
        
        self._add_message("After creating the box, we can move it:", is_user=False)
        
        code2 = """box = App.ActiveDocument.getObject("Box")
box.Placement.Base = App.Vector(5, 5, 5)
App.ActiveDocument.recompute()"""
        self._add_code_block(code2)
    
    def _add_message(self, text, is_user=False):
        """Add a text message to chat.
        
        Args:
            text: Message text
            is_user: Whether this is from the user
        """
        widget = TextMessage(text, is_user=is_user)
        
        list_item = QListWidgetItem()
        list_item.setSizeHint(widget.sizeHint())
        self.chat_list.addItem(list_item)
        self.chat_list.setItemWidget(list_item, widget)
        
        # Scroll to bottom
        self.chat_list.scrollToBottom()
    
    def _add_code_block(self, code):
        """Add a code block widget to chat.
        
        Args:
            code: Python code string
        """
        widget = CodeBlockWidget(code)
        
        # Connect signals
        widget.execute_requested.connect(lambda c=code: self._on_execute_code(c, widget))
        widget.revert_requested.connect(lambda: self._on_revert_code(widget))
        
        list_item = QListWidgetItem()
        list_item.setSizeHint(widget.sizeHint())
        self.chat_list.addItem(list_item)
        self.chat_list.setItemWidget(list_item, widget)
        
        # Scroll to bottom
        self.chat_list.scrollToBottom()
    
    def _on_execute_code(self, code, widget):
        """Handle code execution request.
        
        Args:
            code: Python code to execute
            widget: The code block widget
        """
        print(f"Executing code: {code}")
        # TODO: Implement actual execution in command_executor.py
        
        # For now, just show revert button
        widget.show_revert_button()
    
    def _on_revert_code(self, widget):
        """Handle code revert request.
        
        Args:
            widget: The code block widget
        """
        print("Reverting code")
        # TODO: Implement actual revert using undo_manager.py
        
        # For now, just show execute button
        widget.show_execute_button()
    
    def _open_settings(self):
        """Open the settings dialog."""
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            settings = dialog.get_settings()
            print(f"Settings saved: {settings}")
            # TODO: Save settings and update status
