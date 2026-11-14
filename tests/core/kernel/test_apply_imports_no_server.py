"""
Level 1 -- no module of `core` but the Kernel's client imports the kernel's server file, and the server imports nothing of a Domain.
"""
import ast
import importlib.util
import os
import pkgutil

from core import freecad_apply
from core.kernel import kernel_worker

SERVER = "core.kernel.kernel_worker"
CLIENT = "core.kernel.kernel"


def _imports_the_server(name):
    with open(importlib.util.find_spec(name).origin, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            named = [module] + [module + "." + alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            named = [alias.name for alias in node.names]
        else:
            continue
        if SERVER in named:
            return True
    return False


def test_apply_imports_no_server():
    package = os.path.dirname(freecad_apply.__file__)
    names = [found.name for found in pkgutil.walk_packages([package], "core.")]

    assert not [name for name in names if name not in (SERVER, CLIENT) and _imports_the_server(name)]


def test_the_kernel_worker_imports_nothing_under_core_agent():
    with open(kernel_worker.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    named = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    named += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    assert not [name for name in named if name and name.startswith("core.agent")]
