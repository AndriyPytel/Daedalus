"""
FreeCadCopilot Workbench
"""
import FreeCADGui as Gui
import FreeCAD as App
import os

from ui import copilot_dock

logo_icon = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "Resources", "icons", "copilot.svg"
)


class CopilotWorkbench(Gui.Workbench):
    """Main workbench for FreeCadCopilot."""

    def __init__(self):
        """Initialize the workbench."""
        self.__class__.MenuText = "FreeCad Copilot"
        self.__class__.ToolTip = "AI-powered command execution for FreeCAD"
        self.__class__.Icon = logo_icon

    def Activated(self):
        """
        Called when workbench is activated.

        The chat is the main window's, installed at start-up, so all this does
        is make sure it is on screen for the user who came looking for it here.
        """
        panel = copilot_dock.install(Gui.getMainWindow())
        if panel is None:
            from utils.logger import logger
            logger.error("Cannot get main window")
            return
        panel.show()

    def Deactivated(self):
        """
        Called when workbench is deactivated.

        Deliberately nothing: hiding the panel here is what tied the chat to
        this one workbench.
        """

    def Initialize(self):
        """Called when workbench is first loaded."""
        pass

    def ContextMenu(self, recipient):
        """Called when context menu is requested."""
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"