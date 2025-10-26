"""
Command executor for safe, transactional code execution
"""
import FreeCAD as App
import FreeCADGui as Gui


class CommandExecutor:
    """Executes Python code blocks safely with transaction support."""
    
    def __init__(self):
        """Initialize the command executor."""
        pass
    
    def execute(self, code):
        """Execute a code block in a FreeCAD transaction.
        
        Args:
            code: Python code string to execute
            
        Returns:
            tuple: (success: bool, message: str, error: Exception or None)
        """
        try:
            # Get active document
            doc = App.ActiveDocument
            if doc is None:
                # Create new document if none exists
                doc = App.newDocument("Untitled")
            
            # Start transaction
            doc.openTransaction("FreeCadCopilot")
            
            # Execute the code
            try:
                exec(code, {"App": App, "Gui": Gui, "__builtins__": __builtins__})
                
                # Update GUI to reflect changes
                Gui.updateGui()
                
                # Commit transaction
                doc.commitTransaction()
                
                return True, "Code executed successfully", None
                
            except Exception as e:
                # Abort transaction on error
                doc.abortTransaction()
                return False, f"Error during execution: {str(e)}", e
                
        except Exception as e:
            return False, f"Error setting up transaction: {str(e)}", e
    
    def revert(self):
        """Revert the last transaction (call App.undo).
        
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            doc = App.ActiveDocument
            if doc is None:
                return False, "No active document"
            
            # Call undo
            doc.undo()
            
            # Update GUI
            Gui.updateGui()
            
            return True, "Transaction reverted"
            
        except Exception as e:
            return False, f"Error reverting: {str(e)}"
