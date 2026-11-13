"""
Level 2 -- a run leaves nothing in `$TMPDIR` that it created: FreeCAD backs up
an existing file before it overwrites it, so a reserved-but-empty name must not
exist when `saveCopy` / `saveAs` write it.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import task  # noqa: E402
from core.freecad_apply import discard  # noqa: E402
from core.kernel import copy_of  # noqa: E402

pytestmark = pytest.mark.freecad


def test_a_kernel_copy_and_its_discard_leave_nothing_in_tmpdir(tmp_path, monkeypatch):
    import FreeCAD

    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr("tempfile.tempdir", None)
    doc = FreeCAD.newDocument("TmpdirCopy")
    try:
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        path = copy_of(doc)
        assert os.path.exists(path)
        discard(path)
    finally:
        FreeCAD.closeDocument(doc.Name)
    assert os.listdir(tmp_path) == []


def test_the_edit_document_leaves_no_backup_beside_it(tmp_path, monkeypatch):
    import FreeCAD
    import Part

    step = str(tmp_path / "input.step")
    Part.makeBox(20, 10, 5).exportStep(step)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr("tempfile.tempdir", None)
    path = task.seed_from_step(step)
    try:
        assert os.listdir(scratch) == [os.path.basename(path)]
    finally:
        os.remove(path)
        for doc in list(FreeCAD.listDocuments().values()):
            FreeCAD.closeDocument(doc.Name)
