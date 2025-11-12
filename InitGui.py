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
        # 3. Run the dependency check
        from core import dependency_manager
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

    except Exception:
        App.Console.PrintError("=" * 50 + "\n")
        App.Console.PrintError("An error occurred during FreeCadCopilot initialization.\n")
        App.Console.PrintError(traceback.format_exc())
        App.Console.PrintError("=" * 50 + "\n")

# Standard execution path when FreeCAD loads the addon's GUI
initialize()
