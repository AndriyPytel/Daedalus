"""
Settings dialog for FreeCadCopilot
"""
# Import Qt from FreeCAD
import FreeCADGui as Gui

# FreeCAD provides Qt through FreeCADGui.Qt
QtWidgets = Gui.Qt.QtWidgets
QtCore = Gui.Qt.QtCore
QtGui = Gui.Qt.QtGui


class SettingsDialog(QtWidgets.QDialog):
    """Dialog for configuring AI model settings and system prompt."""
    
    def __init__(self, parent=None):
        """Initialize settings dialog.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("FreeCadCopilot Settings")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)
        
        # Main layout
        layout = QtWidgets.QVBoxLayout(self)
        
        # Form layout for settings
        form_layout = QtWidgets.QFormLayout()
        
        # Model handler combo box
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.addItem("Google Gemini")
        # Add more models here as needed
        self.model_combo.setToolTip("Select the AI model to use")
        form_layout.addRow("Model Handler:", self.model_combo)
        
        # API Key
        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Enter your API key")
        self.api_key_edit.setToolTip("Your API key (stored securely)")
        form_layout.addRow("API Key:", self.api_key_edit)
        
        # API Endpoint
        self.api_endpoint_edit = QtWidgets.QLineEdit()
        self.api_endpoint_edit.setPlaceholderText("https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent")
        self.api_endpoint_edit.setToolTip("API endpoint URL (for local LLMs or custom endpoints)")
        form_layout.addRow("API Endpoint:", self.api_endpoint_edit)
        
        # System Prompt
        system_prompt_label = QtWidgets.QLabel("Global System Prompt:")
        system_prompt_label.setAlignment(QtCore.Qt.AlignTop)
        
        self.system_prompt_edit = QtWidgets.QTextEdit()
        self.system_prompt_edit.setMinimumHeight(200)
        # Use monospace font for better code readability
        font = QtGui.QFont("Courier")
        font.setStyleHint(QtGui.QFont.Monospace)
        self.system_prompt_edit.setFont(font)
        
        default_prompt = """You are a helpful assistant for FreeCAD, a 3D parametric modeling CAD software.
When asked to create or modify 3D models, respond with Python code that can be executed in the FreeCAD console.

Guidelines:
- Use the FreeCAD Python API (App, Gui, Part, etc.)
- Each code block should be self-contained and executable
- Avoid creating duplicate objects
- Be concise and clear in your explanations
- If multiple steps are needed, provide them as separate code blocks"""
        
        self.system_prompt_edit.setPlainText(default_prompt)
        self.system_prompt_edit.setToolTip("The system prompt sent to the AI with every request")
        
        form_layout.addRow(system_prompt_label, self.system_prompt_edit)
        
        layout.addLayout(form_layout)
        
        # Button layout
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        
        # Save button
        self.save_button = QtWidgets.QPushButton("Save")
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 8px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)
        self.save_button.clicked.connect(self.accept)
        
        # Cancel button
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 8px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.save_button)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def get_settings(self):
        """Get the current settings as a dictionary.
        
        Returns:
            dict: Settings with keys: model_handler, api_key, api_endpoint, system_prompt
        """
        return {
            "model_handler": self.model_combo.currentText(),
            "api_key": self.api_key_edit.text(),
            "api_endpoint": self.api_endpoint_edit.text(),
            "system_prompt": self.system_prompt_edit.toPlainText()
        }
    
    def set_settings(self, settings):
        """Set the dialog fields from a settings dictionary.
        
        Args:
            settings: Dictionary with keys: model_handler, api_key, api_endpoint, system_prompt
        """
        if "model_handler" in settings:
            index = self.model_combo.findText(settings["model_handler"])
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        
        if "api_key" in settings:
            self.api_key_edit.setText(settings["api_key"])
        
        if "api_endpoint" in settings:
            self.api_endpoint_edit.setText(settings["api_endpoint"])
        
        if "system_prompt" in settings:
            self.system_prompt_edit.setPlainText(settings["system_prompt"])
