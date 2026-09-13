"""
No package `__init__.py` under `src/` is a docstring and nothing else.

`core/freecad`'s was exactly that -- a docstring naming `apply` and `traps`,
grouping nothing a caller reached through the package. Its modules moved up
to `core/freecad_apply.py` and `core/freecad_traps.py` and the package went.
Every package that stayed groups something real: an explicit re-export
(`design`, `agent`, `kernel`, `loop`) or an explicit statement of which
submodules it loads together and why (`sheets`).
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def _is_docstring_only(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    if not tree.body:
        return False
    first_is_docstring = (
        isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    )
    return first_is_docstring and len(tree.body) == 1


def test_no_package_init_is_only_a_docstring():
    offenders = []
    for path, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        if "__init__.py" in filenames:
            full = os.path.join(path, "__init__.py")
            if _is_docstring_only(full):
                offenders.append(os.path.relpath(full, ROOT))
    assert offenders == []
