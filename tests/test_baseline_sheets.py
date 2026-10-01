"""The 8 sheets the baseline measurement and the comparison run must share."""
from tests import modelmania_harness


def test_baseline_sheets_names_exactly_eight_paths_under_the_modelmania_dir():
    sheets = modelmania_harness.baseline_sheets()

    assert len(sheets) == 8
    assert all(str(path).startswith(str(modelmania_harness.MODELMANIA_DIR)) for path in sheets)


def test_baseline_sheets_resolve_to_real_files_when_the_directory_is_present():
    if not modelmania_harness.MODELMANIA_DIR.is_dir():
        import pytest
        pytest.skip("modelmania sheets not found; set DAE_MODELMANIA_DIR")

    sheets = modelmania_harness.baseline_sheets()

    missing = [str(path) for path in sheets if not path.is_file()]
    assert missing == []
