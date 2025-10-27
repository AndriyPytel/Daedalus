"""
FreeCadCopilot - GUI Initialization
"""
import FreeCADGui as Gui
import FreeCAD as App

import traceback
import FreeCAD # Переконайтесь, що FreeCAD імпортовано, щоб мати доступ до Console

try:
    
    from Mod.CopilotWorkbench import CopilotWorkbench


    class FreeCadCopilotWorkbench(CopilotWorkbench):
        """Workbench initialization class."""
        
        MenuText = "FreeCad Copilot"
        ToolTip = "AI-powered command execution for FreeCAD"
        # Icon = "Resources/icons/copilot.svg"

    Gui.addWorkbench(FreeCadCopilotWorkbench)


except Exception as e:
    # Цей код виконається, якщо в блоці 'try' станеться помилка
    FreeCAD.Console.PrintError("="*50 + "\n")
    FreeCAD.Console.PrintError(f"!!! КРИТИЧНА ПОМИЛКА в InitGui.py: {e}\n")
    FreeCAD.Console.PrintError(traceback.format_exc()) # Це надрукує повний стектрейс!
    FreeCAD.Console.PrintError("="*50 + "\n")
