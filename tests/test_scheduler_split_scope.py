"""
`pytest_xdist_make_scheduler`'s `_split_scope` override, tested directly
against real nodeids rather than through a wall-clock `--durations`
run. Deterministic: no worker startup, no PDF parsing, no timing.
"""
from conftest import pytest_xdist_make_scheduler


class _FakeConfig:
    def __init__(self, dist):
        self._dist = dist

    def getvalue(self, name):
        assert name == "dist"
        return self._dist


def test_split_scope_merges_the_sheet_files_under_loadfile(monkeypatch):
    """
    The files that share `tests/modelmania/*.pdf`
    (test_sheet_reader.py, test_modelmania_evidence.py) must land in one xdist scope so
    `sheet_cache` is the same dict for all of them; an unrelated file keeps
    `loadfile`'s ordinary per-file scope.
    """
    from xdist.scheduler import loadfile

    monkeypatch.setattr(loadfile.LoadFileScheduling, "__init__",
                        lambda self, config, log=None: None)

    scheduler = pytest_xdist_make_scheduler(_FakeConfig("loadfile"), log=None)

    sheet_scopes = {
        scheduler._split_scope(nodeid)
        for nodeid in (
            "tests/test_sheet_reader.py::test_something",
            "tests/test_modelmania_evidence.py::test_other",
        )
    }
    assert sheet_scopes == {"sheets"}

    unrelated = scheduler._split_scope("tests/test_preflight.py::test_unrelated")
    assert unrelated == "tests/test_preflight.py"


def test_no_custom_scheduler_when_dist_is_not_loadfile():
    assert pytest_xdist_make_scheduler(_FakeConfig("each"), log=None) is None
