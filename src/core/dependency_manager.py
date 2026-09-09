"""
The Python packages the addon needs, installed into FreeCAD's own directory for
user-installed packages.

pymupdf is what makes a PDF a drawing rather than an opaque attachment: the page
is rendered at the DPI its own callouts ask for, and its text layer is the exact
list of what is printed on it. Without it `sheet_reader` says so and the sheet
is sent whole. PyYAML is what the part Design is written down as; litellm
already pulls it in, so it is here to be named rather than to be new — a reading
that silently stopped being written because a transitive dependency moved would
otherwise be discovered by its absence, months later.

`pip install --target` without `--upgrade` refuses to overwrite a file that is
already there but writes the new dist-info anyway: installing six 1.15.0 and
then six 1.16.0 leaves `six-1.16.0.dist-info` beside `six-1.15.0.dist-info`, and
`six.py` still reporting `__version__ = "1.15.0"`. That is how two pydantic and
two pydantic_core versions come to sit side by side, and how a pydantic can end
up not matching its pydantic_core. `--upgrade` fixes the code, but it does not
remove the superseded dist-info, so the metadata still describes two installs
where there is one — which is what `prune_superseded` is for.
"""
import csv
import FreeCAD
import os
import re
import shutil
import sys
import subprocess
import importlib.util

_DIST_INFO = re.compile(r"^(?P<name>.+?)-(?P<version>\d[^-]*)\.dist-info$")

REQUIRED_PACKAGES = {
    "litellm": "litellm",
    "numpy": "numpy",
    "Pillow": "PIL",
    "pymupdf": "pymupdf",
    "PyYAML": "yaml",
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
    ones, and deleting those would break the install this is repairing. A path
    a RECORD claims outside the target directory is left alone: a RECORD is not
    a licence to delete elsewhere.

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
                    continue
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

    `InitGui.py:26` calls this synchronously, before `Gui.addWorkbench` and
    the rest of the addon load, so a `pip install` that hangs on the network
    freezes FreeCAD before any of it appears. The 120 s ceiling is pip's own
    default connect/read timeout doubled, room for a slow mirror without
    leaving a stalled one to block the whole startup indefinitely.
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
        "--upgrade",
        "--disable-pip-version-check",
        package_name
    ]

    try:
        process = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120
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
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Could not run '{python_exe}': {e}"

def run_dependency_check(confirm=None, on_install_error=None, on_finished=None):
    """
    The main function to check for and install missing dependencies.
    It identifies missing packages, asks the user for permission to install,
    and then proceeds with the installation, recommending a restart upon completion.

    Superseded versions are pruned before the missing check rather than after
    it: a directory that already has everything is exactly the one an earlier
    layered install left behind, and returning early would never repair it.
    Caches are invalidated after each install, or the import system does not
    see the package that was just put there.

    *confirm* asks whether to install the *missing* packages and returns a
    bool; a GUI wraps this in a Yes/No dialog (`InitGui.py`), and the default
    declines since there is nothing here to ask through (FCC-359: this module
    has no Qt import, so `core` can run with no Qt at all). *on_install_error*
    takes ``(package, error_message)`` for a package that failed to install,
    and *on_finished* takes ``all_successful``; both default to a no-op, and a
    GUI supplies the dialogs they used to be.
    """
    if confirm is None:
        confirm = lambda missing: False
    if on_install_error is None:
        on_install_error = lambda package, error_msg: None
    if on_finished is None:
        on_finished = lambda all_successful: None

    target_dir = get_target_directory()
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)

    superseded = prune_superseded(target_dir)
    if superseded:
        FreeCAD.Console.PrintMessage(
            "[FreeCadCopilot] Removed %d superseded package version(s): %s\n"
            % (len(superseded), ", ".join(superseded)))

    missing = missing_packages()

    if not missing:
        FreeCAD.Console.PrintMessage("[FreeCadCopilot] All required packages are installed.\n")
        return

    if not confirm(missing):
        FreeCAD.Console.PrintMessage(
            "[FreeCadCopilot] Required packages were not installed: %s\n" % ", ".join(missing))
        return

    FreeCAD.Console.PrintMessage("--- [FreeCadCopilot] Starting dependency installation ---\n")
    all_successful = True
    for package in missing:
        FreeCAD.Console.PrintMessage(f"Installing '{package}'...\n")
        success, error_msg = install_package(package, target_dir)
        if success:
            FreeCAD.Console.PrintMessage(f"'{package}' installed successfully.\n")
            importlib.invalidate_caches()
        else:
            on_install_error(package, error_msg)
            all_successful = False

    FreeCAD.Console.PrintMessage("--- [FreeCadCopilot] Dependency installation finished ---\n")
    on_finished(all_successful)
