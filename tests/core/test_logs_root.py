"""Run-evidence paths derive from one root, never the process's cwd — FCC-358."""
import importlib
from pathlib import Path

import pytest

from core import logs_root
from core.design import part_file
from core.loop import execute


ROOT = Path(logs_root.__file__).resolve().parents[2]


def test_logs_root_is_the_repo_root_not_wherever_the_process_started():
    assert logs_root.LOGS == str(ROOT / "logs")


@pytest.mark.parametrize("module", [part_file, execute])
def test_a_run_started_from_another_directory_writes_under_repo_logs_not_src(monkeypatch, tmp_path, module):
    monkeypatch.chdir(tmp_path)
    importlib.reload(module)
    try:
        path = module.WHERE if module is part_file else module.TYPE_CACHE_PATH
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(ROOT / "logs")
        assert not resolved.is_relative_to(ROOT / "src")
    finally:
        importlib.reload(module)
