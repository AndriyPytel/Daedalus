"""
The engine -- `core` and `sdk.py` -- runs with no Qt at all, not
even a deferred import, and never reaches back into `bridge` or `ui`:
`on_main_thread` -- the one PySide import the engine still carried, deferred
inside a function so headless callers never paid for it -- moved to
`Mod/bridge/main_thread.py`, so the check below no longer needs a
module-scope exception.

Everything else -- `Mod/ui`, `src/tools`, `harness`, `deploy` -- reaches the
engine through `sdk` alone; `Mod/bridge` is the one sanctioned exception, the
Qt-touching layer built to carry a `core` setting or a model list across the
boundary as a Qt signal.
"""
import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
ENGINE_DIRS = ("core",)
ENGINE_MODULES = ("sdk.py",)
SANCTIONED_PREFIXES = ("src/core/", "Mod/bridge/", "tests/", "manual/")
SANCTIONED_FILES = ("src/sdk.py", "InitGui.py")


def _py_files(base):
    for path, _, names in os.walk(base):
        if "__pycache__" in path:
            continue
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(path, name)


def _engine_files():
    for dirname in ENGINE_DIRS:
        yield from _py_files(os.path.join(SRC, dirname))
    for name in ENGINE_MODULES:
        yield os.path.join(SRC, name)


def _all_imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_the_engine_imports_no_qt_anywhere():
    offenders = []
    for full in _engine_files():
        where = os.path.relpath(full, ROOT)
        with open(full, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=full)
        for module in _all_imports(tree):
            if module == "PySide" or module.startswith("PySide"):
                offenders.append("%s imports %s" % (where, module))

    assert not offenders, "the engine must not require Qt, deferred or not: %s" % ", ".join(offenders)


def test_the_engine_does_not_reach_back_into_bridge_or_mod():
    offenders = []
    for full in _engine_files():
        where = os.path.relpath(full, ROOT)
        with open(full, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=full)
        for module in _all_imports(tree):
            if module in ("bridge", "ui") or module.startswith(("bridge.", "ui.")):
                offenders.append("%s imports %s" % (where, module))

    assert not offenders, "the engine must not import Mod-side code: %s" % ", ".join(offenders)


def _imports_core(source):
    return any(module == "core" or module.startswith("core.")
               for module in _all_imports(ast.parse(source)))


def callers_of_core_outside_the_sanctioned_places(root):
    """
    Every tracked `.py` under *root* that imports `core`, outside `src/core`, `src/sdk.py` and `Mod/bridge`.
    `tests` and `manual` are exempt because they exercise the engine itself, and `InitGui.py` because it runs the dependency check before `sdk` can import numpy.
    The tree comes from `git ls-files`, so a directory created later is scanned the day it is tracked and a virtualenv or untracked scratch is never read.
    """
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=root, capture_output=True,
                            text=True, check=True).stdout.split("\n")
    offenders = []
    for path in filter(None, listed):
        if path.startswith(SANCTIONED_PREFIXES) or path in SANCTIONED_FILES:
            continue
        with open(os.path.join(root, path), encoding="utf-8") as handle:
            if _imports_core(handle.read()):
                offenders.append(path)
    return offenders


def scanned_directories(root):
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=root, capture_output=True,
                            text=True, check=True).stdout.split("\n")
    return {os.path.dirname(path) for path in filter(None, listed)
            if not path.startswith(SANCTIONED_PREFIXES) and path not in SANCTIONED_FILES}


def test_only_the_sdk_and_the_bridge_import_core():
    offenders = callers_of_core_outside_the_sanctioned_places(ROOT)

    assert not offenders, (
        "outside src/core, src/sdk.py and Mod/bridge the engine is reached through sdk: %s"
        % ", ".join(offenders))


def test_the_scan_reads_the_directories_of_the_tree(tmp_path):
    assert {"Mod/ui", "src/tools", "harness"} <= scanned_directories(ROOT)

    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "later").mkdir()
    (tmp_path / "later" / "caller.py").write_text("from core import settings\n")
    (tmp_path / "later" / "clean.py").write_text("import sdk\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "site.py").write_text("import core\n")
    subprocess.run(["git", "add", "later"], cwd=tmp_path, check=True)

    assert callers_of_core_outside_the_sanctioned_places(str(tmp_path)) == ["later/caller.py"]


def test_importing_sdk_loads_neither_manifold_nor_topology_nor_measure():
    code = ("import sys; sys.path.insert(0, %r); import sdk; "
            "print([m for m in ('core.manifold', 'core.topology', 'core.measure') if m in sys.modules])"
            % SRC)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert result.stdout.strip().endswith("[]"), result.stdout + result.stderr


def test_initgui_installs_only_the_dock_and_preferences():
    """The workbench registration is gone; nothing replaces it."""
    path = os.path.join(ROOT, "InitGui.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    addon_imports = {module for module in _all_imports(tree)
                      if module.startswith(("Mod", "ui", "Mod.ui"))}

    assert addon_imports == {"Mod", "ui"}, addon_imports
