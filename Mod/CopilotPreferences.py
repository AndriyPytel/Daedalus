from PySide import QtCore, QtGui, QtWidgets

try:
	import FreeCAD as App
	import FreeCADGui as Gui
except Exception:
	App = None
	Gui = None


class AddKeyDialog(QtWidgets.QDialog):
	"""Dialog to add an API key for a provider."""

	def __init__(self, parent=None):
		super().__init__(parent)
		self.setWindowTitle("Add API Key")
		self.setModal(True)

		layout = QtWidgets.QVBoxLayout(self)

		form = QtWidgets.QFormLayout()

		self.provider_combo = QtWidgets.QComboBox()
		self.provider_combo.addItems(["Google", "OpenAI", "Anthropic", "Custom/Local"])
		form.addRow("Provider:", self.provider_combo)

		self.key_edit = QtWidgets.QLineEdit()
		self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
		form.addRow("API Key:", self.key_edit)

		self.endpoint_edit = QtWidgets.QLineEdit()
		form.addRow("API Endpoint:", self.endpoint_edit)

		layout.addLayout(form)

		# Buttons
		buttons_layout = QtWidgets.QHBoxLayout()
		buttons_layout.addStretch()
		self.cancel_btn = QtWidgets.QPushButton("Cancel")
		self.save_btn = QtWidgets.QPushButton("Save")
		buttons_layout.addWidget(self.cancel_btn)
		buttons_layout.addWidget(self.save_btn)
		layout.addLayout(buttons_layout)

		self.endpoint_edit.setVisible(False)
		self._update_endpoint_visibility()
		self.provider_combo.currentIndexChanged.connect(self._update_endpoint_visibility)
		self.cancel_btn.clicked.connect(self.reject)
		self.save_btn.clicked.connect(self._on_save)

	def _update_endpoint_visibility(self):
		provider = self.provider_combo.currentText()
		self.endpoint_edit.setVisible(provider == "Custom/Local")

	def _on_save(self):
		provider = self.provider_combo.currentText()
		key = self.key_edit.text().strip()
		endpoint = self.endpoint_edit.text().strip() if provider == "Custom/Local" else ""
		if not provider or not key:
			QtWidgets.QMessageBox.warning(self, "Missing Data", "Provider and API Key are required.")
			return
			
		self._result = {
			"provider": provider,
			"key": key,
			"endpoint": endpoint or None,
		}
		self.accept()

	def get_result(self):
		return getattr(self, "_result", None)


