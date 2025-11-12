"""
FreeCadCopilot Workbench
"""
import FreeCADGui as Gui
import FreeCAD as App
import os

from ui.copilot_panel import CopilotPanel

logo_icon = os.path.join(os.path.dirname(__file__), '../Resources/icons/copilot.svg')


class CopilotWorkbench(Gui.Workbench):
    """Main workbench for FreeCadCopilot."""

    def __init__(self):
        """Initialize the workbench."""
        self.__class__.MenuText = "FreeCad Copilot"
        self.__class__.ToolTip = "AI-powered command execution for FreeCAD"
        self.__class__.Icon = logo_icon
        self.copilot_panel = None
        self.dock_area = None

    def Activated(self):
        """Called when workbench is activated."""
        from PySide import QtCore
        import FreeCAD

        mw = Gui.getMainWindow()
        if mw is None:
            FreeCAD.Console.PrintError("Cannot get main window\n")
            return

        if self.copilot_panel is None:
            self.copilot_panel = CopilotPanel()

        # Add dock widget to the main window
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.copilot_panel)
        self.copilot_panel.show()

    def Deactivated(self):
        """Called when workbench is deactivated."""
        if self.copilot_panel:
            self.copilot_panel.hide()

    def Initialize(self):
        """Called when workbench is first loaded."""
        pass

    def ContextMenu(self, recipient):
        """Called when context menu is requested."""
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"