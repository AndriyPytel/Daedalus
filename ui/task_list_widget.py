# FreeCadCopilot/ui/task_list_widget.py
"""
TaskListWidget – stub.

The "Execution Layer" block has been removed from the UI.
A single StatusLine label in copilot_panel.py now shows agent progress.
This stub is kept so any external import does not break.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui


class TaskListWidget(QtWidgets.QWidget):
    """No-op stub. All functionality migrated to the StatusLine in copilot_panel."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(0)  # invisible, takes no space
        self.setLayout(QtWidgets.QVBoxLayout())
        self.tasks: dict = {}

    def set_tasks(self, task_list: list):            pass
    def update_task_status(self, name: str, s: str): pass
    def clear_tasks(self):                           pass
