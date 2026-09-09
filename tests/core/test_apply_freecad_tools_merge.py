"""Guards the apply/freecad_tools merge — FCC-330.

`core/freecad/freecad_tools.py` does not exist as a compatibility shim: every
call site was rewritten to `core.freecad_apply` in the same commit. The former
cycle was two in-function imports (`core/freecad/freecad_tools.py:152`, `:462`,
both `from core.freecad_apply import on_main_thread`) that existed only to
dodge the load-time cycle a top-level import would have created; with one
module, that workaround is impossible by construction, checked here rather
than by eye. A deferred import of `core.freecad_apply` for an unrelated
reason (e.g. `core/freecad_traps.py`, load-order) is not what this guards
against — only the old flat names the merge retired.
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"

OLD_NAMES = ("core.apply", "core.freecad_tools")


def _in_function_imports_of_old_names(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and child.module in OLD_NAMES:
                hits.append(f"{path.name}:{child.lineno}: from {child.module} import ...")
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name in OLD_NAMES:
                        hits.append(f"{path.name}:{child.lineno}: import {alias.name}")
    return hits


def test_no_import_of_the_old_flat_names_inside_a_function_anywhere_in_core():
    hits = []
    for path in SRC.joinpath("core").rglob("*.py"):
        hits.extend(_in_function_imports_of_old_names(path))
    assert hits == []


def test_freecad_tools_is_not_a_compatibility_shim():
    assert not (SRC / "core" / "freecad" / "freecad_tools.py").exists()
    assert not (SRC / "core" / "freecad_tools.py").exists()
    assert not (SRC / "core" / "apply.py").exists()


def test_apply_imports_cleanly_with_no_fallback():
    result = subprocess.run(
        [sys.executable, "-c", "import core.freecad_apply"],
        cwd=SRC, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
