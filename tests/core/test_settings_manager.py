"""
Level 1 (real FreeCAD present in this environment, but no run and no Qt
event loop needed) — settings that are booleans stored in the same
`FreeCAD.ParamGet` group as everything else `SettingsManager` owns.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")


def test_core_settings_imports_without_pyside_or_freecad():
    """
    `core/settings.py` is the persistence half FCC-329 split out of
    `bridge/settings_manager.py`, so any `core/` module can read or write a
    setting without needing `PySide` importable. A subprocess with a bare
    `sys.path` (no FreeCAD lib dir, no conftest fixtures) is what actually
    proves it: within this suite's own process FreeCAD is already imported
    by the time a test runs.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, %r); import core.settings" % SRC],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_auto_approve_plan_defaults_to_off(clean_auto_approve):
    assert clean_auto_approve.get_auto_approve_plan() is False


def test_auto_approve_plan_round_trips(clean_auto_approve):
    clean_auto_approve.set_auto_approve_plan(True)

    assert clean_auto_approve.get_auto_approve_plan() is True

    clean_auto_approve.set_auto_approve_plan(False)

    assert clean_auto_approve.get_auto_approve_plan() is False
