"""
Level 1 — DAEDALUS_DESIGN_WHOLE_SHEET gives the Design phase the sheet as one picture.
"""
import os

import pytest
from core.loop import design as design_module
from core.loop.design import WHOLE_SHEET, _the_groups, design
from core.sheets import sheet_reader
from tests import modelmania_harness

SHEET = os.path.join(str(modelmania_harness.MODELMANIA_DIR), "model-mania-2024.pdf")

needs_the_sheet = pytest.mark.skipif(
    not sheet_reader.available() or not os.path.exists(SHEET),
    reason="PyMuPDF or the Model Mania drawing is missing")


def _pictures_sent(monkeypatch):
    sent = []

    def declaring(contract, inputs, llm, task, **kwargs):
        sent.extend(block for block in task if block.get("type") != "text")
        return {}, None

    monkeypatch.setattr(design_module, "run_declaring", declaring)
    design({"intent": "x"}, object(), None, [SHEET])
    return sent


@needs_the_sheet
def test_the_whole_sheet_is_one_picture_where_it_was_cut_into_views(monkeypatch):
    monkeypatch.delenv(WHOLE_SHEET, raising=False)
    assert len(_the_groups([SHEET])) > 0
    assert len(_pictures_sent(monkeypatch)) > 1

    monkeypatch.setenv(WHOLE_SHEET, "1")
    assert _the_groups([SHEET]) == []
    assert len(_pictures_sent(monkeypatch)) == 1
