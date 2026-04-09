# FreeCadCopilot/core/command_executor.py
"""
Executes AI-generated FreeCAD scripts inside a transaction.

Returns a structured result dict so callers always get a predictable shape:
    {"status": "success"|"error", "message": str, "crash_report": str|None}
"""
import FreeCAD
from utils.logger import log_exception, generate_crash_report


import threading

import ast

def execute(script: str) -> dict:
    """
    Run *script* inside a FreeCAD transaction.
    If called from a non-main thread, dispatches to main thread and waits.
    """
    try:
        ast.parse(script)
    except SyntaxError as e:
        return {"status": "error", "message": f"Syntax Error in generated code: {e}", "crash_report": None}

    try:
        import FreeCADGui
    except ImportError:
        FreeCADGui = None

    # Determine if we are on the main thread
    curr_thread = threading.current_thread()
    main_thread = threading.main_thread()
    
    is_main_thread_py = curr_thread is main_thread
    is_main_thread_qt = True
    
    try:
        from PySide2 import QtCore, QtWidgets
        app = QtWidgets.qApp
        if app:
            is_main_thread_qt = (QtCore.QThread.currentThread() == app.thread())
        else:
            is_main_thread_qt = is_main_thread_py
    except Exception:
        is_main_thread_qt = is_main_thread_py

    # On macOS, GUI calls MUST be on the main process thread (ID 0 / main_thread)
    is_main_thread = is_main_thread_py and is_main_thread_qt

    # Telemetry
    FreeCAD.Console.PrintMessage(
        f"[Copilot DEBUG] execute() | Thread: {curr_thread.name} (ID: {threading.get_ident()}) | "
        f"MainPY: {is_main_thread_py} | MainQT: {is_main_thread_qt}\n"
    )

    if not is_main_thread:
        result_holder = {"res": None}
        done_event = threading.Event()

        def _run_on_main():
            try:
                result_holder["res"] = _execute_internal(script)
            finally:
                done_event.set()

        dispatched = False
        
        # 1. Try FreeCADGui.addTodo
        if FreeCADGui and hasattr(FreeCADGui, "addTodo"):
            try:
                FreeCADGui.addTodo(_run_on_main)
                dispatched = True
            except Exception as e:
                FreeCAD.Console.PrintError(f"[Copilot] addTodo failed: {e}\n")

        # 2. Try QTimer fallback
        if not dispatched:
            try:
                from PySide2 import QtCore, QtWidgets
                app = QtWidgets.qApp
                if app:
                    # Use a zero-timer to execute on the app's event loop (main thread)
                    QtCore.QTimer.singleShot(0, _run_on_main)
                    dispatched = True
            except Exception:
                pass

        if not dispatched:
            return {
                "status": "error", 
                "message": "Could not dispatch to GUI thread. Command execution aborted to prevent crash.", 
                "crash_report": None
            }

        # Wait for completion (30s timeout)
        if not done_event.wait(timeout=30):
            return {
                "status": "error", 
                "message": "Execution timed out on GUI thread.", 
                "crash_report": None
            }
        
        return result_holder["res"]
    else:
        # Already on main thread
        return _execute_internal(script)


def _execute_internal(script: str) -> dict:
    """The actual implementation of script execution. MUST RUN ON MAIN THREAD."""
    FreeCAD.Console.PrintMessage(
        f"[Copilot DEBUG] _execute_internal() | Thread: {threading.current_thread().name} (ID: {threading.get_ident()})\n"
    )
    
    try:
        import FreeCADGui
    except ImportError:
        FreeCADGui = None

    doc = FreeCAD.ActiveDocument
    if not doc:
        return {"status": "error", "message": "No active document.", "crash_report": None}

    doc.openTransaction("AI Generated Feature")
    try:
        ns = {
            "App": FreeCAD,
            "Gui": FreeCADGui,
            "FreeCAD": FreeCAD,
            "FreeCADGui": FreeCADGui,
        }
        exec(script, ns)  # noqa: S102
        doc.recompute()
        doc.commitTransaction()
        return {"status": "success", "message": "Execution successful", "crash_report": None}

    except Exception as exc:
        doc.abortTransaction()
        crash = generate_crash_report(exc, context="command_executor.execute()")
        return {"status": "error", "message": str(exc), "crash_report": crash}


def revert() -> None:
    """Undo the last transaction in the active document."""
    doc = FreeCAD.ActiveDocument
    if doc:
        doc.undo()
