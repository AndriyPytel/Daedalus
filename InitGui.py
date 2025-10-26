"""
FreeCadCopilot - GUI Initialization
"""

class CopilotWorkbench(Workbench):
    """Main workbench for FreeCadCopilot."""
    
    def __init__(self):
        """Initialize the workbench."""
        self.__class__.MenuText = "FreeCad Copilot"
        self.__class__.ToolTip = "AI-powered command execution for FreeCAD"
        # self.__class__.Icon = "Resources/icons/copilot.svg"
        self.copilot_panel = None
    
    def Activated(self):
        """Called when workbench is activated."""
        if self.copilot_panel is None or not self.copilot_panel.isVisible():
            self.copilot_panel = CopilotPanel()
            Gui.addDockWidget(0x0008, self.copilot_panel)  # Right dock area
    
    def Deactivated(self):
        """Called when workbench is deactivated."""
        if self.copilot_panel:
            Gui.removeDockWidget(self.copilot_panel)
    
    def Initialize(self):
        """Called when workbench is first loaded."""
        pass
    
    def ContextMenu(self, recipient):
        """Called when context menu is requested."""
        pass
    
    def GetClassName(self):
        return "Gui::PythonWorkbench"

Gui.addWorkbench(CopilotWorkbench())
