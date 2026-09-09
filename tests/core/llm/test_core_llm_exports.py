"""
Every name a caller imports from `core.llm` actually resolves (FCC-351).

The package used to keep two statements of its own surface: the lazy
`__getattr__`'s factory names, and a hand-maintained `__all__` list of 27.
Nothing does `from core.llm import *`, so `__all__` is gone; this is the
statement that replaces it, derived from the callers instead of repeated
beside them.
"""
import ast
import importlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _imported_names():
    names = set()
    for base in ("src", "tests"):
        for path, _dirs, files in os.walk(os.path.join(ROOT, base)):
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(path, filename)
                with open(full, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=full)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == "core.llm":
                        names.update(alias.name for alias in node.names)
    return names


def test_every_name_imported_from_core_llm_resolves():
    core_llm = importlib.import_module("core.llm")
    for name in _imported_names():
        assert hasattr(core_llm, name), name
