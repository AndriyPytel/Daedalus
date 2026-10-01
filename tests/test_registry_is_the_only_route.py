"""
"Nothing outside the registry reaches the Document" is an architectural claim,
so it is checked architecturally: by reading the source, not by hoping.

If this fails, either the new code took a shortcut around the Tool registry, or
a module earned its place on ALLOWED and nobody said so.

The Document is reached from exactly these places, and each has a reason:
  - core/freecad_apply.py: The Tools themselves, and where the transaction resolves
    the Document to hand it to. This is the sanctioned route.
  - core/kernel/kernel_worker.py: Runs inside the kernel's own process, against its
    own checkpointed Document. It never touches the user's Document.
  - core/sheets/sketch_check.py: Builds the reading as a sketch in a document of its
    own, to ask the solver whether what was read can be true at once, and
    closes it in a `finally`. It reads a drawing and never the user's Document.
  - src/sdk.py: `Client` owns the Document a caller hands it, opening the
    path or making the empty one the whole run is then applied to. It is the
    assembly point, above the registry rather than around it.

Reaching the live Document means one of these: ActiveDocument, openDocument,
newDocument, listDocuments, getDocument.
"""
import ast
import os

import pytest

from tests.source_tree import ROOT, sources

ALLOWED = {
    "src/core/freecad_apply.py",
    "src/core/kernel/kernel_worker.py",
    "src/core/sheets/sketch_check.py",
    "src/sdk.py",
}

DOCUMENT_ACCESS = ("ActiveDocument", "openDocument", "newDocument", "listDocuments", "getDocument")


def document_access_in(source, path):
    return sorted({
        node.attr for node in ast.walk(ast.parse(source, path))
        if isinstance(node, ast.Attribute) and node.attr in DOCUMENT_ACCESS
    })


@pytest.mark.parametrize("relative,source", list(sources()), ids=lambda v: v if v.endswith(".py") else "")
def test_only_sanctioned_modules_reach_the_document(relative, source):
    if relative in ALLOWED:
        return
    found = document_access_in(source, relative)
    assert not found, (
        "%s reaches the Document (%s). Go through the Tool registry, or add it "
        "to ALLOWED with a reason." % (relative, ", ".join(found))
    )


def test_every_allowed_module_still_exists():
    """So the allowlist shrinks as the old core goes, instead of rotting."""
    missing = [name for name in ALLOWED if not os.path.exists(os.path.join(ROOT, name))]

    assert not missing, "ALLOWED names modules that are gone: %s" % ", ".join(missing)
