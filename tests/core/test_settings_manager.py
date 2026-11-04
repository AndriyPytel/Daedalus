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
    `core/settings.py` is the persistence half split out of
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


@pytest.fixture
def no_keys_or_addresses(the_settings_never_reach_the_user_s_freecad):
    """The store is the session's, so what a test writes is put back."""
    from core import settings

    sm = settings.instance()
    keys, urls = sm.get_api_keys(), sm.get_base_urls()
    yield sm
    sm.set_api_keys(keys)
    sm.set_base_urls(urls)


def test_a_provider_added_by_name_is_offered_after_the_catalogs_own(no_keys_or_addresses):
    """A key or an address stored under a name the catalog does not know is a provider the user added."""
    from core import model_catalog

    sm = no_keys_or_addresses
    sm.set_api_keys([{"provider": "Groq", "api_key": "gsk"},
                     {"provider": "OpenAI", "api_key": "sk"}])
    sm.set_base_urls({"Groq": "https://api.groq.com/openai/v1",
                      "Home server": "http://localhost:11434"})

    assert sm.get_providers() == model_catalog.providers() + ["Groq", "Home server"]


def test_the_domain_choice_defaults_to_auto_and_round_trips(the_settings_never_reach_the_user_s_freecad):
    from core import settings

    sm = settings.instance()
    chosen = sm.get_domain()
    try:
        sm.set_domain("")
        assert sm.get_domain() == "auto"
        sm.set_domain("assembly")
        assert sm.get_domain() == "assembly"
    finally:
        sm.set_domain(chosen)
