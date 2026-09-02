"""
Level 1 (real FreeCAD present in this environment, but no run and no Qt
event loop needed) — settings that are booleans stored in the same
`FreeCAD.ParamGet` group as everything else `SettingsManager` owns.
"""
import pytest


def test_auto_approve_plan_defaults_to_off(clean_auto_approve):
    assert clean_auto_approve.get_auto_approve_plan() is False


def test_auto_approve_plan_round_trips(clean_auto_approve):
    clean_auto_approve.set_auto_approve_plan(True)

    assert clean_auto_approve.get_auto_approve_plan() is True

    clean_auto_approve.set_auto_approve_plan(False)

    assert clean_auto_approve.get_auto_approve_plan() is False
