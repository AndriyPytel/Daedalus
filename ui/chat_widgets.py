"""
Custom widgets for chat display
"""
from PySide2.QtWidgets import (
    QFrame, QTextEdit, QPushButton, QVBoxLayout, QHBoxLayout, QLabel
)
from PySide2.QtCore import Signal, Qt
from PySide2.QtGui import QFont


class TextMessage(QFrame):
    """Widget for displaying plain text messages."""
    
    def __init__(self, text, is_user=False, parent=None):
        """Initialize text message widget.
        
        Args:
            text: The message text
            is_user: Whether this is a user message (True) or AI message (False)
            parent: Parent widget
        """
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        
        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Create label for text
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.MarkdownText)
        
        # Set alignment based on sender
        if is_user:
            self.label.setAlignment(Qt.AlignRight)
            self.label.setStyleSheet("""
                background-color: #007bff;
                color: white;
                border-radius: 10px;
                padding: 8px;
            """)
        else:
            self.label.setAlignment(Qt.AlignLeft)
            self.label.setStyleSheet("""
                background-color: #f0f0f0;
                color: black;
                border-radius: 10px;
                padding: 8px;
            """)
        
        layout.addWidget(self.label)
        self.setLayout(layout)


class CodeBlockWidget(QFrame):
    """Widget for displaying and executing a code block."""
    
    # Signal emitted when execute button is clicked
    execute_requested = Signal(str)
    # Signal emitted when revert button is clicked
    revert_requested = Signal()
    
    def __init__(self, code_text, parent=None):
        """Initialize code block widget.
        
        Args:
            code_text: The Python code to display
            parent: Parent widget
        """
        super().__init__(parent)
        self.code_text = code_text
        self.setFrameStyle(QFrame.Box)
        self.setStyleSheet("""
            CodeBlockWidget {
                border: 2px solid #007bff;
                border-radius: 5px;
                background-color: #f8f9fa;
                margin: 5px;
            }
        """)
        
        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Create code display (read-only)
        self.code_display = QTextEdit()
        self.code_display.setPlainText(code_text)
        self.code_display.setReadOnly(True)
        self.code_display.setMaximumHeight(150)
        
        # Use monospace font
        font = QFont("Courier")
        font.setStyleHint(QFont.Monospace)
        self.code_display.setFont(font)
        
        # Create button container
        button_layout = QHBoxLayout()
        
        # Execute button (initially visible)
        self.execute_button = QPushButton("Execute")
        self.execute_button.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 5px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        self.execute_button.clicked.connect(self._on_execute_clicked)
        
        # Revert button (initially hidden)
        self.revert_button = QPushButton("Revert")
        self.revert_button.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 5px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        self.revert_button.clicked.connect(self._on_revert_clicked)
        self.revert_button.hide()
        
        button_layout.addStretch()
        button_layout.addWidget(self.execute_button)
        button_layout.addWidget(self.revert_button)
        
        layout.addWidget(self.code_display)
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def _on_execute_clicked(self):
        """Handle execute button click."""
        self.execute_requested.emit(self.code_text)
    
    def _on_revert_clicked(self):
        """Handle revert button click."""
        self.revert_requested.emit()
    
    def show_revert_button(self):
        """Show revert button and hide execute button (after execution)."""
        self.execute_button.hide()
        self.revert_button.show()
    
    def show_execute_button(self):
        """Show execute button and hide revert button (after revert)."""
        self.revert_button.hide()
        self.execute_button.show()
    
    def set_revert_enabled(self, enabled):
        """Enable or disable the revert button.
        
        Args:
            enabled: Whether the revert button should be enabled
        """
        self.revert_button.setEnabled(enabled)
