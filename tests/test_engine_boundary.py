"""
The engine -- `core` and `copilot` (the SDK) -- runs with no Qt, and never
reaches back into `bridge`, the Qt-touching layer FCC-353 built for `ui` and
`Mod`'s own preferences and panel. FCC-359 closed the two reach-backs this
used to have left open (`core/llm/factory.py`, `core/settings.py`) and took
the one true Qt import (`core/dependency_manager.py`'s `QMessageBox`) out of
module scope entirely -- a GUI supplies those dialogs as callbacks instead.

`core/freecad_apply.py`'s PySide import stays: it is deferred inside
`on_main_thread`, the marshaller a live GUI's Document mutations need, and
never runs unless a Qt application is already up -- `copilot.sdk.Client`'s
own `dispatch` replaces it for every headless caller, which is why the
engine can still be imported and run with no PySide installed at all.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
ENGINE_DIRS = ("core", "copilot")


def _py_files():
    for dirname in ENGINE_DIRS:
        base = os.path.join(SRC, dirname)
        for path, _, names in os.walk(base):
            if "__pycache__" in path:
                continue
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(path, name)


def _module_scope_imports(tree):
    """Import names reachable without calling a function -- a deferred
    import inside `def` is not one of them."""
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        else:
            stack.extend(ast.iter_child_nodes(node))


def _all_imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_the_engine_imports_no_qt_at_module_scope():
    offenders = []
    for full in _py_files():
        where = os.path.relpath(full, ROOT)
        with open(full, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=full)
        for module in _module_scope_imports(tree):
            if module == "PySide" or module.startswith("PySide"):
                offenders.append("%s imports %s" % (where, module))

    assert not offenders, "the engine must not require Qt to be imported: %s" % ", ".join(offenders)


def test_the_engine_does_not_reach_back_into_bridge():
    offenders = []
    for full in _py_files():
        where = os.path.relpath(full, ROOT)
        with open(full, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=full)
        for module in _all_imports(tree):
            if module == "bridge" or module.startswith("bridge."):
                offenders.append("%s imports %s" % (where, module))

    assert not offenders, "the engine must not import bridge: %s" % ", ".join(offenders)
