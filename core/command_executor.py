# FreeCadCopilot/core/command_executor.py
import FreeCAD

def execute(script):
    """
    Executes a script within a FreeCAD transaction.
    """
    doc = FreeCAD.ActiveDocument
    if not doc:
        FreeCAD.Console.PrintError("No active document to execute script in.\\n")
        return "No active document."
        
    doc.openTransaction("Copilot Action")
    try:
        exec(script, {'App': FreeCAD, 'Gui': FreeCADGui})
        doc.commitTransaction()
        return True
    except Exception as e:
        doc.abortTransaction()
        FreeCAD.Console.PrintError(f"Error executing script: {e}\\n")
        return e

def revert():
    """
    Reverts the last transaction in the active document.
    """
    doc = FreeCAD.ActiveDocument
    if doc:
        doc.undo()
