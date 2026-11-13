"""
Level 2 -- the router, the Domain inference and the Session title are asked with the model and the Effort the chat names, the same as the loop, and a pick made between two sends is the next send's.
"""
import pytest

from core import settings
from core.session_title import generate
from chat_helpers import kernel, set_the_chat, say  # noqa: F401

pytestmark = pytest.mark.freecad


def test_every_call_a_prompt_makes_is_made_with_the_chats_model_and_effort(kernel, monkeypatch):
    from sdk import Client

    asked = set_the_chat(monkeypatch, "haiku", "high",
                         ["run_question", "It finished.", "part", "Bracket"])
    client = Client(kernel=kernel)

    client.send("what happened to the last run?")
    client.send("Make a bracket.", route_first=False, cancelled=lambda: len(asked) >= 3)
    generate("Make a bracket.")
    client.close()

    assert asked == [("haiku", "high")] * 4


def test_a_pick_made_between_two_sends_is_the_next_sends(monkeypatch):
    from sdk import NO_KERNEL, Client

    asked = set_the_chat(monkeypatch, "haiku", "high", ["run_question", "One.", "run_question", "Two."])
    client = Client(kernel=NO_KERNEL)

    client.send("first?")
    settings.instance().set_last_model("Claude (subscription)", "opus")
    settings.instance().set_effort("low")
    client.send("second?")
    client.close()

    assert asked == [("haiku", "high")] * 2 + [("opus", "low")] * 2
