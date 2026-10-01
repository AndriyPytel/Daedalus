"""
Daedalus - GUI Initialization

FreeCAD runs this file with exec(compile(source, path, "exec")) from inside a
function of FreeCADGuiInit. There is no __file__, and everything defined here
is a local of that call, so initialize() cannot look itself up by name: the
addon directory is read off its code object at module level and handed in.
"""
import FreeCADGui as Gui
import FreeCAD as App
import traceback
import os
import sys

def initialize(addon_path):
    """
    This function is called by FreeCAD when the GUI is initialized.
    It sets up the system path, runs the dependency check, and installs the
    dock and the preference pages.
    """
    App.Console.PrintMessage("--- [Daedalus] Initializing GUI ---\n")
    
    try:
        # 1. Register icon path
        sys.path.insert(0, os.path.join(addon_path, "src"))
        sys.path.insert(0, os.path.join(addon_path, "Mod"))
        sys.path.insert(0, addon_path)
        from core import dependency_manager
        icon_path = os.path.join(addon_path, "Mod", "resources", "icons")
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
            msg_box.setText("Daedalus - Additional Dependencies Required")
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
        # This import executes the addPreferencePage() call in DaedalusPreferences.py
        from Mod import DaedalusPreferences
        App.Console.PrintMessage("[DEBUG] DaedalusPreferences module loaded to register preference page.\n")

        # 5. Dock the chat on the main window. There is no workbench of ours
        #    to switch to; the dock is a fixture of the window itself.
        #    FreeCAD may not have built the window yet at this point; the
        #    timer runs once it has.
        from ui import daedalus_dock
        if daedalus_dock.install(Gui.getMainWindow()) is None:
            from PySide import QtCore
            QtCore.QTimer.singleShot(0, lambda: daedalus_dock.install(Gui.getMainWindow()))

    except Exception:
        App.Console.PrintError("=" * 50 + "\n")
        App.Console.PrintError("An error occurred during Daedalus initialization.\n")
        App.Console.PrintError(traceback.format_exc())
        App.Console.PrintError("=" * 50 + "\n")

initialize(os.path.dirname(os.path.abspath(initialize.__code__.co_filename)))
