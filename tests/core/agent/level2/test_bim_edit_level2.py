"""
Level 2 -- a saved flat of Arch objects is edited through the BIM Domain: a change that holds the Assertion lands, a do-nothing finish is refused, a named removal needs no consent and any other deletion is refused.
"""
import json

import pytest

import sdk
from core.agent.domain import BIM
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from tests.bim_helpers import FLAT_WITH_TWO_WINDOWS

pytestmark = pytest.mark.freecad

REMOVE_WINDOW2 = """
extra = doc.getObjectsByLabel("Window_W2")[0]
base = extra.Base
doc.removeObject(extra.Name)
if base is not None:
    doc.removeObject(base.Name)
doc.recompute()
"""
REMOVE_DOOR = """
door = doc.getObjectsByLabel("Door")[0]
doc.removeObject(door.Name)
doc.recompute()
"""


def _say(**turn):
    return json.dumps(turn)


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=180) as running:
        yield running


@pytest.fixture
def saved(kernel, tmp_path):
    client = sdk.Client(kernel=kernel, llm=ScriptedLLMClient(
        [_say(code=FLAT_WITH_TWO_WINDOWS), _say(finish="built")]))
    assert client.agent("Build the flat.", domain=BIM).ok
    path = client.save(str(tmp_path / "input.FCStd"))
    client.close()
    return path


def _edit(kernel, saved, intent, turns, stop=None):
    llm = ScriptedLLMClient(turns)
    client = sdk.Client(document=saved, kernel=kernel, llm=llm)
    result = client.agent(intent, domain=BIM, base=True,
                          cancelled=(lambda: len(llm.calls) >= stop) if stop else None)
    labels = [o.Label for o in client.document.Objects]
    client.close()
    return result, labels, llm


def test_a_named_removal_lands_without_asking_and_the_assertion_holds(kernel, saved):
    result, labels, _ = _edit(kernel, saved, "Remove Window_W2.",
                              [_say(code=REMOVE_WINDOW2), _say(finish="removed")])

    assert result.ok, result.reason
    assert "Window_W2" not in labels and "Window" in labels


def test_a_finish_that_changed_nothing_is_refused(kernel, saved):
    result, _, llm = _edit(kernel, saved, "Remove Window_W2.", [_say(finish="done"), _say(finish="done")], stop=2)

    assert not result.ok
    assert "given" in json.dumps(llm.calls[1][-1]["content"])


def test_a_deletion_the_intent_does_not_name_is_refused(kernel, saved):
    result, labels, _ = _edit(kernel, saved, "Remove Window_W2.", [_say(code=REMOVE_DOOR)], stop=2)

    assert "Door" in labels
    assert not result.ok


def test_the_edit_prompt_and_the_facts_line_are_sent_only_where_a_document_is_edited(kernel, saved):
    sent = {}
    for base in (False, True):
        llm = ScriptedLLMClient([_say(finish="x")])
        client = sdk.Client(document=saved if base else None, kernel=kernel, llm=llm)
        client.agent("Edit.", domain=BIM, base=base, cancelled=lambda: len(llm.calls) >= 1)
        sent[base] = llm.calls[0]
        client.close()

    assert len(sent[True][0]["content"]) > len(sent[False][0]["content"])
    assert "Window_W2" in json.dumps(sent[True][1]["content"])
    assert "Window_W2" not in json.dumps(sent[False][1]["content"])
