"""
Level 3 -- the vendored CADGenBench scorer, pinned against the fixtures the
manager measured: an Exemplar scored against itself is exactly `1.0000`, and
two named cross-year pairs pin the scale so a scorer upgrade that moves it
is caught rather than silently absorbed.
"""
import pytest

from tests import modelmania_harness

pytestmark = pytest.mark.cadgenbench

TOLERANCE = 0.0005


def _score(candidate_stem, exemplar_stem):
    import sys
    import os

    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(ROOT, "src", "tools"))
    import score as score_module

    candidate = modelmania_harness.exemplar_step(candidate_stem)
    exemplar = modelmania_harness.exemplar_step(exemplar_stem)
    return score_module.score(str(candidate), str(exemplar))


def _require_exemplar_dir():
    if not modelmania_harness.EXEMPLAR_DIR.is_dir():
        pytest.skip("modelmania exemplars not found; set FCC_MODELMANIA_DIR")


def test_an_exemplar_scored_against_itself_is_exactly_one():
    _require_exemplar_dir()

    result = _score("mm2025-phase1", "mm2025-phase1")

    assert result["is_valid"] is True
    assert result["is_watertight"] is True
    assert result["score"] == pytest.approx(1.0000, abs=TOLERANCE)


def test_mm2025_phase1_against_phase2_is_pinned():
    _require_exemplar_dir()

    result = _score("mm2025-phase1", "mm2025-phase2")

    assert result["score"] == pytest.approx(0.6779, abs=TOLERANCE)


def test_mm2023_phase1_against_mm2024_phase1_is_pinned():
    _require_exemplar_dir()

    result = _score("mm2023-phase1", "mm2024-phase1")

    assert result["score"] == pytest.approx(0.1613, abs=TOLERANCE)
