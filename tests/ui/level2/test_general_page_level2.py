"""
Level 2 -- the "General" preference sub-page, rendered for real.

One control so far, `auto_approve_check`, backed by
`settings_manager.get_auto_approve_plan`/`set_auto_approve_plan`.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _page():
    assert QT_APP is not None, NO_QT
    from ui.general_page import GeneralPage
    return GeneralPage()


def test_the_checkbox_starts_unchecked_reading_the_default(clean_auto_approve):
    page = _page()
    page.loadSettings()

    assert page.auto_approve_check.isChecked() is False


def test_checking_the_box_and_saving_turns_the_setting_on(clean_auto_approve):
    page = _page()
    page.loadSettings()
    page.auto_approve_check.setChecked(True)
    page.saveSettings()

    assert clean_auto_approve.get_auto_approve_plan() is True


def test_unchecking_and_saving_turns_it_back_off(clean_auto_approve):
    clean_auto_approve.set_auto_approve_plan(True)
    page = _page()
    page.loadSettings()
    assert page.auto_approve_check.isChecked() is True

    page.auto_approve_check.setChecked(False)
    page.saveSettings()

    assert clean_auto_approve.get_auto_approve_plan() is False
