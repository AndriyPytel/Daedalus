"""
The engine -- `core` and `sdk.py` -- runs with no Qt at all, not
even a deferred import, and never reaches back into `bridge` or `ui`:
`on_main_thread` -- the one PySide import the engine still carried, deferred
inside a function so headless callers never paid for it -- moved to
`Mod/bridge/main_thread.py`, so the check below no longer needs a
module-scope exception.

`Mod` -- `ui`, `DaedalusPreferences.py` -- is FreeCAD
and Qt code, and reaches the engine through `sdk` alone; `bridge` is
the one sanctioned exception, the Qt-touching layer built to carry a
`core` setting or a model list across the boundary as a Qt signal.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
MOD = os.path.join(ROOT, "Mod")
ENGINE_DIRS = ("core",)
ENGINE_MODULES = ("sdk.py",)
MOD_NON_BRIDGE_DIRS = ("ui",)
MOD_ROOT_FILES = ("DaedalusPreferences.py",)


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


def _mod_non_bridge_files():
    for dirname in MOD_NON_BRIDGE_DIRS:
        yield from _py_files(os.path.join(MOD, dirname))
    for name in MOD_ROOT_FILES:
        yield os.path.join(MOD, name)


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


def test_mod_reaches_the_engine_only_through_the_sdk():
    offenders = []
    for full in _mod_non_bridge_files():
        if not os.path.exists(full):
            continue
        where = os.path.relpath(full, ROOT)
        with open(full, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=full)
        for module in _all_imports(tree):
            if module == "core" or module.startswith("core."):
                offenders.append("%s imports %s" % (where, module))

    assert not offenders, (
        "Mod code outside bridge/ must cross into the engine only through "
        "sdk, bridge/ being the other sanctioned route: %s" % ", ".join(offenders))


def test_initgui_installs_only_the_dock_and_preferences():
    """The workbench registration is gone; nothing replaces it."""
    path = os.path.join(ROOT, "InitGui.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    addon_imports = {module for module in _all_imports(tree)
                      if module.startswith(("Mod", "ui", "Mod.ui"))}

    assert addon_imports == {"Mod", "ui"}, addon_imports
