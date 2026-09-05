"""
FCC-321: a phase agent iterates against its own kernel instead of answering
in one call.
"""
import json
import os

import pytest

from core import phase_contract as pc
from core.agent import AgentGaveUp, run_phase
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_agent_iterates_after_a_bad_attempt_before_declaring(kernel, tmp_path):
    """AC1/TESTING STRATEGY: fails, tries different code, then declares."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box').NoSuchProperty = 1"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()"}),
        json.dumps({"declare": {"step": "pad the box"}}),
    ])

    declared, session, checkpoints = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"),
        {"design": {"kind": "box"}, "plan": [{"do": "pad 10mm"}]},
        llm, "pad a 10mm box", kernel=kernel,
    )

    assert declared == {"step": "pad the box"}
    assert len(llm.calls) == 3
    assert len(checkpoints) == 1
    assert os.path.exists(checkpoints[0])


def test_agent_does_not_iterate_past_a_first_attempt_success(kernel, tmp_path):
    """AC3: no extra turns once the model is done."""
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box'); doc.recompute()"}),
        json.dumps({"declare": {"step": "pad the box"}}),
    ])

    declared, session, checkpoints = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"),
        {"design": {"kind": "box"}, "plan": [{"do": "pad 10mm"}]},
        llm, "pad a 10mm box", kernel=kernel,
    )

    assert declared == {"step": "pad the box"}
    assert len(llm.calls) == 2
    assert len(checkpoints) == 1


def test_agent_gives_up_on_a_reply_with_neither_code_nor_declaration(kernel, tmp_path):
    llm = ScriptedLLMClient([json.dumps({"comment": "thinking out loud"})])

    with pytest.raises(AgentGaveUp):
        run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box", kernel=kernel)
