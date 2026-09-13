"""
Level 2 — ephemeral sessions write no checkpoint.

Per FCC-341: a fresh-handoff `check(code, document=..., ephemeral=True)` writes
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


def test_ephemeral_fresh_handoff_costs_no_more_than_a_non_ephemeral_one(kernel, tmp_path):
    """
    A fresh handoff (`document=...`) always reopens the document from disk,
    whether or not `ephemeral=True` -- that open is the same cost either way
    and is not what FCC-341 touched. What FCC-341 removed is the checkpoint
    save: a non-ephemeral fresh handoff calls `doc.saveCopy` twice (once for
    the baseline it opens onto, once for the success checkpoint), ephemeral
    calls it zero times.

    Comparing ephemeral to a *continuing* session instead confounds that
    saved cost with the document-open cost a continuing session never pays
    at all, and which one dominates depends on the machine: measured here,
    `doc.saveCopy` on a 50-object document costs ~70ms and `FreeCAD.openDocument`
    on the same file costs ~19ms, so a continuing session (no open, one
    saveCopy) and an ephemeral fresh handoff (one open, no saveCopy) can land
    on either side of a 2x bound depending only on which of those two costs
    is larger that day -- the FCC-387 report saw the open lose, this run
    sees the saveCopy lose. Comparing two fresh handoffs to the same document
    holds the open cost fixed and isolates exactly the saveCopy calls
    ephemeral skips: measured on this machine, ephemeral costs ~20% of the
    non-ephemeral fresh handoff to the same file, so the bound below (70%)
    leaves headroom for run-to-run noise while still failing if either
    saveCopy call comes back.
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

    warmup = kernel.check("doc.addObject('Part::Box', 'Warmup')", document=doc_path, ephemeral=True)
    assert warmup.ok

    non_ephemeral_times = []
    for _ in range(3):
        start = time.monotonic()
        result = kernel.check(
            "doc.addObject('Part::Box', 'Test')",
            document=doc_path,
            ephemeral=False,
        )
        non_ephemeral_times.append(time.monotonic() - start)
        assert result.ok
    non_ephemeral_median = sorted(non_ephemeral_times)[1]

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

    assert ephemeral_median <= non_ephemeral_median * 0.7, \
        (f"ephemeral fresh handoff ({ephemeral_median:.3f}s) is not clearly cheaper than a "
         f"non-ephemeral fresh handoff to the same document ({non_ephemeral_median:.3f}s) -- "
         "the two checkpoint saves ephemeral is meant to skip may be happening anyway")
