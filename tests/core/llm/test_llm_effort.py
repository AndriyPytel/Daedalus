"""
Level 1 -- the effort dial: what a client is asked for, and what it falls
back to.

Effort is per role now, carried on the role's row of the table
(`core.settings :: get_role_table`) and handed to `client_from_settings`;
a row that names none leaves the client on the single stored dial.
"""
from core.llm import ClaudeCodeClient
from core.llm.effort_policy import _CEILING

MODEL = "sonnet"


def test_the_ceiling_follows_each_clients_own_effort_and_not_one_global_dial():
    low = ClaudeCodeClient(MODEL, effort="low")
    high = ClaudeCodeClient(MODEL, effort="high")

    assert low.timeout == _CEILING["low"]
    assert high.timeout == _CEILING["high"]


def test_xhigh_reaches_the_cli_flag_and_is_not_swapped_for_the_dial(monkeypatch):
    import os
    import sys

    from core import settings

    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from test_llm_claude_code import client_replaying, a_turn
    from conftest import collect

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "medium")
    client, spawn = client_replaying(effort="xhigh")
    collect(client.stream(a_turn()))

    assert spawn.argv[spawn.argv.index("--effort") + 1] == "xhigh"


def test_max_reaches_the_cli_flag_and_is_not_swapped_for_the_dial(monkeypatch):
    import os
    import sys

    from core import settings

    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from test_llm_claude_code import client_replaying, a_turn
    from conftest import collect

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "medium")
    client, spawn = client_replaying(effort="max")
    collect(client.stream(a_turn()))

    assert spawn.argv[spawn.argv.index("--effort") + 1] == "max"


def test_an_explicit_effort_wins_over_whatever_the_dial_is_set_to(monkeypatch):
    from core import settings

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "high")

    assert ClaudeCodeClient(MODEL, effort="low").timeout == _CEILING["low"]


def test_no_explicit_effort_still_reads_the_global_dial(monkeypatch):
    from core import settings

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "high")

    assert ClaudeCodeClient(MODEL).timeout == _CEILING["high"]


def test_an_effort_the_dial_does_not_offer_is_refused_and_not_swapped_for_the_dial(monkeypatch):
    import pytest
    from core import settings
    from core.llm.effort_policy import _resolved_effort

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "medium")

    with pytest.raises(ValueError):
        _resolved_effort("ultra")
    assert _resolved_effort(None) == "medium"
