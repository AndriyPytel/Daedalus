"""
The Python packages the addon needs, installed into FreeCAD's own directory
for user-installed packages. pymupdf renders a PDF page at the DPI its own
callouts ask for and gives an exact text layer, without which `sheet_reader`
sends the sheet whole; PyYAML is pinned here explicitly, though litellm
already pulls it in, so a reading does not silently stop being written months
after a transitive dependency moves it. `pip install --target` without
`--upgrade` writes new dist-info beside a file it refuses to overwrite,
leaving two installs' metadata describing what is now one install, which
`prune_superseded` repairs.
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
    Leave one version of each distribution: the newest. A superseded
    install's files are removed only where the surviving version does not
    claim them too, since `--upgrade` has already overwritten the shared
    ones and deleting those would break the install this is repairing, and a
    path a RECORD claims outside the target directory is left alone. Returns
    the dist-info directory names removed.
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
                    pass  # except-pass-allowed: best-effort cleanup, rmtree after covers the rest
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
    Installs a Python package using pip, targeting a specific directory,
    through `freecad.utils.get_python_exe()` so the right interpreter is used
    inside a packaged environment like Flatpak or AppImage. Called
    synchronously before the dock and preference pages load, so a hung `pip
    install` freezes FreeCAD before any of it appears. The 120s ceiling is
    pip's own default connect/read timeout doubled, room for a slow mirror
    without leaving a stalled one to block startup indefinitely.
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
    Checks for and installs missing dependencies: identifies missing
    packages, asks the user for permission, installs them, and recommends a
    restart when done. Superseded versions are pruned before the missing
    check, because a directory that already has everything is exactly the
    one an earlier layered install left behind and returning early would
    never repair it; caches are invalidated after each install so the import
    system sees the new package. *confirm* asks whether to install *missing*
    and returns a bool, defaulting to decline since this module has no Qt
    import; *on_install_error* takes `(package, error_message)` and
    *on_finished* takes `all_successful`, both defaulting to a no-op.
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
            "[Daedalus] Removed %d superseded package version(s): %s\n"
            % (len(superseded), ", ".join(superseded)))

    missing = missing_packages()

    if not missing:
        FreeCAD.Console.PrintMessage("[Daedalus] All required packages are installed.\n")
        return

    if not confirm(missing):
        FreeCAD.Console.PrintMessage(
            "[Daedalus] Required packages were not installed: %s\n" % ", ".join(missing))
        return

    FreeCAD.Console.PrintMessage("--- [Daedalus] Starting dependency installation ---\n")
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

    FreeCAD.Console.PrintMessage("--- [Daedalus] Dependency installation finished ---\n")
    on_finished(all_successful)
