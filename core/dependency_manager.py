# FreeCadCopilot/core/dependency_manager.py
import csv
import FreeCAD
import os
import re
import shutil
import sys
import subprocess
import importlib.util
from PySide.QtGui import QMessageBox

_DIST_INFO = re.compile(r"^(?P<name>.+?)-(?P<version>\d[^-]*)\.dist-info$")

# pip name -> import name for the addon's required packages; the two differ for Pillow
REQUIRED_PACKAGES = {
    "litellm": "litellm",
    "numpy": "numpy",
    "Pillow": "PIL",
}

def get_target_directory():
    """
    Returns the standard directory for pip packages and creates it if it doesn't exist.
    This is the recommended location for user-installed Python packages in FreeCAD.
    """
    path = os.path.join(FreeCAD.getUserAppDataDir(), "AdditionalPythonPackages")
    if not os.path.exists(path):
        os.makedirs(path)
    return path

# ── Layered installs (FCC-28) ─────────────────────────────────────────────────
#
# `pip install --target` without `--upgrade` refuses to overwrite a file that is
# already there but writes the new dist-info anyway. Measured: installing
# six 1.15.0 and then six 1.16.0 leaves `six-1.16.0.dist-info` beside
# `six-1.15.0.dist-info`, and `six.py` still reporting `__version__ = "1.15.0"`.
# That is how two pydantic and two pydantic_core versions came to sit side by
# side, and how a pydantic can end up not matching its pydantic_core.
#
# `--upgrade` fixes the code. It does not remove the superseded dist-info, so
# the metadata still describes two installs where there is one — which is what
# `prune_superseded` is for.


def _normalised(name):
    """PEP 503: `pydantic_core` and `Pydantic-Core` are the same distribution."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_key(version):
    """Sortable enough to say which of two versions of one package is newer."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r"[._-]", version)]


def installed_distributions(target_directory):
    """{distribution: [(version, dist-info path), …]} in *target_directory*."""
    found = {}
    for entry in sorted(os.listdir(target_directory)):
        match = _DIST_INFO.match(entry)
        if not match or not os.path.isdir(os.path.join(target_directory, entry)):
            continue
        found.setdefault(_normalised(match.group("name")), []).append(
            (match.group("version"), os.path.join(target_directory, entry)))
    return found


def _recorded_files(dist_info):
    """The paths a distribution's RECORD claims, relative to the target."""
    record = os.path.join(dist_info, "RECORD")
    try:
        with open(record, newline="", encoding="utf-8") as handle:
            return {row[0] for row in csv.reader(handle) if row}
    except OSError:
        return set()


def prune_superseded(target_directory):
    """
    Leave one version of each distribution: the newest.

    A superseded install's files are removed only where the surviving version
    does not claim them too — `--upgrade` has already overwritten the shared
    ones, and deleting those would break the install this is repairing.

    Returns the dist-info directory names removed.
    """
    removed = []
    for versions in installed_distributions(target_directory).values():
        if len(versions) < 2:
            continue
        versions.sort(key=lambda pair: _version_key(pair[0]))
        keeper = versions[-1][1]
        kept_files = _recorded_files(keeper)
        for _, superseded in versions[:-1]:
            for relative in _recorded_files(superseded) - kept_files:
                path = os.path.normpath(os.path.join(target_directory, relative))
                if not path.startswith(os.path.normpath(target_directory)):
                    continue        # a RECORD is not a licence to delete elsewhere
                try:
                    os.remove(path)
                except OSError:
                    pass
            shutil.rmtree(superseded, ignore_errors=True)
            removed.append(os.path.basename(superseded))
    return removed


def check_package(module_name):
    """
    Checks if a specific Python module is importable.
    It temporarily adds the target directory to the path to ensure detection.
    """
    target_dir = get_target_directory()
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)

    spec = importlib.util.find_spec(module_name)
    return spec is not None

def missing_packages():
    """The pip names of the required packages whose module does not import."""
    return [pip_name for pip_name, module in REQUIRED_PACKAGES.items() if not check_package(module)]

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
        # Without this pip declines to overwrite a file that is already there,
        # and installs the new metadata over the old code (FCC-28).
        "--upgrade",
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

    # Before the missing check, not after it: a directory that already has
    # everything is exactly the one an earlier layered install left behind, and
    # returning early would never repair it (FCC-28).
    superseded = prune_superseded(target_dir)
    if superseded:
        FreeCAD.Console.PrintMessage(
            "[FreeCadCopilot] Removed %d superseded package version(s): %s\n"
            % (len(superseded), ", ".join(superseded)))

    missing = missing_packages()

    if not missing:
        FreeCAD.Console.PrintMessage("[FreeCadCopilot] All required packages are installed.\n")
        return

    # Ask user for permission to install
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
    
    if msg_box.exec_() != QMessageBox.Yes:
        QMessageBox.warning(None, "Dependencies Missing", "Required packages were not installed. The FreeCadCopilot addon may not function correctly.")
        return

    # Install missing packages
    FreeCAD.Console.PrintMessage("--- [FreeCadCopilot] Starting dependency installation ---\n")
    all_successful = True
    for package in missing:
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
