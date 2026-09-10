"""
RolesPage -- "Role -> Model" preference sub-page (FCC-312).

One row per role `core.llm.ROLES` names, seeded with
`core.llm.DEFAULT_ROLE_TABLE` (FCC-293's values) and editable here. A role's
Provider and Effort are picked from a fixed list; its Model is picked from
what that provider offers, and stays typable, because that list is fetched
over the network per API key (`bridge.provider_manager`) and a role may name
a model this session has no key to reach -- a seeded row, or a provider whose
fetch has not answered yet. `models_changed` is what refills the lists when
it does.
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

        from bridge import provider_manager
        provider_manager.instance().models_changed.connect(self._on_models_changed)

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

    @staticmethod
    def _fill_models(combo, provider, model):
        """Offer *provider*'s models, keeping *model* whether it is one of them or not."""
        from bridge import provider_manager

        combo.clear()
        combo.addItems([entry["id"] for entry
                        in provider_manager.instance().get_models_for_provider(provider)])
        combo.setCurrentText(model)

    def _on_models_changed(self, _models):
        for row_index in range(self.table.rowCount()):
            provider_combo = self.table.cellWidget(row_index, 1)
            model_combo = self.table.cellWidget(row_index, 2)
            if provider_combo and model_combo:
                self._fill_models(model_combo, provider_combo.currentText(),
                                  model_combo.currentText())

    def loadSettings(self):
        from bridge import settings_manager
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

            model_combo = QtWidgets.QComboBox()
            model_combo.setEditable(True)
            self._fill_models(model_combo, current_provider, row.get("model", ""))
            provider_combo.currentTextChanged.connect(
                lambda name, combo=model_combo: self._fill_models(combo, name, combo.currentText()))
            self.table.setCellWidget(row_index, 2, model_combo)

            effort_combo = QtWidgets.QComboBox()
            effort_combo.addItems(list(EFFORTS))
            current_effort = row.get("effort", "")
            if current_effort and effort_combo.findText(current_effort) >= 0:
                effort_combo.setCurrentText(current_effort)
            self.table.setCellWidget(row_index, 3, effort_combo)

        self.table.resizeColumnsToContents()

    def saveSettings(self):
        from bridge import settings_manager

        table = []
        for row_index in range(self.table.rowCount()):
            role_item = self.table.item(row_index, 0)
            provider_combo = self.table.cellWidget(row_index, 1)
            model_combo = self.table.cellWidget(row_index, 2)
            effort_combo = self.table.cellWidget(row_index, 3)
            table.append({
                "role": role_item.text() if role_item else "",
                "provider": provider_combo.currentText() if provider_combo else "",
                "model": model_combo.currentText().strip() if model_combo else "",
                "effort": effort_combo.currentText() if effort_combo else "",
            })
        settings_manager.instance().set_role_table(table)
