"""
Level 1 -- the GUI's replay and the Step modules never import the kernel's server file.
"""
import ast

from core import freecad_apply
from core.kernel import step_namespace, step_rules

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
    assert not any(_imports_the_server(module) for module in (freecad_apply, step_namespace, step_rules))
