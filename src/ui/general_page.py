"""
GeneralPage – "General" preference sub-page.

One setting so far: whether a run goes unattended, stored under
`bridge.settings_manager.get_auto_approve_plan`. The checkbox gates two
things -- the Plan Gate, and the decision after a failed acceptance check --
under the one name, because the key is invisible to the user and a
migration buys nothing a label change does not already buy. More belongs
here as the addon grows settings that are neither a provider nor a key.
"""
try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide import QtGui as QtWidgets

from ui.ui_helpers import UIHelpers


class GeneralPage(QtWidgets.QWidget):
    """The 'General' preference sub-page."""

    def __init__(self, parent=None):
        super(GeneralPage, self).__init__(parent)
        self.setObjectName("CopilotGeneralPage")
        self.setWindowTitle("General")
        self._build_ui()

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        header = QtWidgets.QLabel("General")
        header.setStyleSheet(UIHelpers.get_header_style())
        outer.addWidget(header)

        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        outer.addWidget(divider)

        self.auto_approve_check = QtWidgets.QCheckBox(
            "Run unattended (approve the Plan, take the default revision)")
        self.auto_approve_check.setToolTip(
            "Runs without stopping to ask: the Plan Gate approves itself, "
            "including a Step that assumes a value nobody supplied, and a "
            "failed acceptance check is revised rather than asked about. "
            "Every auto-answer is still recorded on the Session so it can be "
            "reviewed afterwards. A run that keeps failing still stops once "
            "its revision budget runs out.")
        outer.addWidget(self.auto_approve_check)
        outer.addStretch(1)

    def getWidget(self):
        return self

    def loadSettings(self):
        from bridge import settings_manager
        self.auto_approve_check.setChecked(
            settings_manager.instance().get_auto_approve_plan())

    def saveSettings(self):
        from bridge import settings_manager
        settings_manager.instance().set_auto_approve_plan(
            self.auto_approve_check.isChecked())
