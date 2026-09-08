"""Guards the settings/llm cycle fix — FCC-337.

`core/settings.py` and `core/llm` used to have a two-way import dodged by
function-body imports (`settings.py:172` for `llm.DEFAULT_ROLE_TABLE`, several
lines in `llm.py` for `settings.instance()`). The fix is structural:
`DEFAULT_ROLE_TABLE` lives in `core/llm/defaults.py`, and `settings.py` imports
it at top level without triggering the rest of the package. The factory module
imports settings at top level but is lazy-loaded via `__getattr__`, so the
cycle is gone by construction rather than dodged.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _function_body_imports_of(path: Path, target_names: tuple) -> list:
    """Find imports of *target_names* inside function bodies."""
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom):
                module = child.module or ""
                for alias in child.names:
                    full = f"{module}.{alias.name}" if module else alias.name
                    if any(full.startswith(t) or full == t or alias.name in target_names
                           for t in target_names):
                        hits.append(f"{path.name}:{child.lineno}: from {module} import {alias.name}")
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if any(alias.name.startswith(t) for t in target_names):
                        hits.append(f"{path.name}:{child.lineno}: import {alias.name}")
    return hits


def test_settings_has_no_function_body_import_of_llm():
    """settings.py should import from llm.defaults at top level, not in a function."""
    path = SRC / "core" / "settings.py"
    hits = _function_body_imports_of(path, ("core.llm", "llm"))
    assert hits == [], f"function-body imports of llm in settings.py: {hits}"


def test_factory_has_top_level_import_of_settings():
    """factory.py should import settings at top level since it's lazy-loaded."""
    path = SRC / "core" / "llm" / "factory.py"
    tree = ast.parse(path.read_text(), filename=str(path))

    has_top_level_settings_import = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "core" and any(alias.name == "settings" for alias in node.names):
                has_top_level_settings_import = True
                break

    assert has_top_level_settings_import, "factory.py should import settings at top level"

    hits = _function_body_imports_of(path, ("core.settings", "settings"))
    assert hits == [], f"factory.py should have no function-body imports of settings: {hits}"


def test_defaults_does_not_import_settings_or_llm_modules():
    """defaults.py should be import-free from the cycle participants."""
    path = SRC / "core" / "llm" / "defaults.py"
    if not path.exists():
        return
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("core.settings") or module.startswith("core.llm"):
                if module != "core.llm.defaults":
                    hits.append(f"defaults.py:{node.lineno}: from {module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("core.settings") or alias.name.startswith("core.llm"):
                    hits.append(f"defaults.py:{node.lineno}: import {alias.name}")
    assert hits == [], f"defaults.py should not import settings or other llm modules: {hits}"


def test_llm_py_file_no_longer_exists():
    """core/llm.py should be replaced by the core/llm/ package."""
    assert not (SRC / "core" / "llm.py").exists(), "core/llm.py should not exist"
    assert (SRC / "core" / "llm" / "__init__.py").exists(), "core/llm/ package should exist"
