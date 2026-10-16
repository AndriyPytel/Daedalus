"""
Level 2 — ephemeral sessions write no checkpoint.

A fresh-handoff `check(code, document=..., ephemeral=True)` writes
no checkpoint file, and costs less than the same fresh handoff without
`ephemeral=True` to the same document.
"""
import os
import time

import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_an_ephemeral_fresh_handoff_writes_no_checkpoint_file(kernel, tmp_path):
    doc_path = str(tmp_path / "baseline.FCStd")
    
    baseline = kernel.check("pass", document=None)
    assert baseline.ok, baseline.traceback
    assert baseline.checkpoint
    
    import FreeCAD
    doc = FreeCAD.openDocument(baseline.checkpoint)
    doc.saveAs(doc_path)
    FreeCAD.closeDocument(doc.Name)
    
    checkpoint_before = set(tmp_path.glob("*.FCStd"))
    
    result = kernel.check(
        "doc.addObject('Part::Box', 'Box')",
        document=doc_path,
        ephemeral=True,
    )
    
    checkpoint_after = set(tmp_path.glob("*.FCStd"))
    
    assert result.ok, result.traceback
    assert checkpoint_after == checkpoint_before, \
        "ephemeral call wrote a checkpoint file"
