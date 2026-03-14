# FreeCadCopilot/ui/copilot_panel.py
import FreeCAD
from PySide.QtGui import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QListWidgetItem
)
from PySide.QtCore import QThread, Signal, QObject, Slot

from .chat_widgets import TextMessageWidget, CodeBlockWidget
from .multi_tab_manager import MultiTabManager
from .upload_widget import UploadWidget
from .task_list_widget import TaskListWidget
from .accordion_widget import AccordionWidget
from agents.system_controller import MultiAgentController

class AiWorker(QObject):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, controller):
        super(AiWorker, self).__init__()
        self.controller = controller

    @Slot(str)
    def do_work(self, user_input):
        try:
            # The MultiAgentController processes the async request
            response = self.controller.process_request(user_input)
            self.finished.emit(response)
        except Exception as e:
            self.error.emit(str(e))

class CopilotPanel(QDockWidget):
    request_work = Signal(str)

    def __init__(self):
        super().__init__("FreeCAD Copilot")
        self.setWidget(QWidget())
        self.layout = QVBoxLayout()
        self.widget().setLayout(self.layout)

        # 1. Task List Widget
        self.task_list = TaskListWidget()
        self.layout.addWidget(self.task_list)

        # 2. Multi-Tab Manager
        self.chat_tabs = MultiTabManager()
        self.layout.addWidget(self.chat_tabs)

        # Worker Thread Setup
        self.controller = MultiAgentController()
        self.thread = QThread()
        self.worker = AiWorker(self.controller)
        self.worker.moveToThread(self.thread)

        self.request_work.connect(self.worker.do_work)
        self.worker.finished.connect(self.on_ai_response_received)
        self.worker.error.connect(self.on_ai_error)

        self.thread.start()

        # 3. File Upload Widget
        self.upload_widget = UploadWidget()
        self.layout.addWidget(self.upload_widget)

        # 4. Input Area
        self.user_input = QTextEdit()
        self.user_input.setMaximumHeight(80)
        self.layout.addWidget(self.user_input)

        button_layout = QHBoxLayout()
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.on_send)
        button_layout.addWidget(self.send_button)

        self.new_chat_button = QPushButton("New Chat")
        self.new_chat_button.clicked.connect(self.new_chat)
        button_layout.addWidget(self.new_chat_button)
        self.layout.addLayout(button_layout)

        self.new_chat()

    def new_chat(self):
        self.chat_tabs.add_new_chat(f"Chat {self.chat_tabs.count() + 1}")
        # Reset overall tasks on fresh chat
        self.task_list.set_tasks([])

    def on_send(self):
        user_text = self.user_input.toPlainText().strip()
        if not user_text:
            return

        # Attached files pass context
        files = self.upload_widget.get_and_clear_files()
        context_msg = ""
        if files:
            context_msg = f" [Attached {len(files)} files]"

        self.add_chat_widget(TextMessageWidget(f"You: {user_text}{context_msg}"))
        self.user_input.clear()
        
        # Setup UI for standard execution pipeline steps
        self.task_list.set_tasks(["Orchestrating", "Designing", "Developing", "Validating", "Reviewing"])

        self.request_work.emit(user_text)

    def on_ai_response_received(self, response: dict):
        if "error" in response:
            self.on_ai_error(response["error"])
            self.task_list.update_task_status("Execution", "failed")
            return

        # Accordion showing Chain of Thought / Plan
        accordion = AccordionWidget("View Planner Strategy")
        accordion.append_text(response.get("strategy", "No specialized strategy output."))
        self.add_chat_widget(accordion)
        
        # Display the Code
        gen_code = response.get("generated_code")
        if gen_code:
            self.add_chat_widget(CodeBlockWidget(gen_code))
        else:
             self.add_chat_widget(TextMessageWidget(response.get("orchestrator_output", "Finished task.")))

        # Update success states in UI
        self.task_list.update_task_status("Orchestrating", "done")
        self.task_list.update_task_status("Designing", "done")
        self.task_list.update_task_status("Developing", "done")
        self.task_list.update_task_status("Validating", "done")
        self.task_list.update_task_status("Reviewing", "done")

    def on_ai_error(self, error_message):
        FreeCAD.Console.PrintMessage(f"FreeCAD Copilot Error: {error_message}\\n")
        self.add_chat_widget(TextMessageWidget(f"🚨 Error: {error_message}"))

    def add_chat_widget(self, widget):
        chat_history = self.chat_tabs.get_current_chat_history()
        if not chat_history:
            return
        item = QListWidgetItem(chat_history)
        item.setSizeHint(widget.sizeHint())
        chat_history.addItem(item)
        chat_history.setItemWidget(item, widget)
        chat_history.scrollToBottom()

    def closeEvent(self, event):
        self.thread.quit()
        self.thread.wait()
        super().closeEvent(event)
