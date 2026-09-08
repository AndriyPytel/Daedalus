"""
FreeCadCopilot - GUI Initialization
"""
import FreeCADGui as Gui
import FreeCAD as App
import traceback
import os
import sys

def initialize():
    """
    This function is called by FreeCAD when the GUI is initialized.
    It sets up the system path, runs the dependency check, and loads the workbench.
    """
    App.Console.PrintMessage("--- [FreeCadCopilot] Initializing GUI ---\n")
    
    try:
        # 1. Register icon path
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
        from core import dependency_manager
        addon_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(dependency_manager.__file__))))
        icon_path = os.path.join(addon_path, "Resources", "icons")
        Gui.addIconPath(icon_path)
        App.Console.PrintMessage(f"[DEBUG] Registered icon path: {icon_path}\n")

        # 3. Run the dependency check
        dependency_manager.run_dependency_check()
        App.Console.PrintMessage("[DEBUG] Dependency check process completed.\n")

        # 4. Register the Preference Page
        # This import executes the addPreferencePage() call in CopilotPreferences.py
        from Mod import CopilotPreferences
        App.Console.PrintMessage("[DEBUG] CopilotPreferences module loaded to register preference page.\n")

        # 5. Load the workbench
        from Mod.CopilotWorkbench import CopilotWorkbench
        Gui.addWorkbench(CopilotWorkbench())
        App.Console.PrintMessage("[SUCCESS] CopilotWorkbench loaded into FreeCAD GUI.\n")

        # 6. Dock the chat on the main window, so it is there on every
        #    workbench rather than only on ours. FreeCAD may not have built
        #    the window yet at this point; the timer runs once it has.
        from ui import copilot_dock
        if copilot_dock.install(Gui.getMainWindow()) is None:
            from PySide import QtCore
            QtCore.QTimer.singleShot(0, lambda: copilot_dock.install(Gui.getMainWindow()))

    except Exception:
        App.Console.PrintError("=" * 50 + "\n")
        App.Console.PrintError("An error occurred during FreeCadCopilot initialization.\n")
        App.Console.PrintError(traceback.format_exc())
        App.Console.PrintError("=" * 50 + "\n")

# Standard execution path when FreeCAD loads the addon's GUI
initialize()
