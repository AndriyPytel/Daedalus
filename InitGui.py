"""
FreeCadCopilot - GUI Initialization
"""
import FreeCADGui as Gui
import FreeCAD as App

from Mod import CopilotWorkbench


class FreeCadCopilotWorkbench(CopilotWorkbench.CopilotWorkbench):
    """Workbench initialization class."""
    
    MenuText = "FreeCad Copilot"
    ToolTip = "AI-powered command execution for FreeCAD"
    Icon = ""

Gui.addWorkbench(FreeCadCopilotWorkbench())
