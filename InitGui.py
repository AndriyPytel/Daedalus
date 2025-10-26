"""
FreeCadCopilot - GUI Initialization
"""

from ui.copilot_panel import CopilotPanel


class CopilotWorkbench(Workbench):
    """Main workbench for FreeCadCopilot."""
    MenuText = "FreeCad Copilot"
    ToolTip = "AI-powered command execution for FreeCAD"
    Icon = "Resources/icons/copilot.svg"
    copilot_panel = None

    
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
    
    def Initialize(self):
        """This function is executed when the workbench is first activated.
        It is executed once in a FreeCAD session followed by the Activated function.
        """
        #import MyModuleA, MyModuleB # import here all the needed files that create your FreeCAD commands  # pyright: ignore[reportMissingImports]
        self.list = ["MyCommand1", "MyCommand2"] # a list of command names created in the line above
        self.appendToolbar("My Commands", self.list) # creates a new toolbar with your commands
        self.appendMenu("My New Menu", self.list) # creates a new menu
        self.appendMenu(["An existing Menu", "My submenu"], self.list) # appends a submenu to an existing menu

    def ContextMenu(self, recipient):
        """This function is executed whenever the user right-clicks on screen"""
        # "recipient" will be either "view" or "tree"
        self.appendContextMenu("My commands", self.list) # add commands to the context menu

    def GetClassName(self): 
        # This function is mandatory if this is a full Python workbench
        # This is not a template, the returned string should be exactly "Gui::PythonWorkbench"
        return "Gui::PythonWorkbench"
       

Gui.addWorkbench(CopilotWorkbench())
