# FreeCadCopilot/ui/copilot_panel.py
from PySide.QtGui import QDockWidget, QWidget, QVBoxLayout, QTextEdit, QPushButton, QListWidget, QListWidgetItem
from PySide.QtCore import QThread, Signal
from .chat_widgets import TextMessageWidget, CodeBlockWidget
from ai_handlers.AgentFactory import create_agent_executor

class Worker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, user_input):
        super().__init__()
        self.user_input = user_input

    def run(self):
        try:
            agent_executor = create_agent_executor()
            response = agent_executor.invoke({"input": self.user_input})
            self.finished.emit(response)
        except Exception as e:
            self.error.emit(str(e))

class CopilotPanel(QDockWidget):
    def __init__(self):
        super().__init__("FreeCAD Copilot")
        self.setWidget(QWidget())
        self.layout = QVBoxLayout()
        self.widget().setLayout(self.layout)

        self.chat_history = QListWidget()
        self.layout.addWidget(self.chat_history)

        self.user_input = QTextEdit()
        self.user_input.setMaximumHeight(100)
        self.layout.addWidget(self.user_input)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.on_send)
        self.layout.addWidget(self.send_button)

    def on_send(self):
        user_text = self.user_input.toPlainText()
        if not user_text:
            return

        self.add_chat_widget(TextMessageWidget(f"You: {user_text}"))
        self.user_input.clear()

        self.worker = Worker(user_text)
        self.worker.finished.connect(self.on_ai_response_received)
        self.worker.error.connect(self.on_ai_error)
        self.worker.start()

    def on_ai_response_received(self, response):
        self.add_chat_widget(TextMessageWidget(f"AI: {response['output']}"))
        for tool_call in response.get("intermediate_steps", []):
            # Assuming the tool call is for 'execute_freecad_python'
            script = tool_call[0].tool_input['script']
            self.add_chat_widget(CodeBlockWidget(script))

    def on_ai_error(self, error_message):
        self.add_chat_widget(TextMessageWidget(f"Error: {error_message}"))

    def add_chat_widget(self, widget):
        item = QListWidgetItem(self.chat_history)
        item.setSizeHint(widget.sizeHint())
        self.chat_history.addItem(item)
        self.chat_history.setItemWidget(item, widget)
