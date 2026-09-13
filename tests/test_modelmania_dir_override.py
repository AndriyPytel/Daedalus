"""Guards the FCC_MODELMANIA_DIR override."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_modelmania_evidence_skips_cleanly_without_the_directory(tmp_path):
    empty_dir = tmp_path / "no-modelmania-here"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/core/level2/test_modelmania_evidence.py", "-v"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "FCC_MODELMANIA_DIR": str(empty_dir)},
    )
    assert "SKIPPED" in result.stdout, result.stdout
    assert "FAILED" not in result.stdout, result.stdout
