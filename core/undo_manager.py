# FreeCadCopilot/core/undo_manager.py

class UndoManager:
    def __init__(self):
        self.execution_stack = []

    def register_execution(self, widget):
        self.execution_stack.append(widget)
        self._update_buttons()

    def pop_last_execution(self):
        if self.execution_stack:
            self.execution_stack.pop()
        self._update_buttons()

    def _update_buttons(self):
        for widget in self.execution_stack:
            widget.revert_button.setEnabled(False)
        
        if self.execution_stack:
            self.execution_stack[-1].revert_button.setEnabled(True)
            self.execution_stack[-1].revert_button.setVisible(True)

undo_manager = UndoManager()
