# FreeCadCopilot/Mod/pages/providers_page.py
"""
ProvidersPage – "API & Providers" preference sub-page.

Two-panel layout:
  Left  – QListWidget of LLM providers (OpenAI, Google, Anthropic, etc.)
  Right – API Key editor for the selected provider.

Uses SVG icons for visibility toggle and status indicators.
Dynamic provider management allows for future extension.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.ui_helpers import UIHelpers

# Carried alongside the semantic colours, which replace rather than extend a stylesheet.
_STATUS_STYLE = "font-size: 11px; font-weight: 600;"


# ── Connection-test worker ────────────────────────────────────────────────────

class _TestWorker(QtCore.QObject):
    result = QtCore.Signal(bool, str)   # success, message

    def __init__(self, provider, api_key):
        super(_TestWorker, self).__init__()
        self._provider = provider
        self._api_key  = api_key

    def run(self):
        from utils import provider_manager
        ok, msg = provider_manager.instance().test_connection(self._provider, self._api_key)
        self.result.emit(ok, msg)


# ── Main page ─────────────────────────────────────────────────────────────────

class ProvidersPage(QtWidgets.QWidget):
    """The 'API & Providers' preference sub-page."""

    def __init__(self, parent=None):
        super(ProvidersPage, self).__init__(parent)
        self.setObjectName("CopilotProvidersPage")
        self.setWindowTitle("API & Providers")
        self._current_provider = None
        self._edits = {}  # Tracks all unsaved key changes locally
        self._thread = None
        self._worker = None
        self._tinted = False
        self._build_ui()

        from utils import provider_manager
        # Bound method, not a lambda: Qt drops it when this page is destroyed.
        provider_manager.instance().models_changed.connect(self._on_models_changed)

    def getWidget(self):
        return self

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        header = QtWidgets.QLabel("LLM Providers & API Keys")
        header.setStyleSheet(UIHelpers.get_header_style())
        outer.addWidget(header)
        
        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        outer.addWidget(divider)

        # ── Splitter ─────────────────────────────────────────────────────────
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setHandleWidth(1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(4)

        self.provider_list = QtWidgets.QListWidget()
        self.provider_list.setStyleSheet(
            "QListWidget { border-radius: 6px; }"
            "QListWidget::item { padding: 8px 12px; font-weight: 500; }"
        )
        self.provider_list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.provider_list)

        self.add_btn = QtWidgets.QPushButton("Add Custom Provider")
        self.add_btn.setIcon(UIHelpers.get_icon("plus", size=16, on=self.add_btn))
        self.add_btn.setStyleSheet(
            "QPushButton { border-radius: 6px; padding: 6px; }"
        )
        self.add_btn.clicked.connect(self._add_custom_provider)
        left_layout.addWidget(self.add_btn)

        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        self.right_layout = QtWidgets.QVBoxLayout(right)
        self.right_layout.setContentsMargins(12, 0, 0, 0)
        self.right_layout.setSpacing(12)

        self.title_lbl = QtWidgets.QLabel("Select a provider")
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.right_layout.addWidget(self.title_lbl)

        self.key_container = QtWidgets.QWidget()
        key_vbox = QtWidgets.QVBoxLayout(self.key_container)
        key_vbox.setContentsMargins(0, 0, 0, 0)
        key_vbox.setSpacing(6)

        key_vbox.addWidget(QtWidgets.QLabel("API Key"))
        
        key_hbox = QtWidgets.QHBoxLayout()
        key_hbox.setSpacing(6)
        
        self.key_edit = QtWidgets.QLineEdit()
        self.key_edit.setPlaceholderText("Enter API key…")
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.key_edit.setStyleSheet(UIHelpers.get_input_style())
        key_hbox.addWidget(self.key_edit, stretch=1)

        self.visibility_btn = QtWidgets.QPushButton()
        self.visibility_btn.setFixedSize(32, 32)
        self.visibility_btn.setCheckable(True)
        self.visibility_btn.setToolTip("Show/Hide Key")
        self._update_visibility_icon(False)
        self.visibility_btn.setStyleSheet(
            "QPushButton { background: transparent; border-radius: 6px; }"
        )
        self.visibility_btn.toggled.connect(self._toggle_visibility)
        key_hbox.addWidget(self.visibility_btn)
        
        key_vbox.addLayout(key_hbox)
        self.right_layout.addWidget(self.key_container)

        test_row = QtWidgets.QHBoxLayout()
        self.test_btn = QtWidgets.QPushButton("Test Connection")
        self.test_btn.setIcon(UIHelpers.get_icon("refresh", size=16, on=self.test_btn))
        self.test_btn.setStyleSheet(
            "QPushButton { border-radius: 6px; padding: 6px 16px; }"
        )
        self.test_btn.clicked.connect(self._run_test)
        test_row.addWidget(self.test_btn)

        self.status_lbl = QtWidgets.QLabel("")
        self.status_lbl.setStyleSheet(_STATUS_STYLE)
        test_row.addWidget(self.status_lbl, stretch=1)
        
        self.right_layout.addLayout(test_row)

        self.model_container = QtWidgets.QWidget()
        model_vbox = QtWidgets.QVBoxLayout(self.model_container)
        model_vbox.setContentsMargins(0, 0, 0, 0)
        model_vbox.setSpacing(6)

        model_vbox.addWidget(QtWidgets.QLabel("Default Model"))
        self.default_model_combo = QtWidgets.QComboBox()
        self.default_model_combo.setStyleSheet(UIHelpers.get_input_style())
        model_vbox.addWidget(self.default_model_combo)

        model_vbox.addWidget(QtWidgets.QLabel("Session Naming Model"))
        self.naming_model_combo = QtWidgets.QComboBox()
        self.naming_model_combo.setStyleSheet(UIHelpers.get_input_style())
        self.naming_model_combo.setToolTip(
            "Names a Session from its first message. Unset uses the chat model.")
        model_vbox.addWidget(self.naming_model_combo)

        self.right_layout.addWidget(self.model_container)

        self.right_layout.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([200, 400])
        outer.addWidget(splitter, stretch=1)

        self._set_editor_enabled(False)

    def showEvent(self, event):
        """
        Re-tint once Qt has polished the page (ADR-0011).

        Only the first show: rebuilding the list is what re-tints its item
        icons, and doing that on every show would reset the selection and
        throw away a key the user had typed but not saved.
        """
        super().showEvent(event)
        if self._tinted:
            return
        self._tinted = True
        self.add_btn.setIcon(UIHelpers.get_icon("plus", size=16, on=self.add_btn))
        self.test_btn.setIcon(UIHelpers.get_icon("refresh", size=16, on=self.test_btn))
        self._update_visibility_icon(self.visibility_btn.isChecked())
        self._populate_list()

    def _update_visibility_icon(self, visible):
        icon_name = "eye_open" if visible else "eye_closed"
        self.visibility_btn.setIcon(UIHelpers.get_icon(icon_name, size=20, on=self.visibility_btn))

    def _toggle_visibility(self, checked):
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.Normal if checked else QtWidgets.QLineEdit.Password)
        self._update_visibility_icon(checked)

    def _set_editor_enabled(self, enabled):
        self.title_lbl.setEnabled(enabled)
        self.key_container.setEnabled(enabled)
        self.test_btn.setEnabled(enabled)
        self.model_container.setEnabled(enabled)
        if not enabled:
            self.title_lbl.setText("Select a provider")
            self.key_edit.clear()
            self.status_lbl.setText("")
            self.default_model_combo.clear()

    def _on_selection_changed(self, current, _prev):
        if not current:
            self._set_editor_enabled(False)
            return

        if self._current_provider:
            self._edits[self._current_provider] = self.key_edit.text().strip()
        
        provider = current.data(QtCore.Qt.UserRole)
        self._current_provider = provider
        self.title_lbl.setText(provider)
        
        if provider in self._edits:
            key = self._edits[provider]
        else:
            from utils import settings_manager
            key = settings_manager.instance().get_api_key_for_provider(provider)
        
        self.key_edit.setText(key)
        self.status_lbl.setText("")
        
        self._set_editor_enabled(True)
        self._refresh_models()

    def _on_key_changed(self):
        pass

    def _on_models_changed(self, _models):
        self._refresh_models()

    def _refresh_models(self):
        if not self._current_provider:
            return
        
        from utils import provider_manager
        from utils import settings_manager
        
        self.default_model_combo.clear()
        models = provider_manager.instance().get_models_for_provider(self._current_provider)
        for m in models:
            self.default_model_combo.addItem(m['display'], userData=m['id'])
            
        current_default = settings_manager.instance().get_default_model()
        for i in range(self.default_model_combo.count()):
            if self.default_model_combo.itemData(i) == current_default:
                self.default_model_combo.setCurrentIndex(i)
                break

        self.naming_model_combo.clear()
        self.naming_model_combo.addItem("Same as the chat model", userData="")
        for m in models:
            self.naming_model_combo.addItem(m['display'], userData=m['id'])
        current_naming = settings_manager.instance().get_naming_model()
        for i in range(self.naming_model_combo.count()):
            if self.naming_model_combo.itemData(i) == current_naming:
                self.naming_model_combo.setCurrentIndex(i)
                break

    def _run_test(self):
        key = self.key_edit.text().strip()
        if not key:
            self.status_lbl.setText("No key")
            self.status_lbl.setStyleSheet(_STATUS_STYLE)
            return

        self.status_lbl.setText("Testing…")
        self.status_lbl.setStyleSheet(_STATUS_STYLE)
        self.test_btn.setEnabled(False)

        self._worker = _TestWorker(self._current_provider, key)
        self._thread = QtCore.QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.result.connect(self._on_test_result)
        self._worker.result.connect(self._thread.quit)
        self._thread.start()

    def _on_test_result(self, ok, msg):
        self.test_btn.setEnabled(True)
        if ok:
            self.status_lbl.setText("✓ Success")
            self.status_lbl.setStyleSheet(_STATUS_STYLE + "color: #4caf50;")
        else:
            self.status_lbl.setText(f"✗ Error: {msg[:40]}…")
            self.status_lbl.setStyleSheet(_STATUS_STYLE + "color: #f44336;")

    def _add_custom_provider(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Provider", "Provider Name:")
        if ok and name:
            item = QtWidgets.QListWidgetItem(name)
            item.setData(QtCore.Qt.UserRole, name)
            item.setIcon(UIHelpers.get_icon("user", size=18, on=self.provider_list))
            self.provider_list.addItem(item)
            self.provider_list.setCurrentItem(item)

    def _populate_list(self):
        from utils import provider_manager
        from utils import settings_manager
        
        self.provider_list.clear()
        
        all_supported = provider_manager.instance().get_all_providers()
        saved_keys = settings_manager.instance().get_api_keys()
        
        seen = set()
        for p in all_supported:
            seen.add(p)
            item = QtWidgets.QListWidgetItem(p)
            item.setData(QtCore.Qt.UserRole, p)
            icon = "factory" if p != "Custom/Local" else "user"
            item.setIcon(UIHelpers.get_icon(icon, size=18, on=self.provider_list))
            self.provider_list.addItem(item)
            
        for key_info in saved_keys:
            p = key_info["provider"]
            if p not in seen:
                seen.add(p)
                item = QtWidgets.QListWidgetItem(p)
                item.setData(QtCore.Qt.UserRole, p)
                item.setIcon(UIHelpers.get_icon("user", size=18, on=self.provider_list))
                self.provider_list.addItem(item)
                
        if self.provider_list.count() > 0:
            self.provider_list.setCurrentRow(0)

    # ── FreeCAD Preferences API ───────────────────────────────────────────────

    def loadSettings(self):
        self._populate_list()

    def saveSettings(self):
        from utils import settings_manager
        sm = settings_manager.instance()
        
        if self._current_provider:
            self._edits[self._current_provider] = self.key_edit.text().strip()

        if not self._edits:
            self._save_models(sm)
            return

        current_keys = sm.get_api_keys()
        for provider, key in self._edits.items():
            existing = next((e for e in current_keys if e["provider"] == provider), None)
            if key:
                if existing:
                    existing["api_key"] = key
                else:
                    current_keys.append({"provider": provider, "api_key": key})
            else:
                if existing:
                    current_keys.remove(existing)
        
        sm.set_api_keys(current_keys)
        self._edits.clear()

        self._save_models(sm)

    def _save_models(self, sm):
        mid = self.default_model_combo.currentData()
        if mid:
            sm.set_default_model(mid)
        # "" is the "Same as the chat model" row, which is a choice and has to be
        # writable; None only means the combo has nothing selected at all.
        naming = self.naming_model_combo.currentData()
        if naming is not None:
            sm.set_naming_model(naming)
