"""
FCC-322: the five phase prompts (`config/prompts/{design,plan,execute,
assertion,review}.md`, plus `review_suspect.md`) instruct FCC-321's
multi-turn agent, not the old one-call-per-phase `_ask` shape -- run through
a real prompt, a scripted LLM client and a real kernel per the ticket's own
TESTING STRATEGY.
"""
import json
import os

import pytest

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.events import SUB_AGENT_ANSWERED, SUB_AGENT_SPAWNED, EventStream
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from core.loop import ASSERTION_PHASE, DESIGN_PHASE, PLAN_PHASE, prompt
from core.agent.subagent import SessionTreeSupervisor

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "prompts")

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _text(name):
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as handle:
        return handle.read()


def test_design_prompt_runs_code_before_declaring(kernel, tmp_path):
    """AC1: a real Design prompt produces code-execution turns, not one reply."""
    task = prompt(DESIGN_PHASE).replace("{{traps}}", "")
    llm = ScriptedLLMClient([
        json.dumps({"code": "print(150 / 2)"}),
        json.dumps({"declare": {"kind_vocabulary": "closed",
                                "design": {"envelope": {"length": {"value": "150 mm",
                                                                    "flag": "read"}}},
                                "question": ""}}),
    ])

    declared, session, checkpoints = run_phase(
        pc.DESIGN, str(tmp_path / "design"), {"intent": {"ask": "a plate 150 long"}},
        llm, task, kernel=kernel,
    )

    assert len(llm.calls) == 2
    assert declared["design"]["envelope"]["length"]["value"] == "150 mm"
    assert any(m["role"] == "user" and "ok" in m["content"] for m in session.messages[2:])


def test_assertion_prompt_has_no_execute_artifact_and_says_so(kernel, tmp_path):
    """AC2: Assertion's row omits Execute; its prompt states that outright."""
    assert "execute" not in pc.PHASE_CONTRACT[pc.ASSERTION]

    text = _text("assertion.md")
    assert "no artefact of Execute" in text
    assert "not a file to go looking for" in text

    working = pc.spawn(pc.ASSERTION, str(tmp_path / "assertion"),
                       design={"envelope": {}}, document=_blank_document(tmp_path),
                       execute={"steps": ["should never be written"]})
    assert sorted(os.listdir(working)) == ["design.json", "document.FCStd"]


def test_assertion_agent_declares_from_a_real_prompt(kernel, tmp_path):
    task = prompt(ASSERTION_PHASE).replace("{{traps}}", "")
    llm = ScriptedLLMClient([
        json.dumps({"code": "held = ['exists']\nfailed = []\nassert not failed"}),
        json.dumps({"declare": {"uncovered": []}}),
    ])

    declared, session, checkpoints = run_phase(
        pc.ASSERTION, str(tmp_path / "assertion"),
        {"design": {"envelope": {}}, "document": _blank_document(tmp_path)},
        llm, task, kernel=kernel,
    )

    assert declared == {"uncovered": []}
    assert len(llm.calls) == 2


def test_plan_prompt_exercises_delegation(kernel, tmp_path):
    """AC3: a rewritten prompt's agent may delegate, and the run shows the pair."""
    task = prompt(PLAN_PHASE).replace("{{traps}}", "")
    llm = ScriptedLLMClient([
        json.dumps({"delegate": "is 'thickness' on the closed envelope-key list?"}),
        "yes, 'thickness' is on the closed list.",
        json.dumps({"declare": {"op_vocabulary": "closed",
                                "steps": [{"do": "pad the plate 10 mm",
                                          "op": "pad", "satisfies": ["envelope -> thickness"]}]}}),
    ])
    supervisor = SessionTreeSupervisor()
    stream = EventStream()

    declared, session, checkpoints = run_phase(
        pc.PLAN, str(tmp_path / "plan"), {"design": {"envelope": {"thickness": "10 mm"}}},
        llm, task, kernel=kernel, supervisor=supervisor, stream=stream,
    )

    assert declared["steps"][0]["op"] == "pad"
    kinds = [event.kind for event in stream]
    assert SUB_AGENT_SPAWNED in kinds
    assert SUB_AGENT_ANSWERED in kinds


def _blank_document(tmp_path):
    path = str(tmp_path / "blank.FCStd")
    with Kernel() as kernel:
        result = kernel.check("pass", document=None)
        assert result.ok
        import shutil
        shutil.copyfile(result.checkpoint, path)
    return path
