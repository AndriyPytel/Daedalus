# FreeCadCopilot/core/command_executor.py
import FreeCAD

def execute(script):
    """
    Executes a script within a FreeCAD transaction.
    """
    try:
        import FreeCADGui
    except ImportError:
        FreeCADGui = None

    doc = FreeCAD.ActiveDocument
    if not doc:
        FreeCAD.Console.PrintError("No active document to execute script in.\\n")
        return {"status": "error", "message": "No active document."}
        
    doc.openTransaction("AI Generated Feature")
    try:
        # Provide common aliases in the exec namespace
        exec_namespace = {'App': FreeCAD, 'Gui': FreeCADGui, 'FreeCAD': FreeCAD, 'FreeCADGui': FreeCADGui}
        exec(script, exec_namespace)
        doc.recompute() # Make sure changes show up before committing
        doc.commitTransaction()
        return {"status": "success", "message": "Execution successful"}
    except Exception as e:
        doc.abortTransaction()
        FreeCAD.Console.PrintError(f"Error executing script: {e}\\n")
        raise e

def revert():
    """
    Reverts the last transaction in the active document.
    """
    doc = FreeCAD.ActiveDocument
    if doc:
        doc.undo()
