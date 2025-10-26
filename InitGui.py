"""
FreeCadCopilot - GUI Initialization
"""

from Mod.CopilotWorkbench import CopilotWorkbench


class FreeCadCopilotWorkbench(CopilotWorkbench):
    """Workbench initialization class."""
    
    MenuText = "FreeCad Copilot"
    ToolTip = "AI-powered command execution for FreeCAD"
    Icon = "Resources/icons/copilot.svg"


Gui.addWorkbench(FreeCadCopilotWorkbench)
