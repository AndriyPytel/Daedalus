class CopilotWorkbench(Workbench):
    MenuText = "FreeCad Copilot"
    ToolTip = "AI-powered command execution for FreeCAD"

    def Initialize(self):
        commands = ["Start_RPC_Server", "Stop_RPC_Server"]
        self.appendToolbar("FreeCAD MCP", commands)
        self.appendMenu("FreeCAD MCP", commands)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def ContextMenu(self, recipient):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(CopilotWorkbench())
