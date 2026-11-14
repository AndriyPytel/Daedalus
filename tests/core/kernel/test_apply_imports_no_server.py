"""
Level 1 -- the GUI's replay and the Step modules never import the kernel's server file, and the server imports nothing of a Domain.
"""
import ast

from core import freecad_apply
from core.agent import part_rules
from core.kernel import kernel_worker, step_namespace

SERVER = "core.kernel.kernel_worker"


def _imports_the_server(module):
    with open(module.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            named = [node.module] + [node.module + "." + alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            named = [alias.name for alias in node.names]
        else:
            continue
        if SERVER in named:
            return True
    return False


def test_apply_imports_no_server():
    assert not any(_imports_the_server(module) for module in (freecad_apply, step_namespace, part_rules))


def test_the_kernel_worker_imports_nothing_under_core_agent():
    with open(kernel_worker.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    named = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    named += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    assert not [name for name in named if name and name.startswith("core.agent")]
