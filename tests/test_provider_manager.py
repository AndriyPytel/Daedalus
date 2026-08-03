"""
The Providers page test button reached for vendor SDKs that this addon never
installs — `google.generativeai` is not a dependency, so "Test" on a Google key
died with a ModuleNotFoundError and Google looked broken. OpenAI and Anthropic
only worked by accident, off packages litellm happens to pull in.

Checked by parsing, because `utils.provider_manager` imports PySide and so is
not importable outside FreeCAD.
"""
import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VENDOR_SDKS = {"openai", "anthropic", "google"}


@pytest.mark.parametrize("module", [
    os.path.join("utils", "provider_manager.py"),
    os.path.join("core", "model_catalog.py"),
])
def test_no_vendor_sdk_imports(module):
    path = os.path.join(ROOT, module)
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not (imported & VENDOR_SDKS), (
        "%s imports undeclared vendor SDKs: %s" % (module, sorted(imported & VENDOR_SDKS))
    )
