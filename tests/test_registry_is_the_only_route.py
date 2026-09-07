"""
"Nothing outside the registry reaches the Document" is an architectural claim,
so it is checked architecturally: by reading the source, not by hoping.

If this fails, either the new code took a shortcut around the Tool registry, or
a module earned its place on ALLOWED and nobody said so.

The Document is reached from exactly these places, and each has a reason:
  - core/freecad/freecad_tools.py: The Tools themselves. This is the sanctioned route.
  - core/kernel/kernel_worker.py: Runs inside the kernel's own process, against its
    own checkpointed Document. It never touches the user's Document.
  - core/freecad/apply.py: Resolves the Document to hand to the transaction; reached
    only through the apply_step Tool.
  - core/sketch_check.py: Builds the reading as a sketch in a document of its
    own, to ask the solver whether what was read can be true at once, and
    closes it in a `finally`. It reads a drawing and never the user's Document.

Reaching the live Document means one of these: ActiveDocument, openDocument,
newDocument, listDocuments, getDocument.
"""
import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = ("core", "ui", "utils", "Mod")

ALLOWED = {
    "core/freecad/freecad_tools.py",
    "core/kernel/kernel_worker.py",
    "core/freecad/apply.py",
    "core/sketch_check.py",
}

DOCUMENT_ACCESS = ("ActiveDocument", "openDocument", "newDocument", "listDocuments", "getDocument")


def python_files():
    for package in PACKAGES:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, package)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in filenames:
                if filename.endswith(".py"):
                    path = os.path.join(dirpath, filename)
                    yield os.path.relpath(path, ROOT).replace(os.sep, "/"), path


def document_access_in(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)
    return sorted({
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in DOCUMENT_ACCESS
    })


@pytest.mark.parametrize("relative,path", list(python_files()), ids=lambda v: v if isinstance(v, str) else "")
def test_only_sanctioned_modules_reach_the_document(relative, path):
    if relative in ALLOWED:
        return
    found = document_access_in(path)
    assert not found, (
        "%s reaches the Document (%s). Go through the Tool registry, or add it "
        "to ALLOWED with a reason." % (relative, ", ".join(found))
    )


def test_every_allowed_module_still_exists():
    """So the allowlist shrinks as the old core goes, instead of rotting."""
    missing = [name for name in ALLOWED if not os.path.exists(os.path.join(ROOT, name))]

    assert not missing, "ALLOWED names modules that are gone: %s" % ", ".join(missing)
