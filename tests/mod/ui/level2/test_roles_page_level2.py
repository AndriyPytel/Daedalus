"""
Level 2 -- the "Role -> Model" preference sub-page, rendered for real.

Seeded from `core.llm.DEFAULT_ROLE_TABLE`; editing a row and saving is what
`bridge.settings_manager.set_role_table` reads back.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _page():
    assert QT_APP is not None, NO_QT
    from ui.roles_page import RolesPage
    return RolesPage()


@pytest.fixture
def clean_role_table(the_settings_never_reach_the_user_s_freecad):
    from bridge import settings_manager

    the_settings_never_reach_the_user_s_freecad.SetString("RoleModelTable", "")
    return settings_manager.instance()


def test_opening_the_page_shows_one_row_per_seeded_role(clean_role_table):
    from core import llm

    page = _page()
    page.loadSettings()

    assert page.table.rowCount() == len(llm.DEFAULT_ROLE_TABLE)
    assert page.table.item(0, 0).text() == llm.DEFAULT_ROLE_TABLE[0]["role"]


def _the_providers_offer(monkeypatch, by_provider):
    from bridge import provider_manager

    monkeypatch.setattr(
        provider_manager.instance(), "get_models_for_provider",
        lambda provider: [{"id": name, "display": name}
                          for name in by_provider.get(provider, [])])


def test_a_rows_model_is_picked_from_what_its_provider_offers(clean_role_table, monkeypatch):
    _the_providers_offer(monkeypatch, {"Anthropic": ["claude-opus-5", "claude-haiku-4-5"]})

    page = _page()
    page.loadSettings()

    combo = page.table.cellWidget(1, 2)
    assert [combo.itemText(i) for i in range(combo.count())] == \
        ["claude-opus-5", "claude-haiku-4-5"]
    assert combo.currentText() == "claude-opus-5"


def test_changing_a_rows_provider_refills_the_models_it_offers(clean_role_table, monkeypatch):
    _the_providers_offer(monkeypatch, {"Anthropic": ["claude-opus-5"], "OpenAI": ["gpt-5"]})

    page = _page()
    page.loadSettings()
    page.table.cellWidget(1, 1).setCurrentText("OpenAI")

    combo = page.table.cellWidget(1, 2)
    assert [combo.itemText(i) for i in range(combo.count())] == ["gpt-5"]


def test_a_model_no_provider_lists_today_survives_the_save(clean_role_table, monkeypatch):
    _the_providers_offer(monkeypatch, {})

    page = _page()
    page.loadSettings()
    page.saveSettings()

    stored = clean_role_table.get_role_table()
    assert stored[1]["model"] == "claude-opus-5"


def test_editing_a_rows_model_and_saving_changes_the_stored_table(clean_role_table):
    page = _page()
    page.loadSettings()

    page.table.cellWidget(1, 2).setCurrentText("claude-opus-9000")
    page.saveSettings()

    stored = clean_role_table.get_role_table()
    assert stored[1]["model"] == "claude-opus-9000"


def test_saved_rows_still_carry_their_role_and_provider(clean_role_table):
    from core import llm

    page = _page()
    page.loadSettings()
    page.saveSettings()

    stored = clean_role_table.get_role_table()
    assert [row["role"] for row in stored] == [row["role"] for row in llm.DEFAULT_ROLE_TABLE]
    assert stored[0]["provider"] == llm.DEFAULT_ROLE_TABLE[0]["provider"]
