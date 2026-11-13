"""
Level 1 -- the Effort a client is asked for: the one it was built with, which
the factory reads from the single stored `effort` setting.
"""
import pytest

from core import settings
from core.events import CALL_STARTED
from core.llm import ClaudeCodeClient, factory
from core.llm.effort_policy import _CEILING, _SUBSCRIPTION_EFFORT
from chat_helpers import kernel, say, set_the_chat  # noqa: F401

MODEL = "sonnet"


def test_the_ceiling_follows_each_clients_own_effort():
    assert ClaudeCodeClient(MODEL, effort="low").timeout == _CEILING["low"]
    assert ClaudeCodeClient(MODEL, effort="high").timeout == _CEILING["high"]


@pytest.mark.parametrize("level", ["xhigh", "max"])
def test_the_levels_above_high_reach_the_cli_flag(level):
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from test_llm_claude_code import client_replaying, a_turn
    from conftest import collect

    client, spawn = client_replaying(effort=level)
    collect(client.stream(a_turn()))

    assert spawn.argv[spawn.argv.index("--effort") + 1] == level


def test_a_client_built_with_no_effort_is_asked_at_the_default():
    assert ClaudeCodeClient(MODEL).effort == _SUBSCRIPTION_EFFORT


def test_the_factory_reads_the_one_stored_effort():
    settings.instance().set_effort("high")
    settings.instance().set_last_model("Claude (subscription)", MODEL)

    assert factory.client_from_settings().effort == "high"


def test_an_effort_the_dial_does_not_offer_falls_back_to_the_default():
    settings.instance().set_effort("ultra")

    assert factory.effort() == _SUBSCRIPTION_EFFORT


@pytest.mark.freecad
def test_the_loops_calls_are_logged_at_the_effort_the_chat_names(monkeypatch, kernel):
    from sdk import Client

    set_the_chat(monkeypatch, "haiku", "high", [say(probe="print(1)"), say(finish="done")])
    client = Client(kernel=kernel)
    seen = []
    client.watch(seen.append)

    client.agent("Make a 10 mm cube")
    client.close()

    started = [event.data for event in seen if event.kind == CALL_STARTED]
    assert started
    assert {call["effort"] for call in started} == {"high"}