class CopilotPreferences(QtWidgets.QWidget):
	"""Preference page for FreeCadCopilot, including API Key Manager and model selection."""

	MODEL_MAP = {
		"Google": ["models/gemini-2.5-pro-latest", "models/gemini-2.5-flash-latest"],
		"OpenAI": ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
		"Anthropic": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229"],
		# Custom/Local handled separately
	}

	DEFAULT_SYSTEM_PROMPT = (
		"You are a helpful FreeCAD assistant. To perform any action (create, modify, delete, query), "
		"you MUST call the execute_freecad_python tool with the complete, syntactically correct Python script."
	)

	def __init__(self, parent=None):
		super().__init__(parent)
		self.setObjectName("CopilotPreferencesPage")

		self.params = self._get_params()
		self._keys = []

		self._build_ui()
		self._load_from_params()
		self.update_available_models()

	def _get_params(self):
		if App is None:
			return None
		return App.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")

	def _build_ui(self):
		layout = QtWidgets.QVBoxLayout(self)
		layout.setContentsMargins(8, 8, 8, 8)

		# API Keys group
		keys_group = QtWidgets.QGroupBox("API Keys")
		keys_layout = QtWidgets.QVBoxLayout(keys_group)

		self.keys_table = QtWidgets.QTableWidget(0, 3)
		self.keys_table.setHorizontalHeaderLabels(["Provider", "Key", "Endpoint"])
		self.keys_table.horizontalHeader().setStretchLastSection(True)
		self.keys_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
		self.keys_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

		buttons_layout = QtWidgets.QHBoxLayout()
		self.add_key_btn = QtWidgets.QPushButton("Add Key…")
		self.remove_key_btn = QtWidgets.QPushButton("Remove Selected Key")
		buttons_layout.addWidget(self.add_key_btn)
		buttons_layout.addWidget(self.remove_key_btn)
		buttons_layout.addStretch()

		keys_layout.addWidget(self.keys_table)
		keys_layout.addLayout(buttons_layout)

		# Model and prompt group
		model_group = QtWidgets.QGroupBox("Model & Prompt")
		model_layout = QtWidgets.QFormLayout(model_group)

		self.model_combo = QtWidgets.QComboBox()
		self.system_prompt = QtWidgets.QTextEdit()
		self.system_prompt.setPlaceholderText("Global system prompt used for all conversations…")

		model_layout.addRow("Select Model:", self.model_combo)
		model_layout.addRow("Global System Prompt:", self.system_prompt)

		layout.addWidget(keys_group)
		layout.addWidget(model_group)
		layout.addStretch()

		# Connections
		self.add_key_btn.clicked.connect(self._on_add_key)
		self.remove_key_btn.clicked.connect(self._on_remove_key)
		self.keys_table.itemSelectionChanged.connect(self._on_keys_changed)

	def _on_add_key(self):
		dlg = AddKeyDialog(self)
		if dlg.exec_() == QtWidgets.QDialog.Accepted:
			entry = dlg.get_result()
			if entry:
				self._keys.append(entry)
				self._refresh_keys_table()
				self.update_available_models()

	def _on_remove_key(self):
		rows = sorted(set([i.row() for i in self.keys_table.selectedItems()]), reverse=True)
		for row in rows:
			if 0 <= row < len(self._keys):
				self._keys.pop(row)
				self.keys_table.removeRow(row)
		self.update_available_models()

	def _on_keys_changed(self):
		# No inline editing supported; handled via add/remove
		pass

	def _refresh_keys_table(self):
		self.keys_table.setRowCount(0)
		for entry in self._keys:
			row = self.keys_table.rowCount()
			self.keys_table.insertRow(row)
			provider_item = QtWidgets.QTableWidgetItem(entry.get("provider", ""))
			key_masked = "*****" + (entry.get("key", "")[-4:] if entry.get("key") else "")
			key_item = QtWidgets.QTableWidgetItem(key_masked)
			endpoint_item = QtWidgets.QTableWidgetItem(entry.get("endpoint") or "")
			for item in (provider_item, key_item, endpoint_item):
				item.setFlags(item.flags() ^ QtCore.Qt.ItemIsEditable)
			self.keys_table.setItem(row, 0, provider_item)
			self.keys_table.setItem(row, 1, key_item)
			self.keys_table.setItem(row, 2, endpoint_item)

	def update_available_models(self):
		providers = {e.get("provider") for e in self._keys if e.get("provider")}
		available_models = []
		for provider in sorted(providers):
			if provider in CopilotPreferences.MODEL_MAP:
				available_models.extend(CopilotPreferences.MODEL_MAP[provider])
			elif provider == "Custom/Local":
				# Add placeholder for custom/local
				available_models.append("Custom/Local Model")

		current = self.model_combo.currentText()
		self.model_combo.blockSignals(True)
		self.model_combo.clear()
		self.model_combo.addItems(available_models)
		# restore selection if still present
		if current in available_models:
			self.model_combo.setCurrentText(current)
		self.model_combo.blockSignals(False)

	def _load_from_params(self):
		if not self.params:
			return
		import json
		api_keys_json = self.params.GetString("ApiKeys", "")
		if api_keys_json:
			try:
				self._keys = json.loads(api_keys_json)
			except Exception:
				self._keys = []
		else:
			self._keys = []

		self._refresh_keys_table()

		selected_model = self.params.GetString("SelectedModel", "")
		self.update_available_models()
		if selected_model:
			index = self.model_combo.findText(selected_model)
			if index >= 0:
				self.model_combo.setCurrentIndex(index)

		prompt = self.params.GetString("GlobalSystemPrompt", CopilotPreferences.DEFAULT_SYSTEM_PROMPT)
		self.system_prompt.setPlainText(prompt)

	def save_to_params(self):
		if not self.params:
			return
		import json
		serialized = json.dumps(self._keys)
		self.params.SetString("ApiKeys", serialized)
		self.params.SetString("SelectedModel", self.model_combo.currentText())
		self.params.SetString("GlobalSystemPrompt", self.system_prompt.toPlainText())

	# FreeCAD calls apply() when OK/Apply pressed; also save on close as a fallback
	def apply(self):  # FreeCAD convention
		self.save_to_params()

	def reject(self):  # Optional: FreeCAD may call on cancel
		pass

	def closeEvent(self, event):
		try:
			self.save_to_params()
		except Exception:
			pass
		return super().closeEvent(event)


# Register preference page in FreeCAD GUI when available
if Gui is not None:
	try:
		Gui.addPreferencePage(CopilotPreferences(), "FreeCadCopilot")
	except Exception:
		# In headless or testing environments, ignore
		pass
