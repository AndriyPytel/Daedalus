# FreeCadCopilot/Mod/pages/prompts_page.py
"""
PromptsPage – "Prompt Management" CRUD preference sub-page.

Two-panel layout:
  Left  – QListWidget of all prompts (factory marked with star SVG, user with person SVG)
  Right – Name + Text editor form with Save / Delete buttons

Factory prompts are read-only (Delete disabled).
Uses UIHelpers for premium styling and SVG icons.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.ui_helpers import UIHelpers


class PromptsPage(QtWidgets.QWidget):
    """The 'Prompt Management' preference sub-page."""

    def __init__(self, parent=None):
        super(PromptsPage, self).__init__(parent)
        self.setObjectName("CopilotPromptsPage")
        self.setWindowTitle("Prompt Management")
        self._current_key = None       # str | None
        self._current_is_factory = True
        self._tinted = False
        self._icon_widgets = []        # re-tinted on show, once the theme applies
        self._build_ui()

    def getWidget(self):
        return self

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _btn(self, label: str, icon_name: str = None, tooltip: str = "") -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(label)
        if icon_name:
            b.setIcon(UIHelpers.get_icon(icon_name, size=16, on=b))
            self._icon_widgets.append((b, icon_name))
        if tooltip:
            b.setToolTip(tooltip)
        b.setStyleSheet(
            "QPushButton { border-radius: 6px; padding: 6px 14px; font-weight: 500; }"
        )
        return b

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        # Header
        header = QtWidgets.QLabel("System Prompt Templates")
        header.setStyleSheet(UIHelpers.get_header_style())
        outer.addWidget(header)
        
        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        outer.addWidget(divider)

        # ── Splitter: left list + right editor ───────────────────────────────
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setHandleWidth(1)

        # — Left: prompt list —
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(4)

        self.prompt_list = QtWidgets.QListWidget()
        self.prompt_list.setStyleSheet(
            "QListWidget { border-radius: 6px; }"
            "QListWidget::item { padding: 8px 10px; font-weight: 500; }"
        )
        self.prompt_list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.prompt_list)

        new_btn = self._btn("New Prompt", icon_name="plus")
        new_btn.clicked.connect(self._new_prompt)
        left_layout.addWidget(new_btn)

        splitter.addWidget(left)

        # — Right: editor form —
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(8)

        right_layout.addWidget(QtWidgets.QLabel("Prompt Name"))
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Code Reviewer…")
        self.name_edit.setStyleSheet(UIHelpers.get_input_style())
        right_layout.addWidget(self.name_edit)

        right_layout.addWidget(QtWidgets.QLabel("System Prompt Instructions"))
        self.text_edit = QtWidgets.QTextEdit()
        self.text_edit.setPlaceholderText("Enter the system prompt instructions here…")
        self.text_edit.setStyleSheet(UIHelpers.get_input_style())
        right_layout.addWidget(self.text_edit, stretch=1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        self.save_btn = self._btn("Save")
        self.save_btn.clicked.connect(self._save_prompt)
        btn_row.addWidget(self.save_btn)

        self.delete_btn = self._btn("Delete", icon_name="trash")
        self.delete_btn.clicked.connect(self._delete_prompt)
        btn_row.addWidget(self.delete_btn)

        btn_row.addStretch()
        right_layout.addLayout(btn_row)

        # Read-only notice
        self.readonly_notice = QtWidgets.QWidget()
        notice_layout = QtWidgets.QHBoxLayout(self.readonly_notice)
        notice_layout.setContentsMargins(0, 4, 0, 0)
        notice_layout.setSpacing(6)
        
        notice_icon = QtWidgets.QLabel()
        self.notice_icon = notice_icon
        notice_icon.setPixmap(
            UIHelpers.get_icon("factory", size=16, on=notice_icon).pixmap(16, 16))
        notice_layout.addWidget(notice_icon)
        
        notice_text = QtWidgets.QLabel("Factory default — create a new prompt to customize")
        notice_text.setStyleSheet("font-size: 11px; font-style: italic;")
        notice_layout.addWidget(notice_text)
        notice_layout.addStretch()
        
        self.readonly_notice.setVisible(False)
        right_layout.addWidget(self.readonly_notice)

        splitter.addWidget(right)
        splitter.setSizes([220, 440])

        outer.addWidget(splitter, stretch=1)

        # ── Reset to defaults ─────────────────────────────────────────────────
        reset_row = QtWidgets.QHBoxLayout()
        reset_row.setContentsMargins(0, 4, 0, 0)
        reset_row.addStretch()
        self.reset_btn = self._btn("Reset to Defaults", icon_name="refresh",
                                    tooltip="Remove all user prompts and restore factory defaults")
        # Destructive, so it keeps an explicit colour, as failure does (ADR-0011).
        self.reset_btn.setStyleSheet(
            self.reset_btn.styleSheet()
            + "QPushButton { color: #f44336; }"
            + "QPushButton:hover { background: rgba(244, 67, 54, 0.1); }"
        )
        self.reset_btn.clicked.connect(self._reset_to_defaults)
        reset_row.addWidget(self.reset_btn)
        outer.addLayout(reset_row)

        # Initial blank state
        self._set_editor_enabled(False)

    def showEvent(self, event):
        """
        Re-tint once Qt has polished the page (ADR-0011).

        Only the first show: rebuilding the list is what re-tints its item
        icons, and doing that on every show would reset the selection and
        throw away a prompt the user had edited but not saved.
        """
        super().showEvent(event)
        if self._tinted:
            return
        self._tinted = True
        for widget, icon_name in self._icon_widgets:
            widget.setIcon(UIHelpers.get_icon(icon_name, size=16, on=widget))
        self.notice_icon.setPixmap(
            UIHelpers.get_icon("factory", size=16, on=self.notice_icon).pixmap(16, 16))
        self._populate_list()

    # ── List population ───────────────────────────────────────────────────────

    def _populate_list(self, select_key: str = None):
        from utils import prompt_manager
        self.prompt_list.blockSignals(True)
        self.prompt_list.clear()
        prompts = prompt_manager.instance().get_all_prompts()
        target_item = None
        for key, info in prompts.items():
            is_factory = info.get("is_factory", False)
            item = QtWidgets.QListWidgetItem(info["name"])
            item.setData(QtCore.Qt.UserRole, key)
            item.setData(QtCore.Qt.UserRole + 1, is_factory)
            
            # Use SVG icons for factory/user
            icon_name = "factory" if is_factory else "user"
            item.setIcon(UIHelpers.get_icon(icon_name, size=18, on=self.prompt_list))
            
            self.prompt_list.addItem(item)
            if key == select_key:
                target_item = item
        self.prompt_list.blockSignals(False)
        if target_item:
            self.prompt_list.setCurrentItem(target_item)
        elif self.prompt_list.count() > 0:
            self.prompt_list.setCurrentRow(0)

    # ── Selection changed ─────────────────────────────────────────────────────

    def _on_selection_changed(self, current: QtWidgets.QListWidgetItem, _prev):
        if current is None:
            self._set_editor_enabled(False)
            return
        key = current.data(QtCore.Qt.UserRole)
        is_factory = current.data(QtCore.Qt.UserRole + 1)
        self._current_key = key
        self._current_is_factory = is_factory

        from utils import prompt_manager
        prompt = prompt_manager.instance().get_prompt(key)
        if prompt:
            self.name_edit.setText(prompt.get("name", ""))
            self.text_edit.setPlainText(prompt.get("text", ""))

        self._set_editor_enabled(True)
        editable = not is_factory
        self.name_edit.setReadOnly(not editable)
        self.text_edit.setReadOnly(not editable)
        self.save_btn.setEnabled(editable)
        self.delete_btn.setEnabled(editable)
        self.readonly_notice.setVisible(is_factory)

    # ── CRUD actions ──────────────────────────────────────────────────────────

    def _new_prompt(self):
        self.prompt_list.clearSelection()
        self._current_key     = ""
        self._current_is_factory = False
        self.name_edit.setText("")
        self.text_edit.setPlainText("")
        self._set_editor_enabled(True)
        self.name_edit.setReadOnly(False)
        self.text_edit.setReadOnly(False)
        self.save_btn.setEnabled(True)
        self.delete_btn.setEnabled(False)
        self.readonly_notice.setVisible(False)
        self.name_edit.setFocus()

    def _save_prompt(self):
        name = self.name_edit.text().strip()
        text = self.text_edit.toPlainText().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Missing Name", "Please enter a name for the prompt.")
            return
        from utils import prompt_manager
        prompt_manager.instance().save_user_prompt(self._current_key or "", name, text)
        new_key = self._current_key or name.lower().replace(" ", "_")[:40]
        self._populate_list(select_key=new_key)

    def _delete_prompt(self):
        if not self._current_key or self._current_is_factory:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Prompt",
            f"Delete '{self.name_edit.text()}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            from utils import prompt_manager
            prompt_manager.instance().delete_user_prompt(self._current_key)
            self._populate_list()
            self._set_editor_enabled(False)

    def _reset_to_defaults(self):
        reply = QtWidgets.QMessageBox.question(
            self, "Reset to Defaults",
            "This will permanently delete all your custom prompts.\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            from utils import prompt_manager
            prompt_manager.instance().reset_to_defaults()
            self._populate_list()
            self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled: bool):
        self.name_edit.setEnabled(enabled)
        self.text_edit.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)
        if not enabled:
            self.name_edit.clear()
            self.text_edit.clear()
            self.readonly_notice.setVisible(False)

    # ── FreeCAD Preferences API ───────────────────────────────────────────────

    def loadSettings(self):
        self._populate_list()

    def saveSettings(self):
        # CRUD ops auto-save via PromptManager → SettingsManager
        pass
