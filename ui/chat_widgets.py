# FreeCadCopilot/ui/chat_widgets.py
from PySide.QtGui import QWidget, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QLabel
from core.command_executor import execute, revert
from core.undo_manager import undo_manager

class TextMessageWidget(QWidget):
    def __init__(self, text):
        super().__init__()
        layout = QVBoxLayout()
        layout.addWidget(QLabel(text))
        self.setLayout(layout)

class CodeBlockWidget(QWidget):
    def __init__(self, script):
        super().__init__()
        self.script = script
        
        layout = QVBoxLayout()
        
        self.code_edit = QTextEdit()
        self.code_edit.setPlainText(script)
        self.code_edit.setReadOnly(True)
        layout.addWidget(self.code_edit)
        
        button_layout = QHBoxLayout()
        self.execute_button = QPushButton("Execute")
        self.execute_button.clicked.connect(self.execute_script)
        button_layout.addWidget(self.execute_button)
        
        self.revert_button = QPushButton("Revert")
        self.revert_button.clicked.connect(self.revert_script)
        self.revert_button.setVisible(False)
        button_layout.addWidget(self.revert_button)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)

    def execute_script(self):
        result = execute(self.script)
        if result is True:
            undo_manager.register_execution(self)
        else:
            # Handle error
            pass

    def revert_script(self):
        revert()
        undo_manager.pop_last_execution()
