"""Guards the apply/freecad_tools merge — FCC-330.

`core/freecad_tools.py` does not exist as a compatibility shim: every call
site was rewritten to `core.apply` in the same commit. The former cycle was
two in-function imports (`core/freecad_tools.py:152`, `:462`, both
`from core.apply import on_main_thread`) that existed only to dodge the
load-time cycle a top-level import would have created; with one module,
that workaround is impossible by construction, checked here rather than by
eye.
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _in_function_imports_of_apply(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and child.module == "core.apply":
                hits.append(f"{path.name}:{child.lineno}: from core.apply import ...")
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "core.apply":
                        hits.append(f"{path.name}:{child.lineno}: import core.apply")
    return hits


def test_no_import_of_apply_inside_a_function_anywhere_in_core():
    hits = []
    for path in (ROOT / "core").glob("*.py"):
        hits.extend(_in_function_imports_of_apply(path))
    assert hits == []


def test_freecad_tools_is_not_a_compatibility_shim():
    assert not (ROOT / "core" / "freecad_tools.py").exists()


def test_apply_imports_cleanly_with_no_fallback():
    result = subprocess.run(
        [sys.executable, "-c", "import core.apply"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
