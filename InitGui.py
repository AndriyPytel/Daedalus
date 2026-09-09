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
        # 1. Register icon path
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
        from core import dependency_manager
        addon_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(dependency_manager.__file__))))
        icon_path = os.path.join(addon_path, "src", "resources", "icons")
        Gui.addIconPath(icon_path)
        App.Console.PrintMessage(f"[DEBUG] Registered icon path: {icon_path}\n")

        # 3. Run the dependency check
        try:
            from PySide2.QtWidgets import QMessageBox
        except ImportError:
            from PySide.QtGui import QMessageBox

        def _confirm(missing):
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setText("FreeCadCopilot - Additional Dependencies Required")
            msg_box.setInformativeText(
                "The following Python packages are missing and need to be installed:\n\n"
                f"  - {', '.join(missing)}\n\n"
                "Do you want to install them now? This may take a few moments.\n"
                "The installation progress will be shown in the FreeCAD Report view."
            )
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg_box.setDefaultButton(QMessageBox.Yes)
            return msg_box.exec_() == QMessageBox.Yes

        def _on_install_error(package, error_msg):
            QMessageBox.critical(None, "Installation Error",
                f"Failed to install '{package}':\n\n{error_msg}\n\nPlease check the Report view for details.")

        def _on_finished(all_successful):
            if all_successful:
                QMessageBox.information(None, "Restart Recommended",
                    "Packages installed successfully.\n\nA restart of FreeCAD is recommended to ensure "
                    "all components of the addon are loaded correctly.")
            else:
                QMessageBox.warning(None, "Installation Incomplete",
                    "Some packages could not be installed. The addon may not work as expected.")

        dependency_manager.run_dependency_check(
            confirm=_confirm, on_install_error=_on_install_error, on_finished=_on_finished)
        App.Console.PrintMessage("[DEBUG] Dependency check process completed.\n")

        # 4. Register the Preference Page
        # This import executes the addPreferencePage() call in CopilotPreferences.py
        from Mod import CopilotPreferences
        App.Console.PrintMessage("[DEBUG] CopilotPreferences module loaded to register preference page.\n")

        # 5. Load the workbench
        from Mod.CopilotWorkbench import CopilotWorkbench
        Gui.addWorkbench(CopilotWorkbench())
        App.Console.PrintMessage("[SUCCESS] CopilotWorkbench loaded into FreeCAD GUI.\n")

        # 6. Dock the chat on the main window, so it is there on every
        #    workbench rather than only on ours. FreeCAD may not have built
        #    the window yet at this point; the timer runs once it has.
        from ui import copilot_dock
        if copilot_dock.install(Gui.getMainWindow()) is None:
            from PySide import QtCore
            QtCore.QTimer.singleShot(0, lambda: copilot_dock.install(Gui.getMainWindow()))

    except Exception:
        App.Console.PrintError("=" * 50 + "\n")
        App.Console.PrintError("An error occurred during FreeCadCopilot initialization.\n")
        App.Console.PrintError(traceback.format_exc())
        App.Console.PrintError("=" * 50 + "\n")

# Standard execution path when FreeCAD loads the addon's GUI
initialize()
