# FreeCadCopilot/ui/task_list_widget.py
from PySide.QtGui import QWidget, QVBoxLayout, QLabel, QHBoxLayout
from PySide.QtCore import Qt

class TaskListWidget(QWidget):
    """
    Displays the current progress of the Orchestrator's execution plan.
    """
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout()
        self.layout.setAlignment(Qt.AlignTop)
        
        self.header = QLabel("<b>Execution Plan</b>")
        self.layout.addWidget(self.header)
        
        self.task_container = QVBoxLayout()
        self.layout.addLayout(self.task_container)
        self.setLayout(self.layout)
        self.tasks = {}

    def set_tasks(self, task_list: list):
        """Initializes the view with a list of tasks. e.g., ['RAG', 'Plan', 'Code']"""
        self.clear_tasks()
        for i, task_name in enumerate(task_list):
            row = QHBoxLayout()
            icon_label = QLabel("⏳")  
            name_label = QLabel(task_name)
            
            row.addWidget(icon_label)
            row.addWidget(name_label)
            row.addStretch()
            
            self.task_container.addLayout(row)
            self.tasks[task_name] = {"icon": icon_label, "name": name_label}

    def update_task_status(self, task_name: str, status: str):
        """status: 'pending', 'in_progress', 'done', 'failed'"""
        if task_name not in self.tasks:
            return
            
        icon_label = self.tasks[task_name]["icon"]
        if status == "in_progress":
            icon_label.setText("🔄")
        elif status == "done":
            icon_label.setText("✅")
        elif status == "failed":
            icon_label.setText("❌")
        else:
            icon_label.setText("⏳")

    def clear_tasks(self):
        while self.task_container.count():
            child = self.task_container.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                while child.layout().count():
                    sub_child = child.layout().takeAt(0)
                    if sub_child.widget():
                        sub_child.widget().deleteLater()
        self.tasks.clear()
