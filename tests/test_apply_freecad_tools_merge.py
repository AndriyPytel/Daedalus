"""Guards the apply/freecad_tools merge — FCC-330.

The former cycle was two in-function imports (`core/freecad_tools.py:152`,
`:462`) that existed only to dodge the load-time cycle a top-level
`apply <-> freecad_tools` import would have created. Module-level imports of
either name are unrelated (e.g. `core.loop`'s `from core.freecad_tools import
TRAPS`, or `core/freecad_tools.py`'s own re-export) and are not the defect
this test targets.
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TARGETS = {"core.apply", "core.freecad_tools"}


def _in_function_imports_of(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and child.module in _TARGETS:
                hits.append(f"{path.name}:{child.lineno}: from {child.module} import ...")
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name in _TARGETS:
                        hits.append(f"{path.name}:{child.lineno}: import {alias.name}")
    return hits


def test_no_import_inside_a_function_between_apply_and_freecad_tools():
    hits = []
    for path in (ROOT / "core").glob("*.py"):
        hits.extend(_in_function_imports_of(path))
    assert hits == []


def test_apply_and_freecad_tools_import_cleanly_with_no_fallback():
    result = subprocess.run(
        [sys.executable, "-c", "import core.apply; import core.freecad_tools"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
