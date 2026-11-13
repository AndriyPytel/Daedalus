import ast
import os

import core.agent

DOCUMENT_CODE = ("core.freecad_apply", "core.document_tools")


def _imported(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            if node.module == "core":
                yield from ("core." + alias.name for alias in node.names)


def test_the_loop_imports_no_document_code():
    """
    The loop reads the Document through the registry it is handed and through the kernel, so a
    module under `core/agent` that imports Apply or the Document Tools has put FreeCAD behind
    the loop's back. The module list comes from the directory.
    """
    directory = os.path.dirname(core.agent.__file__)
    modules = sorted(name for name in os.listdir(directory) if name.endswith(".py"))
    assert modules

    offenders = []
    for name in modules:
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=name)
        offenders += ["%s imports %s" % (name, module) for module in _imported(tree)
                      if module in DOCUMENT_CODE]

    assert not offenders, offenders
