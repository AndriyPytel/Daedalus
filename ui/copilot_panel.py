# FreeCadCopilot/ui/copilot_panel.py
import FreeCAD
import json
from PySide.QtGui import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QListWidget, QListWidgetItem, QTabWidget
)
from PySide.QtCore import QThread, Signal, QObject, Slot
from .chat_widgets import TextMessageWidget, CodeBlockWidget
from ai_handlers.AgentFactory import create_agent_executor

# The worker that will run in a separate thread
class AiWorker(QObject):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, agent_executor):
        super(AiWorker, self).__init__()
        self.agent_executor = agent_executor

    @Slot(str)
    def do_work(self, user_input):
        try:
            response = self.agent_executor.invoke({"messages": [{"role": "user", "content": user_input}]})
            self.finished.emit(response)
        except Exception as e:
            self.error.emit(str(e))

class CopilotPanel(QDockWidget):
    # Signal to request work from the worker thread
    request_work = Signal(str)

    def __init__(self):
        super(CopilotPanel, self).__init__("FreeCAD Copilot")
        self.setWidget(QWidget())
        self.layout = QVBoxLayout()
        self.widget().setLayout(self.layout)

        # Chat history management
        self.chat_tabs = QTabWidget()
        self.chat_tabs.setTabsClosable(True)
        self.chat_tabs.tabCloseRequested.connect(self.close_chat_tab)
        self.layout.addWidget(self.chat_tabs)

        # Create and start the worker thread
        self.agent_executor = create_agent_executor()
        self.thread = QThread()
        self.worker = AiWorker(self.agent_executor)
        self.worker.moveToThread(self.thread)

        # Connect signals and slots
        self.request_work.connect(self.worker.do_work)
        self.worker.finished.connect(self.on_ai_response_received)
        self.worker.error.connect(self.on_ai_error)

        self.thread.start()

        # Input and control buttons
        self.user_input = QTextEdit()
        self.user_input.setMaximumHeight(100)
        self.layout.addWidget(self.user_input)

        button_layout = QHBoxLayout()
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.on_send)
        button_layout.addWidget(self.send_button)

        self.new_chat_button = QPushButton("New Chat")
        self.new_chat_button.clicked.connect(self.new_chat)
        button_layout.addWidget(self.new_chat_button)
        self.layout.addLayout(button_layout)

        self.new_chat() # Start with an initial chat

    def new_chat(self):
        chat_history = QListWidget()
        tab_index = self.chat_tabs.addTab(chat_history, f"Chat {self.chat_tabs.count() + 1}")
        self.chat_tabs.setCurrentIndex(tab_index)

    def close_chat_tab(self, index):
        if self.chat_tabs.count() > 1:
            widget = self.chat_tabs.widget(index)
            self.chat_tabs.removeTab(index)
            widget.deleteLater()
        else:
            # Clear the last chat tab instead of closing it
            self.chat_tabs.widget(index).clear()

    def get_current_chat_history(self):
        return self.chat_tabs.currentWidget()

    def on_send(self):
        user_text = self.user_input.toPlainText().strip()
        if not user_text:
            return

        chat_history = self.get_current_chat_history()
        if not chat_history:
            # This should not happen if there is always at least one tab
            return

        self.add_chat_widget(TextMessageWidget(f"You: {user_text}"))
        self.user_input.clear()

        # Emit signal to start AI processing in worker thread
        self.request_work.emit(user_text)

    def on_ai_response_received(self, response):
        print("----------RAW RESPONSE----------")
        print(response)
        print("---------------------------------")

        # The response format has changed. The AI message is now inside a list of messages.
        # The new structure is like: {'messages': [HumanMessage, AIMessage]}
        # The AIMessage content can be a string or a list of content blocks.

        ai_message = None
        if 'messages' in response and response['messages']:
            # AIMessage is typically the last one in the list
            message = response['messages'][-1]
            # Check if it's an AIMessage (or has 'response_metadata' which is typical for AIMessage)
            if hasattr(message, 'response_metadata'):
                 ai_message = message

        if ai_message:
            if isinstance(ai_message.content, str):
                self.add_chat_widget(TextMessageWidget(f"AI: {ai_message.content}"))
            elif isinstance(ai_message.content, list):
                for content_block in ai_message.content:
                    if content_block.get('type') == 'text':
                        self.add_chat_widget(TextMessageWidget(f"AI: {content_block.get('text', '')}"))
                    # Here we could handle other content types like tool calls if they appear in content
        elif 'output' in response:
            # Fallback to the old response structure
            self.add_chat_widget(TextMessageWidget(f"AI: {response['output']}"))
        else:
            self.on_ai_error("Unknown AI response format")

        # Handle tool calls, which might still be in 'intermediate_steps' for agent responses
        for tool_call in response.get("intermediate_steps", []):
            # tool_call is a tuple (action, observation)
            action = tool_call[0]
            if hasattr(action, 'tool_input') and isinstance(action.tool_input, dict) and 'script' in action.tool_input:
                script = action.tool_input['script']
                self.add_chat_widget(CodeBlockWidget(script))

    def on_ai_error(self, error_message):
        FreeCAD.Console.PrintMessage(f"FreeCAD Copilot Error: {error_message}\\n")
        self.add_chat_widget(TextMessageWidget(f"Error: {error_message}"))

    def add_chat_widget(self, widget):
        chat_history = self.get_current_chat_history()
        if not chat_history:
            return
        item = QListWidgetItem(chat_history)
        item.setSizeHint(widget.sizeHint())
        chat_history.addItem(item)
        chat_history.setItemWidget(item, widget)
        chat_history.scrollToBottom()

    def closeEvent(self, event):
        # Clean up the thread when the widget is closed
        self.thread.quit()
        self.thread.wait()
        super(CopilotPanel, self).closeEvent(event)
