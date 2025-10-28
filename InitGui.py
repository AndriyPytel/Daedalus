"""
FreeCadCopilot - GUI Initialization
"""
import FreeCADGui as Gui
import FreeCAD as App

import traceback

try:
    
    from Mod.CopilotWorkbench import CopilotWorkbench

    Gui.addWorkbench(CopilotWorkbench())


except Exception as e:
    App.Console.PrintError("="*50 + "\n")
    App.Console.PrintError(traceback.format_exc()) 
    App.Console.PrintError("="*50 + "\n")
