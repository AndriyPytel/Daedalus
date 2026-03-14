# FreeCadCopilot/Mod/CopilotPreferences.py
import FreeCAD
import FreeCADGui
from PySide.QtGui import QWidget, QTableWidget, QPushButton, QDialog, QComboBox, QLineEdit, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView, QLabel, QTextEdit
import json
from ai_handlers.AgentFactory import MODEL_MAP


class CopilotPreferences(QWidget):
    def __init__(self):
        super().__init__()
        self.api_keys = []
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
        self.model_combo.currentIndexChanged.connect(self.save_model_selection)
        layout.addWidget(self.model_combo)

        # Global System Prompt
        system_prompt_label = QLabel("Global System Prompt")
        layout.addWidget(system_prompt_label)
        self.system_prompt_text = QTextEdit()
        self.system_prompt_text.setMaximumHeight(60)
        self.system_prompt_text.textChanged.connect(self.save_settings)
        layout.addWidget(self.system_prompt_text)

        # Agent Specific Prompts
        self.designer_prompt = self._add_prompt_field(layout, "Designer System Prompt (Override)")
        self.developer_prompt = self._add_prompt_field(layout, "Developer System Prompt (Override)")
        self.validator_prompt = self._add_prompt_field(layout, "Validator System Prompt (Override)")
        self.reviewer_prompt = self._add_prompt_field(layout, "Reviewer System Prompt (Override)")

        self.setLayout(layout)
        self.load_settings()

    def _add_prompt_field(self, layout, label_text):
        label = QLabel(label_text)
        layout.addWidget(label)
        text_edit = QTextEdit()
        text_edit.setMaximumHeight(60)
        text_edit.textChanged.connect(self.save_settings)
        layout.addWidget(text_edit)
        return text_edit

    def add_key(self):
        dialog = AddKeyDialog(self)
        if dialog.exec_():
            provider, api_key, api_endpoint = dialog.get_details()

            self.api_keys.append({"provider": provider, "api_key": api_key})

            row_position = self.table.rowCount()
            self.table.insertRow(row_position)
            self.table.setItem(row_position, 0, QTableWidgetItem(provider))
            self.table.setItem(row_position, 1, QTableWidgetItem("*" * len(api_key)))

            self.save_api_keys()
            self.update_model_selection()

    def remove_key(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            del self.api_keys[current_row]
            self.table.removeRow(current_row)
            self.save_api_keys()
            self.update_model_selection()

    def load_settings(self):
        param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
        api_keys_json = param_grp.GetString("ApiKeys", "[]")
        try:
            self.api_keys = json.loads(api_keys_json)
        except json.JSONDecodeError:
            self.api_keys = []

        self.table.setRowCount(0)
        for key_info in self.api_keys:
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)
            self.table.setItem(row_position, 0, QTableWidgetItem(key_info.get("provider", "")))
            self.table.setItem(row_position, 1, QTableWidgetItem("*" * len(key_info.get("api_key", ""))))

        self.system_prompt_text.setPlainText(param_grp.GetString("GlobalSystemPrompt", ""))
        self.orchestrator_prompt.setPlainText(param_grp.GetString("OrchestratorPrompt", ""))
        self.designer_prompt.setPlainText(param_grp.GetString("DesignerPrompt", ""))
        self.developer_prompt.setPlainText(param_grp.GetString("DeveloperPrompt", ""))
        self.validator_prompt.setPlainText(param_grp.GetString("ValidatorPrompt", ""))
        self.reviewer_prompt.setPlainText(param_grp.GetString("ReviewerPrompt", ""))
        
        self.update_model_selection()

        selected_model = param_grp.GetString("SelectedModel", "")
        if selected_model:
            index = self.model_combo.findText(selected_model)
            if index != -1:
                self.model_combo.setCurrentIndex(index)

    def save_settings(self):
        self.save_api_keys()
        self.save_model_selection()
        self.save_system_prompt()

    def save_api_keys(self):
        param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
        param_grp.SetString("ApiKeys", json.dumps(self.api_keys))

    def save_model_selection(self):
        param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
        param_grp.SetString("SelectedModel", self.model_combo.currentText())

    def save_system_prompt(self):
        param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
        param_grp.SetString("GlobalSystemPrompt", self.system_prompt_text.toPlainText())
        param_grp.SetString("OrchestratorPrompt", self.orchestrator_prompt.toPlainText())
        param_grp.SetString("DesignerPrompt", self.designer_prompt.toPlainText())
        param_grp.SetString("DeveloperPrompt", self.developer_prompt.toPlainText())
        param_grp.SetString("ValidatorPrompt", self.validator_prompt.toPlainText())
        param_grp.SetString("ReviewerPrompt", self.reviewer_prompt.toPlainText())

    def update_model_selection(self):
        self.model_combo.clear()
        
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
