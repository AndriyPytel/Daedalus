"""
`run_phase` no longer returns a value no caller reads.

The bare `checkpoints` list that replaced it was unpacked at every call
site and read at none; this is the grep-level guard against that
regression, so a future call site cannot bring the dead name back without
this test failing.
"""
import os

from tests.source_tree import ROOT

CALL_SITES = (
    "src/core/loop/plan.py",
    "src/core/loop/execute.py",
    "src/core/loop/design.py",
    "src/core/loop/assertion.py",
    "src/core/loop/review.py",
)


def test_no_call_site_unpacks_run_phase_into_an_unread_checkpoints_list():
    for relative in CALL_SITES:
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            source = handle.read()
        assert "checkpoints" not in source, relative
        assert "declarations = run_phase(" in source, relative
