# FreeCadCopilot/Mod/CopilotPreferences.py
import FreeCAD
import FreeCADGui
from PySide.QtGui import QWidget, QTableWidget, QPushButton, QDialog, QComboBox, QLineEdit, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView, QLabel, QTextEdit
import json

class CopilotPreferences(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()
        
        # API Key Manager
        api_key_label = QLabel("API Key Manager")
        layout.addWidget(api_key_label)
        
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Provider", "API Key"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)
        
        button_layout = QHBoxLayout()
        add_key_button = QPushButton("Add Key...")
        add_key_button.clicked.connect(self.add_key)
        button_layout.addWidget(add_key_button)
        
        remove_key_button = QPushButton("Remove Selected Key")
        remove_key_button.clicked.connect(self.remove_key)
        button_layout.addWidget(remove_key_button)
        layout.addLayout(button_layout)
        
        # Model Selection
        model_selection_label = QLabel("Model Selection")
        layout.addWidget(model_selection_label)
        
        self.model_combo = QComboBox()
        layout.addWidget(self.model_combo)
        
        # Global System Prompt
        system_prompt_label = QLabel("Global System Prompt")
        layout.addWidget(system_prompt_label)
        
        self.system_prompt_text = QTextEdit()
        layout.addWidget(self.system_prompt_text)
        
        self.setLayout(layout)
        self.load_settings()

    def add_key(self):
        dialog = AddKeyDialog(self)
        if dialog.exec_():
            provider, api_key, api_endpoint = dialog.get_details()
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)
            self.table.setItem(row_position, 0, QTableWidgetItem(provider))
            self.table.setItem(row_position, 1, QTableWidgetItem("*" * len(api_key)))
            self.save_settings()
            self.update_model_selection()

    def remove_key(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            self.table.removeRow(current_row)
            self.save_settings()
            self.update_model_selection()

    def load_settings(self):
        param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
        api_keys_json = param_grp.GetString("ApiKeys", "[]")
        api_keys = json.loads(api_keys_json)
        
        self.table.setRowCount(0)
        for key_info in api_keys:
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)
            self.table.setItem(row_position, 0, QTableWidgetItem(key_info["provider"]))
            self.table.setItem(row_position, 1, QTableWidgetItem("*" * len(key_info["api_key"])))
            
        self.system_prompt_text.setPlainText(param_grp.GetString("GlobalSystemPrompt", ""))
        self.update_model_selection()
        
        selected_model = param_grp.GetString("SelectedModel", "")
        if selected_model:
            index = self.model_combo.findText(selected_model)
            if index != -1:
                self.model_combo.setCurrentIndex(index)

    def save_settings(self):
        param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
        api_keys = []
        for row in range(self.table.rowCount()):
            provider = self.table.item(row, 0).text()
            # This is a simplification, in a real scenario you would need to store the actual key securely
            api_keys.append({"provider": provider, "api_key": "dummy_key"})
        param_grp.SetString("ApiKeys", json.dumps(api_keys))
        param_grp.SetString("SelectedModel", self.model_combo.currentText())
        param_grp.SetString("GlobalSystemPrompt", self.system_prompt_text.toPlainText())

    def update_model_selection(self):
        self.model_combo.clear()
        MODEL_MAP = {
            "Google": ["models/gemini-1.5-pro-latest"],
            "OpenAI": ["gpt-4o", "gpt-4", "gpt-3.5-turbo"],
            "Anthropic": ["claude-3-opus-20240229", "claude-3-sonnet-20240229"],
        }
        
        providers = set()
        for row in range(self.table.rowCount()):
            providers.add(self.table.item(row, 0).text())
            
        for provider in providers:
            if provider in MODEL_MAP:
                self.model_combo.addItems(MODEL_MAP[provider])
            elif provider == "Custom/Local":
                self.model_combo.addItem("Custom/Local Model")

class AddKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add API Key")
        layout = QVBoxLayout()
        
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["Google", "OpenAI", "Anthropic", "Custom/Local"])
        self.provider_combo.currentTextChanged.connect(self.toggle_endpoint)
        layout.addWidget(self.provider_combo)
        
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.api_key_input)
        
        self.api_endpoint_input = QLineEdit()
        self.api_endpoint_input.setPlaceholderText("API Endpoint (only for Custom/Local)")
        self.api_endpoint_input.setVisible(False)
        layout.addWidget(self.api_endpoint_input)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        button_layout.addWidget(ok_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)

    def toggle_endpoint(self, text):
        self.api_endpoint_input.setVisible(text == "Custom/Local")

    def get_details(self):
        return self.provider_combo.currentText(), self.api_key_input.text(), self.api_endpoint_input.text()

# Corrected: Pass the class itself, not an instance.
FreeCADGui.addPreferencePage(CopilotPreferences, "FreeCadCopilot")
