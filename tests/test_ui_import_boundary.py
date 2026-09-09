"""
The UI layer imports nothing from core directly; all capability comes through
copilot.sdk. This boundary means the SDK is the public surface and core can
change freely without breaking the addon.

The harness directory's two scripts drive copilot.sdk.Client the same way any
other caller does, so nothing else in the repository has a reason to reach
into it either; a stray import of that package is caught the same way a stray
core import under ui/ is.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
UI_DIR = os.path.join(SRC, "ui")


def core_imports():
    for path, _, names in os.walk(UI_DIR):
        if "__pycache__" in path:
            continue
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            full = os.path.join(path, name)
            where = os.path.relpath(full, ROOT)
            with open(full, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=full)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "core" or node.module.startswith("core."):
                        yield where, node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "core" or alias.name.startswith("core."):
                            yield where, alias.name


def test_ui_does_not_import_core():
    offenders = ["%s imports %s" % (where, module) for where, module in core_imports()]

    assert not offenders, "ui must not import core.* directly: %s" % ", ".join(offenders)


def _test_sources():
    for path, _, names in os.walk(os.path.join(ROOT, "tests")):
        if "__pycache__" in path:
            continue
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            full = os.path.join(path, name)
            with open(full, encoding="utf-8") as handle:
                yield os.path.relpath(full, ROOT), handle.read()


def harness_imports():
    from itertools import chain
    from tests.source_tree import sources

    for where, source in chain(sources(), _test_sources()):
        tree = ast.parse(source, filename=where)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "tools" or node.module.startswith("tools."):
                    yield where, node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "tools" or alias.name.startswith("tools."):
                        yield where, alias.name


def test_nothing_outside_the_harness_directory_imports_it():
    offenders = ["%s imports %s" % (where, module) for where, module in harness_imports()]

    assert not offenders, "only the harness scripts may import each other: %s" % ", ".join(offenders)
