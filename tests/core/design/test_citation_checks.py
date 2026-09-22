"""Level 1 -- a `from` field checked against the sheet it claims to quote.

Fed the mm2024-phase1 sheet's own text layer, per FCC-426: `40` appears
bare at (586,412), `R8` and `4X` are both printed, and nothing on the
sheet is ever parenthesised.
"""
import os

import pytest

from core.design.citation_checks import fabricated_citation
from tests import modelmania_harness

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

SHEET = modelmania_harness.PHASES["mm2024-phase1.pdf"]

needs_pymupdf = pytest.mark.skipif(pymupdf is None, reason="PyMuPDF is not installed")


def _skip_unless_present():
    if not os.path.exists(SHEET):
        pytest.skip("sheet not found: %s; set FCC_MODELMANIA_DIR" % SHEET)


@pytest.fixture(scope="module")
def sheet_words():
    _skip_unless_present()
    document = pymupdf.open(str(SHEET))
    try:
        return [word[4] for page in document for word in page.get_text("words")]
    finally:
        document.close()


def _read(value, citation):
    return {"value": value, "flag": "read", "from": citation}


@needs_pymupdf
def test_a_fabricated_parenthesised_citation_is_flagged(sheet_words):
    model = {"envelope": {"height": _read(
        "40 mm", "SECTION B-B, the (40) reference dimension")}}

    found = fabricated_citation(model, sheet_words)

    assert len(found) == 1
    assert found[0]["about"] == "envelope → height"
    assert "(40)" in found[0]["stands"]


@needs_pymupdf
def test_an_honest_citation_is_not_flagged(sheet_words):
    model = {"envelope": {"height": _read("60 mm", "the top view, R8 4X")}}

    assert fabricated_citation(model, sheet_words) == []


@needs_pymupdf
def test_only_the_quoted_marks_are_checked_not_the_surrounding_prose(sheet_words):
    model = {"envelope": {"height": _read(
        "60 mm", "a description this sheet never printed, R8 4X")}}

    assert fabricated_citation(model, sheet_words) == []


@needs_pymupdf
def test_a_refused_citation_still_carries_its_reading(sheet_words):
    node = _read("40 mm", "SECTION B-B, the (40) reference dimension")
    model = {"envelope": {"height": node}}

    found = fabricated_citation(model, sheet_words)

    assert model["envelope"]["height"] is node
    assert model["envelope"]["height"]["value"] == "40 mm"
    assert found[0]["kind"] == "conflict"
    assert found[0]["machine_written"] is True
