# FreeCadCopilot/ui/multi_tab_manager.py
from PySide.QtGui import QTabWidget, QListWidget, QWidget, QVBoxLayout

class MultiTabManager(QTabWidget):
    """
    Manages independent conversational threads for the multi-agent copilot.
    Each tab runs its own state machine asynchronously.
    """
    def __init__(self):
        super().__init__()
        self.setTabsClosable(True)
        self.tabCloseRequested.connect(self.close_tab)
        # Holds state internally for each tab index
        self._states = {}
        
    def add_new_chat(self, tab_name: str = "New Chat"):
        chat_container = QWidget()
        layout = QVBoxLayout()
        chat_history = QListWidget()
        layout.addWidget(chat_history)
        chat_container.setLayout(layout)
        
        index = self.addTab(chat_container, tab_name)
        self.setCurrentIndex(index)
        self._states[index] = {"history": []}
        return index, chat_history

    def get_current_chat_history(self):
        if self.count() == 0:
            return None
        current_widget = self.currentWidget()
        # The history list is the first child in the VBoxLayout
        return current_widget.layout().itemAt(0).widget()

    def get_current_state(self):
        if self.count() == 0:
            return None
        return self._states.get(self.currentIndex(), {})
        
    def update_current_state(self, state: dict):
        if self.count() > 0:
            self._states[self.currentIndex()] = state

    def close_tab(self, index):
        if self.count() > 1:
            widget = self.widget(index)
            self.removeTab(index)
            widget.deleteLater()
            if index in self._states:
                del self._states[index]
        else:
            self.widget(index).layout().itemAt(0).widget().clear()
            self._states[index] = {"history": []}
