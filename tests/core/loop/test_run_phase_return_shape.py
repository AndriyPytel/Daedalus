"""
`run_phase` no longer returns a value no caller reads.

The bare `checkpoints` list that replaced it was unpacked at every call
site and read at none; this is the grep-level guard against that
regression. Every phase reaches `run_phase` through `run_declaring` alone.
"""
import os

from tests.source_tree import ROOT, tracked


def _callers():
    for relative in sorted(tracked()):
        if relative.startswith("src/core/") and relative.endswith(".py") \
                and relative != "src/core/agent/agent.py":
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                yield relative, handle.read()


def test_no_call_site_unpacks_run_phase_into_an_unread_checkpoints_list():
    callers = [(relative, source) for relative, source in _callers() if "run_declaring(" in source]
    assert callers
    for relative, source in callers:
        assert "checkpoints" not in source, relative


def test_no_phase_calls_run_phase_around_run_declaring():
    assert [relative for relative, source in _callers() if "run_phase(" in source] == []
