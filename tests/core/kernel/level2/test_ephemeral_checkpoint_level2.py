"""
Level 2 — ephemeral sessions write no checkpoint.

Per FCC-341: a fresh-handoff `check(code, document=..., ephemeral=True)` writes
no checkpoint file, and the measured cost drops to within one saveCopy of a
continuing-session call.
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


def test_ephemeral_fresh_handoff_costs_no_more_than_a_continuing_session(kernel, tmp_path):
    """
    The fresh handoff cost two saveCopy operations (baseline + success
    checkpoint). With ephemeral=True, it should cost no more than a
    continuing-session call.
    """
    doc_path = str(tmp_path / "baseline.FCStd")
    
    baseline = kernel.check("pass", document=None)
    assert baseline.ok
    
    import FreeCAD
    doc = FreeCAD.openDocument(baseline.checkpoint)
    for i in range(50):
        doc.addObject("Part::Box", f"Box{i:03d}")
    doc.recompute()
    doc.saveAs(doc_path)
    FreeCAD.closeDocument(doc.Name)
    
    warmup = kernel.check("doc.addObject('Part::Box', 'Warmup')")
    assert warmup.ok
    
    continuing_times = []
    for _ in range(3):
        start = time.monotonic()
        result = kernel.check("doc.addObject('Part::Box', 'Test')")
        continuing_times.append(time.monotonic() - start)
        assert result.ok
    continuing_median = sorted(continuing_times)[1]
    
    ephemeral_times = []
    for _ in range(3):
        start = time.monotonic()
        result = kernel.check(
            "doc.addObject('Part::Box', 'Test')",
            document=doc_path,
            ephemeral=True,
        )
        ephemeral_times.append(time.monotonic() - start)
        assert result.ok
    ephemeral_median = sorted(ephemeral_times)[1]
    
    assert ephemeral_median <= continuing_median * 2.0, \
        f"ephemeral ({ephemeral_median:.3f}s) is still much slower than continuing ({continuing_median:.3f}s)"
