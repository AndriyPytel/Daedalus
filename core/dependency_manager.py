# FreeCadCopilot/core/dependency_manager.py
import FreeCAD
import os
import sys
import subprocess
import importlib.util
from PySide.QtGui import QMessageBox

# List of required packages for the addon
REQUIRED_PACKAGES = [
    "litellm",
]

def get_target_directory():
    """
    Returns the standard directory for pip packages and creates it if it doesn't exist.
    This is the recommended location for user-installed Python packages in FreeCAD.
    """
    path = os.path.join(FreeCAD.getUserAppDataDir(), "AdditionalPythonPackages")
    if not os.path.exists(path):
        os.makedirs(path)
    return path

def check_package(package_name):
    """
    Checks if a specific Python package is installed and available.
    It temporarily adds the target directory to the path to ensure detection.
    """
    target_dir = get_target_directory()
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)
    
    spec = importlib.util.find_spec(package_name)
    return spec is not None

def install_package(package_name, target_directory):
    """
    Installs a Python package using pip, targeting a specific directory.
    This function uses the robust `freecad.utils.get_python_exe()` to ensure
    the correct Python interpreter is used, which is critical in packaged
    environments like Flatpak or AppImage.
    """
    try:
        from freecad.utils import get_python_exe
        python_exe = get_python_exe()
    except ImportError:
        FreeCAD.Console.PrintError("Could not import 'get_python_exe' from 'freecad.utils'.\n")
        FreeCAD.Console.PrintError("This might be an older FreeCAD version. Falling back to 'sys.executable'.\n")
        python_exe = sys.executable

    FreeCAD.Console.PrintMessage(f"Using Python interpreter: {python_exe}\n")

    command = [
        python_exe,
        "-m", "pip", "install",
        "--target", target_directory,
        "--disable-pip-version-check",
        package_name
    ]

    try:
        # Using subprocess.run for a simpler blocking call
        process = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        FreeCAD.Console.PrintMessage(process.stdout)
        if process.stderr:
            FreeCAD.Console.PrintWarning(process.stderr)
        return True, ""
    except subprocess.CalledProcessError as e:
        error_message = f"pip command failed with exit code {e.returncode}.\n"
        error_message += f"Stdout: {e.stdout}\n"
        error_message += f"Stderr: {e.stderr}\n"
        return False, error_message
    except FileNotFoundError:
        return False, f"The Python executable '{python_exe}' was not found."

def run_dependency_check():
    """
    The main function to check for and install missing dependencies.
    It identifies missing packages, asks the user for permission to install,
    and then proceeds with the installation, recommending a restart upon completion.
    """
    target_dir = get_target_directory()
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)

    missing_packages = [pkg for pkg in REQUIRED_PACKAGES if not check_package(pkg)]

    if not missing_packages:
        FreeCAD.Console.PrintMessage("[FreeCadCopilot] All required packages are installed.\n")
        return

    # Ask user for permission to install
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Information)
    msg_box.setText("FreeCadCopilot - Additional Dependencies Required")
    msg_box.setInformativeText(
        "The following Python packages are missing and need to be installed:\n\n"
        f"  - {', '.join(missing_packages)}\n\n"
        "Do you want to install them now? This may take a few moments.\n"
        "The installation progress will be shown in the FreeCAD Report view."
    )
    msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msg_box.setDefaultButton(QMessageBox.Yes)
    
    if msg_box.exec_() != QMessageBox.Yes:
        QMessageBox.warning(None, "Dependencies Missing", "Required packages were not installed. The FreeCadCopilot addon may not function correctly.")
        return

    # Install missing packages
    FreeCAD.Console.PrintMessage("--- [FreeCadCopilot] Starting dependency installation ---\n")
    all_successful = True
    for package in missing_packages:
        FreeCAD.Console.PrintMessage(f"Installing '{package}'...\n")
        success, error_msg = install_package(package, target_dir)
        if success:
            FreeCAD.Console.PrintMessage(f"'{package}' installed successfully.\n")
            importlib.invalidate_caches() # Crucial for the import system to recognize the new package
        else:
            QMessageBox.critical(None, "Installation Error", f"Failed to install '{package}':\n\n{error_msg}\n\nPlease check the Report view for details.")
            all_successful = False
            break
    
    FreeCAD.Console.PrintMessage("--- [FreeCadCopilot] Dependency installation finished ---\n")

    if all_successful:
        QMessageBox.information(None, "Restart Recommended", "Packages installed successfully.\n\nA restart of FreeCAD is recommended to ensure all components of the addon are loaded correctly.")
    else:
        QMessageBox.warning(None, "Installation Incomplete", "Some packages could not be installed. The addon may not work as expected.")
