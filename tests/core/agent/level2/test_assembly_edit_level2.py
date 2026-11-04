"""
Level 2 -- a saved Assembly opened as the Document is edited through the Assembly Domain, and the Assertion holds on what it saves.
"""
import json
import os
import sys

import pytest

import sdk
from core.agent.domain import ASSEMBLY
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from tests.assembly_helpers import SOLVED

pytestmark = pytest.mark.freecad

sys.path.insert(0, os.path.join(os.path.dirname(sdk.__file__), "tools"))

import assert_assembly  # noqa: E402

REMOVE_B = """
doc.removeObject('Joint')
doc.removeObject('B')
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
    with Kernel(timeout=120) as running:
        yield running


@pytest.fixture
def saved(kernel, tmp_path):
    client = sdk.Client(kernel=kernel, llm=ScriptedLLMClient([_say(code=SOLVED), _say(finish="built")]))
    assert client.agent("Assemble the two boxes.", domain=ASSEMBLY).ok
    path = client.save(str(tmp_path / "input.FCStd"))
    client.close()
    return path


def test_the_agent_edits_the_assembly_it_was_handed_and_the_assertion_holds(kernel, saved, tmp_path):
    llm = ScriptedLLMClient([_say(code=REMOVE_B), _say(finish="removed B")])
    client = sdk.Client(document=saved, kernel=kernel, llm=llm, confirm=lambda kinds: True)

    result = client.agent("Remove B and its joint.", domain=ASSEMBLY, base=True)

    assert result.ok, result.reason
    labels = [o.Label for o in client.document.Objects if o.TypeId == "Part::Feature"]
    assert labels == ["A"]
    client.close()


def test_the_edit_prompt_is_sent_only_where_a_document_is_edited(kernel, saved):
    systems = {}
    for base in (False, True):
        llm = ScriptedLLMClient([_say(finish="x")])
        client = sdk.Client(document=saved if base else None, kernel=kernel, llm=llm)
        client.agent("Assemble.", domain=ASSEMBLY, base=base, cancelled=lambda: len(llm.calls) >= 1)
        systems[base] = llm.calls[0][0]["content"]
        client.close()

    assert systems[True].startswith(systems[False])
    assert len(systems[True]) > len(systems[False])


def test_the_assertion_command_says_which_saved_documents_fail_it(kernel, saved, tmp_path):
    llm = ScriptedLLMClient([_say(code="doc.removeObject('Joint')\ndoc.recompute()"), _say(finish="x")])
    client = sdk.Client(document=saved, kernel=kernel, llm=llm, confirm=lambda kinds: True)
    client.agent("Drop the joint.", domain=ASSEMBLY, base=True, cancelled=lambda: len(llm.calls) >= 1)
    jointless = client.save(str(tmp_path / "jointless.FCStd"))
    client.close()

    verdicts = assert_assembly.verdicts([saved, jointless])

    assert verdicts[saved] is None
    assert "no joint" in verdicts[jointless]
