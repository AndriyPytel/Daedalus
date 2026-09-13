"""
The harness directory -- `src/tools/`, the two scripts that drive
`copilot.sdk.Client` from the command line -- is imported by nothing else in
the repository. A stray import of it is caught the same way a stray `core`
import under `Mod/` is (`tests/test_tree_boundary.py`).
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def test_the_harness_directory_holds_exactly_two_scripts():
    from tests.source_tree import tracked

    harness = {path for path in tracked() if path.startswith("src/tools/")}

    assert harness == {"src/tools/run.py", "src/tools/lane.py"}
