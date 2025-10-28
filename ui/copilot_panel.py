"""
Main copilot panel (QDockWidget)
"""
from PySide import QtCore, QtGui, QtWidgets

from .chat_widgets import TextMessage, CodeBlockWidget

from ai_handlers.AgentFactory import create_agent_executor
from core.command_executor import CommandExecutor
from core.undo_manager import UndoManager


class _AgentWorker(QtCore.QThread):
	finished = QtCore.Signal(dict)
	error = QtCore.Signal(str)

	def __init__(self, user_prompt, parent=None):
		super().__init__(parent)
		self.user_prompt = user_prompt

	def run(self):
		try:
			executor = create_agent_executor()
			response = executor.invoke({"input": self.user_prompt})
			# Ensure dict
			if not isinstance(response, dict):
				response = {"output": str(response)}
			self.finished.emit(response)
		except Exception as e:
			self.error.emit(str(e))


class CopilotPanel(QtWidgets.QDockWidget):
	"""Main dockable panel for FreeCadCopilot."""
	
	def __init__(self, parent=None):
		"""Initialize the copilot panel.
		
		Args:
			parent: Parent widget
		"""
		super().__init__(parent)
		self.setWindowTitle("FreeCad Copilot")
		self.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
		self._worker = None
		self._executor = CommandExecutor()
		self._undo_manager = UndoManager()
		
		# Create main widget
		main_widget = QtWidgets.QWidget(self)
		layout = QtWidgets.QVBoxLayout(main_widget)
		layout.setContentsMargins(5, 5, 5, 5)
		
		# Status bar at top
		status_layout = QtWidgets.QHBoxLayout()
		status_layout.setContentsMargins(5, 0, 5, 0)
		
		self.status_label = QtWidgets.QLabel("Status: Idle")
		self.status_label.setStyleSheet("""
			QLabel {
				background-color: #6c757d;
				color: white;
				padding: 5px 10px;
				border-radius: 3px;
				font-weight: bold;
			}
		""")
		
		# Settings button (opens FreeCAD Preferences)
		self.settings_button = QtWidgets.QPushButton("⚙ Preferences")
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
		self.chat_list = QtWidgets.QListWidget()
		self.chat_list.setAlternatingRowColors(False)
		self.chat_list.setSelectionMode(QtWidgets.QListWidget.NoSelection)
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
		input_layout = QtWidgets.QHBoxLayout()
		
		self.user_input = QtWidgets.QTextEdit()
		self.user_input.setMaximumHeight(60)
		self.user_input.setPlaceholderText("Type your command here... (Press Ctrl+Enter to send)")
		# Handle Ctrl+Enter to send
		self.user_input.keyPressEvent = self._handle_key_press
		
		self.send_button = QtWidgets.QPushButton("Send")
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
		self._add_message("Welcome to FreeCad Copilot! Configure preferences and start chatting.", is_user=False)
	
	def _handle_key_press(self, event):
		"""Handle key press events in user input.
		
		Args:
			event: QKeyEvent
		"""
		# Call original keyPressEvent
		QtWidgets.QTextEdit.keyPressEvent(self.user_input, event)
		
		# Send on Ctrl+Enter
		if event.key() == QtCore.Qt.Key_Return and (event.modifiers() & QtCore.Qt.ControlModifier):
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
		
		# Start background worker
		self.send_button.setDisabled(True)
		self.status_label.setText("Status: Thinking…")
		self.status_label.setStyleSheet("""
			QLabel { background-color: #17a2b8; color: white; padding: 5px 10px; border-radius: 3px; font-weight: bold; }
		""")
		self._worker = _AgentWorker(text, self)
		self._worker.finished.connect(self._on_ai_response_received)
		self._worker.error.connect(self._on_ai_error)
		self._worker.finished.connect(lambda _: self._on_worker_done())
		self._worker.error.connect(lambda _: self._on_worker_done())
		self._worker.start()
	
	def _on_worker_done(self):
		self.send_button.setDisabled(False)
		self.status_label.setText("Status: Idle")
		self.status_label.setStyleSheet("""
			QLabel { background-color: #6c757d; color: white; padding: 5px 10px; border-radius: 3px; font-weight: bold; }
		""")
		self._worker = None
	
	def _on_ai_error(self, message: str):
		self._add_message(f"Error: {message}", is_user=False)
	
	def _on_ai_response_received(self, response: dict):
		"""Handle structured agent response.
		
		Flow: show text output, then list proposed tool calls as executable code blocks.
		"""
		text_response = response.get("output", "An error occurred.")
		self._add_message(text_response, is_user=False)
		
		steps = response.get("intermediate_steps", []) or []
		for step in steps:
			try:
				tool_call = step[0] if isinstance(step, (list, tuple)) and step else step
				tool_name = getattr(tool_call, "tool", None) or getattr(tool_call, "name", None) or tool_call.get("tool") if isinstance(tool_call, dict) else None
				if tool_name != "execute_freecad_python":
					continue
				# Extract args
				args = None
				if hasattr(tool_call, "tool_input"):
					args = getattr(tool_call, "tool_input")
				elif hasattr(tool_call, "args"):
					args = getattr(tool_call, "args")
				elif isinstance(tool_call, dict):
					args = tool_call.get("args") or tool_call.get("tool_input")
				script_to_run = None
				if isinstance(args, dict):
					script_to_run = args.get("script")
				elif isinstance(args, str):
					# Sometimes tool_input could be raw string if schema not enforced
					script_to_run = args
				if not script_to_run:
					continue
				self._add_code_block(script_to_run)
			except Exception as e:
				self._add_message(f"Warning: failed to parse tool call: {e}", is_user=False)
	
	def _add_message(self, text, is_user=False):
		"""Add a text message to chat.
		
		Args:
			text: Message text
			is_user: Whether this is from the user
		"""
		widget = TextMessage(text, is_user=is_user)
		
		list_item = QtWidgets.QListWidgetItem()
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
		
		list_item = QtWidgets.QListWidgetItem()
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
		success, message, error = self._executor.execute(code)
		if success:
			widget.show_revert_button()
			self._undo_manager.push(widget)
			self._add_message(message, is_user=False)
		else:
			self._add_message(message, is_user=False)
	
	def _on_revert_code(self, widget):
		"""Handle code revert request.
		
		Args:
			widget: The code block widget
		"""
		success, message = self._executor.revert()
		if success:
			popped = self._undo_manager.pop()
			widget.show_execute_button()
			self._add_message(message, is_user=False)
		else:
			self._add_message(message, is_user=False)
	
	def _open_settings(self):
		"""Open FreeCAD Preferences page for Copilot."""
		try:
			import FreeCADGui as Gui
			Gui.showPreferences("FreeCadCopilot")
		except Exception:
			QtWidgets.QMessageBox.information(self, "Preferences", "Open Edit -> Preferences -> FreeCadCopilot to configure.")
