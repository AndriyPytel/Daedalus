"""
Undo manager to track revert button state (FR7)
"""
from PySide2.QtCore import QObject, Signal


class UndoManager(QObject):
    """Manages the stack of revert buttons and their enabled state."""
    
    def __init__(self):
        """Initialize the undo manager."""
        super().__init__()
        # Stack of widgets that have been executed (most recent on top)
        self.revert_stack = []
    
    def push(self, widget):
        """Add a widget to the stack and update enabled states.
        
        Args:
            widget: The CodeBlockWidget that was just executed
        """
        # Disable all existing revert buttons
        for w in self.revert_stack:
            w.set_revert_enabled(False)
        
        # Add new widget to stack
        self.revert_stack.append(widget)
        
        # Enable only the top widget
        if self.revert_stack:
            self.revert_stack[-1].set_revert_enabled(True)
    
    def pop(self):
        """Remove the top widget from the stack and update enabled states.
        
        Returns:
            The widget that was popped, or None if stack is empty
        """
        if not self.revert_stack:
            return None
        
        # Remove top widget
        top = self.revert_stack.pop()
        
        # Disable it
        top.set_revert_enabled(False)
        
        # Enable the new top widget
        if self.revert_stack:
            self.revert_stack[-1].set_revert_enabled(True)
        
        return top
    
    def clear(self):
        """Clear the entire stack."""
        for widget in self.revert_stack:
            widget.set_revert_enabled(False)
        self.revert_stack.clear()
    
    def is_empty(self):
        """Check if the stack is empty.
        
        Returns:
            bool: True if stack is empty
        """
        return len(self.revert_stack) == 0
