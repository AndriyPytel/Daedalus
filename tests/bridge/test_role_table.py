"""
Level 1 -- the role-to-model table (FCC-312), stored beside every other
setting `_SettingsManager` owns.
"""
import pytest


@pytest.fixture
def clean_role_table(the_settings_never_reach_the_user_s_freecad):
    """The manager with `RoleModelTable` unset, for a test that writes one."""
    from bridge import settings_manager

    the_settings_never_reach_the_user_s_freecad.SetString("RoleModelTable", "")
    return settings_manager.instance()


def test_an_unset_table_seeds_from_the_epics_default(clean_role_table):
    from core import llm

    table = clean_role_table.get_role_table()

    assert [row["role"] for row in table] == [row["role"] for row in llm.DEFAULT_ROLE_TABLE]
    assert table == [dict(row) for row in llm.DEFAULT_ROLE_TABLE]


def test_a_stored_table_round_trips(clean_role_table):
    written = [{"role": "design", "provider": "Anthropic",
                "model": "claude-opus-9", "effort": "high"}]

    clean_role_table.set_role_table(written)

    assert clean_role_table.get_role_table() == written


def test_get_role_model_finds_the_row_by_role(clean_role_table):
    row = clean_role_table.get_role_model("execute")

    assert row["role"] == "execute"
    assert row["provider"] == "Anthropic"


def test_get_role_model_is_none_for_a_role_not_in_the_table(clean_role_table):
    clean_role_table.set_role_table([{"role": "design", "provider": "Anthropic",
                                       "model": "x", "effort": "low"}])

    assert clean_role_table.get_role_model("orchestrator") is None
