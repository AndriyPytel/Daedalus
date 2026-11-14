"""
Level 2 -- `Client.send` is the whole run: the Kernel is seeded once per send, the Domain is inferred when none is chosen, an Owned Object is asked about only when someone is there to ask, and the model is the chat's as it is at each send.
"""
import pytest

from core.agent.domain import ASSEMBLY, PART
from core.events import ANSWER, DOMAIN, QUESTION
from core.llm import ScriptedLLMClient
from chat_helpers import kernel, set_the_chat, say  # noqa: F401

pytestmark = pytest.mark.freecad

BOX = "doc.addObject('Part::Box', 'Box')\ndoc.recompute()"
DELETING = say(code="doc.removeObject('UserBox')")


def _client(kernel, llm=None, **options):
    from sdk import Client

    client = Client(kernel=kernel, llm=llm, **options)
    client.seen = []
    client.watch(client.seen.append)
    return client


def _with_a_box_of_the_users(client):
    client.document.addObject("Part::Box", "UserBox")
    client.document.recompute()


def test_an_intent_sent_twice_seeds_the_kernel_once_per_send_and_forgets_the_names_in_between(kernel, monkeypatch):
    seeds = []
    real = kernel.seed

    def recording(*args, **kwargs):
        seeds.append(dict(client._pipeline._names))
        return real(*args, **kwargs)

    monkeypatch.setattr(kernel, "seed", recording)
    llm = ScriptedLLMClient([say(code=BOX), say(finish="done"), say(code=BOX), say(finish="done")])
    client = _client(kernel, llm)

    client.send("Make a cube", route_first=False, choice=PART)
    assert client._pipeline._names
    client.send("Make a cube", route_first=False, choice=PART)

    assert seeds == [{}, {}]
    client.close()


def test_an_assembly_intent_sent_with_no_choice_builds_under_assembly(kernel):
    llm = ScriptedLLMClient(["assembly"])
    client = _client(kernel, llm)

    client.send("Assemble a hinge", route_first=False, cancelled=lambda: len(llm.calls) >= 1)

    domains = [event.data for event in client.seen if event.kind == DOMAIN]
    assert domains == [{"domain": ASSEMBLY, "inferred": True}]
    client.close()


def test_with_no_confirm_an_object_the_user_made_is_kept_and_nobody_is_asked(kernel):
    llm = ScriptedLLMClient([DELETING, say(finish="done")])
    client = _client(kernel, llm)
    _with_a_box_of_the_users(client)

    client.send("Remove the box", route_first=False, choice=PART, cancelled=lambda: len(llm.calls) >= 2)

    assert client.document.getObject("UserBox") is not None
    assert "UserBox" in llm.calls[1][-1]["content"]
    assert not {QUESTION, ANSWER} & {event.kind for event in client.seen}
    client.close()


def test_with_a_confirm_callback_it_is_called_once_with_the_kinds_and_its_yes_lets_the_deletion_land(kernel):
    asked = []
    llm = ScriptedLLMClient([DELETING, say(finish="done")])
    client = _client(kernel, llm, confirm=lambda kinds: asked.append(kinds) or True)
    _with_a_box_of_the_users(client)

    client.send("Remove the box", route_first=False, choice=PART)

    assert asked == [{"delete": ["UserBox"]}]
    assert client.document.getObject("UserBox") is None
    client.close()


def test_a_model_picked_between_two_sends_is_the_one_the_second_send_calls(kernel, monkeypatch):
    asked = set_the_chat(monkeypatch, "haiku", "high", [say(finish="one"), say(finish="two")])
    from core import settings

    client = _client(kernel)

    client.send("Make a cube", route_first=False, choice=PART, cancelled=lambda: len(asked) >= 1)
    settings.instance().set_last_model("Claude (subscription)", "opus")
    client.send("Make a cube", route_first=False, choice=PART, cancelled=lambda: len(asked) >= 2)

    started = [event.data["model"] for event in client.seen if event.kind == "call_started"]
    assert started == ["haiku", "opus"]
    client.close()
