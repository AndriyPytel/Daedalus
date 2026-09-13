"""
A Step's feedback carries only the geometry it touched.

The measurement matters: over a scripted N-Step phase, total bytes in feedback
messages grow linearly in Step count, not quadratically.
"""
import json

import pytest

from core.agent import phase_contract as pc
from core.agent import run_phase
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


def _scripted_turns(n_steps):
    turns = []
    for i in range(n_steps):
        turns.append(json.dumps({
            "code": f"doc.addObject('Part::Box', 'Box{i:03d}'); doc.recompute()",
            "step": i, "label": f"box {i:03d}",
        }))
    turns.append(json.dumps({"declare": "done"}))
    return turns


def _feedback_bytes(session):
    total = 0
    for msg in session.messages:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, str) and content.startswith('{"ok":'):
                total += len(content.encode('utf-8'))
    return total


def test_feedback_grows_linearly_with_step_count(kernel, tmp_path):
    """
    Over a scripted N-Step phase, feedback bytes grow linearly with N.

    Measured at 10 and 40 Steps: the ratio of total feedback bytes should
    approximate the step ratio (4.0), not its square (16.0).
    """
    n_small, n_large = 10, 40

    llm_small = ScriptedLLMClient(_scripted_turns(n_small))
    _, session_small, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "exec_small"),
        {"design": {"kind": "box"}, "plan": [{"do": "make boxes"}]},
        llm_small, "make boxes", kernel=kernel,
    )
    bytes_small = _feedback_bytes(session_small)

    llm_large = ScriptedLLMClient(_scripted_turns(n_large))
    _, session_large, _ = run_phase(
        pc.EXECUTE, str(tmp_path / "exec_large"),
        {"design": {"kind": "box"}, "plan": [{"do": "make boxes"}]},
        llm_large, "make boxes", kernel=kernel,
    )
    bytes_large = _feedback_bytes(session_large)

    ratio = bytes_large / bytes_small
    expected_linear_ratio = n_large / n_small

    assert ratio < expected_linear_ratio * 1.5, (
        f"Feedback bytes grew faster than linearly: "
        f"{bytes_small} bytes at {n_small} steps, "
        f"{bytes_large} bytes at {n_large} steps, "
        f"ratio {ratio:.1f} (expected ~{expected_linear_ratio:.1f} for linear)"
    )
