"""
A rewind after a Step the Document refused permanently -- not drift, not
retried, just abandoned on its first and only attempt -- must not resume the
kernel from a landed Step's own checkpoint mixed with the abandoned Step's
mutation: `declarations[-1]` is the last entry appended, and an abandoned
Step is appended too.
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


def test_a_drift_after_an_abandoned_step_does_not_rewind_onto_it(kernel, tmp_path):
    """
    Two Steps land. A third is refused outright -- lint, one attempt, no
    retry -- so it is abandoned. The model retries that same Step number and
    drifts on its first rewrite; the rewind that follows must land on the
    last Step that reached the Document, not on the abandoned attempt's own
    checkpoint.
    """
    attempts = {"three": 0}

    def on_declaration(declaration):
        if declaration["step"] in (1, 2):
            return {"delivered": True}
        if declaration["step"] == 3:
            attempts["three"] += 1
            if attempts["three"] == 1:
                return {"delivered": False, "traceback": "lint: bad BoundBox use"}
            if attempts["three"] == 2:
                return {"delivered": False, "drift": True, "traceback": "signature mismatch"}
            return {"delivered": True}
        return {"delivered": True}

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box1'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()",
                    "step": 2, "label": "second box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box3Abandoned'); doc.recompute()",
                    "step": 3, "label": "third box, abandoned"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box3Drifted'); doc.recompute()",
                    "step": 3, "label": "third box, drifted"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box3'); doc.recompute()",
                    "step": 3, "label": "third box, landed"}),
        json.dumps({"declare": {"steps": [{"step": 1}, {"step": 2}, {"step": 3}]}}),
    ])

    _, _, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad three boxes",
        kernel=kernel, on_declaration=on_declaration,
    )

    landed = [d for d in declarations if d.get("delivered", True)]
    assert [d["step"] for d in landed] == [1, 2, 3]
    assert landed[-1]["signature"]["names"] == ["Box1", "Box2", "Box3"], (
        "the rewind carried an abandoned attempt's own object into a later "
        "Step's signature: %r" % landed[-1]["signature"])
