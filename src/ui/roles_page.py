"""
RolesPage -- "Role -> Model" preference sub-page (FCC-312).

One row per role `core.llm.ROLES` names, seeded with
`core.llm.DEFAULT_ROLE_TABLE` (FCC-293's values) and editable here. A role's
Provider and Effort are picked from a fixed list; its Model is free text,
because the live model list a provider actually offers is fetched over the
network per API key (`utils.provider_manager`), and a role's model is set
once and rarely, not chosen from whatever happens to be configured this
session.
"""
try:
    from PySide2 import QtCore, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui as QtWidgets

from ui.ui_helpers import UIHelpers

_COLUMNS = ("Role", "Provider", "Model", "Effort")


class RolesPage(QtWidgets.QWidget):
    """The 'Role -> Model' preference sub-page."""

    def __init__(self, parent=None):
        super(RolesPage, self).__init__(parent)
        self.setObjectName("CopilotRolesPage")
        self.setWindowTitle("Role → Model")
        self._build_ui()

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        header = QtWidgets.QLabel("Role → Model")
        header.setStyleSheet(UIHelpers.get_header_style())
        outer.addWidget(header)

        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        outer.addWidget(divider)

        outer.addWidget(QtWidgets.QLabel(
            "Which provider, model and effort each role's next call uses. "
            "A role removed from this table falls back to the caller's own "
            "model, not a cheaper default."))

        self.table = QtWidgets.QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        outer.addWidget(self.table)

    def getWidget(self):
        return self

    def _providers(self):
        from copilot.sdk import providers
        return list(providers())

    def loadSettings(self):
        from utils import settings_manager
        from copilot.sdk import EFFORTS

        table = settings_manager.instance().get_role_table()
        self.table.setRowCount(len(table))
        providers = self._providers()
        for row_index, row in enumerate(table):
            role_item = QtWidgets.QTableWidgetItem(row.get("role", ""))
            role_item.setFlags(role_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(row_index, 0, role_item)

            provider_combo = QtWidgets.QComboBox()
            provider_combo.addItems(providers)
            current_provider = row.get("provider", "")
            if current_provider and provider_combo.findText(current_provider) < 0:
                provider_combo.addItem(current_provider)
            if current_provider:
                provider_combo.setCurrentText(current_provider)
            self.table.setCellWidget(row_index, 1, provider_combo)

            self.table.setItem(row_index, 2, QtWidgets.QTableWidgetItem(row.get("model", "")))

            effort_combo = QtWidgets.QComboBox()
            effort_combo.addItems(list(EFFORTS))
            current_effort = row.get("effort", "")
            if current_effort and effort_combo.findText(current_effort) >= 0:
                effort_combo.setCurrentText(current_effort)
            self.table.setCellWidget(row_index, 3, effort_combo)

        self.table.resizeColumnsToContents()

    def saveSettings(self):
        from utils import settings_manager

        table = []
        for row_index in range(self.table.rowCount()):
            role_item = self.table.item(row_index, 0)
            provider_combo = self.table.cellWidget(row_index, 1)
            model_item = self.table.item(row_index, 2)
            effort_combo = self.table.cellWidget(row_index, 3)
            table.append({
                "role": role_item.text() if role_item else "",
                "provider": provider_combo.currentText() if provider_combo else "",
                "model": model_item.text() if model_item else "",
                "effort": effort_combo.currentText() if effort_combo else "",
            })
        settings_manager.instance().set_role_table(table)
