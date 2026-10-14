"""
`intent` and `outcome` are variables in the kernel's namespace on every call of the phases that read them -- Review and Review-suspect get both, Design gets `intent`, Execute gets neither -- and no phase's working directory holds a file for them.
"""
import json

import pytest

from core.agent import agent as agent_module
from core.agent import phase_contract as pc
from core.kernel import Kernel, KernelResult, find_freecadcmd
from core.llm import ScriptedLLMClient
from core.loop.review import review, review_suspect
from tests.loop_helpers import design_reply, drive, plan_reply

INTENT = "Make a 10mm box"
BASE = "The part before the edit, in mm:\nplanes:\n  plane y = -17.5 (area 1605.35 mm2)"
GOOD = {"features": [{"id": "boss", "edit": "changed", "at": ["plane y = -17.5"]}]}
DESCRIBED = {"volume": 1000.0, "solids": 1}
LOOK = json.dumps({"code": "print(sorted(outcome), intent)"})
DONE = {"review": json.dumps({"declare": {"verdict": "pass", "reason": "ok"}}),
        "suspect": json.dumps({"declare": {"suspected": []}})}


class _Spy:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def check(self, code, **kwargs):
        _Spy.calls.append(dict(kwargs, code=code))
        return KernelResult(ok=True, stdout="[]")

    def kill(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _looked(call, *turn_after):
    _Spy.calls = []
    call(ScriptedLLMClient([LOOK, *turn_after]))
    return [c for c in _Spy.calls if c["code"] == json.loads(LOOK)["code"]]


def _review(llm):
    return review(INTENT, [], DESCRIBED, llm, None, kernel=_Spy())


def _suspect(llm):
    return review_suspect(INTENT, [], DESCRIBED, llm, None, kernel=_Spy())


def test_review_code_gets_intent_and_outcome():
    seen = _looked(_review, DONE["review"])

    assert [(c["intent"], c["outcome"]) for c in seen] == [(INTENT, DESCRIBED)]


def test_review_suspect_code_gets_intent_and_outcome():
    seen = _looked(_suspect, DONE["suspect"])

    assert [(c["intent"], c["outcome"]) for c in seen] == [(INTENT, DESCRIBED)]


def test_design_code_gets_intent_and_no_outcome(monkeypatch):
    _Spy.calls = []
    monkeypatch.setattr(agent_module, "Kernel", _Spy)
    drive([LOOK, design_reply(GOOD), plan_reply("Pad it")], kernel=_Spy(), base=BASE,
          faces=[{"base": {"plane y = -17.5": 1605.35}, "now": []}] * 4)

    seen = [c for c in _Spy.calls if c["code"] == json.loads(LOOK)["code"]]
    assert seen and seen[0]["intent"] == INTENT and "outcome" not in seen[0]


def test_execute_code_gets_neither():
    _Spy.calls = []
    agent_module.run_phase(pc.EXECUTE, "unused", {"intent": INTENT, "outcome": DESCRIBED},
                           ScriptedLLMClient([LOOK, json.dumps({"declare": {}})]), "go",
                           kernel=_Spy())

    assert all("intent" not in c and "outcome" not in c for c in _Spy.calls)


def test_no_phase_working_directory_holds_either(tmp_path):
    sources = {"intent": INTENT, "outcome": DESCRIBED}
    for phase in pc.PHASE_CONTRACT:
        assert pc.spawn(phase, str(tmp_path / phase), **sources) == str(tmp_path / phase)
        assert list((tmp_path / phase).iterdir()) == []


def _check(code, **kwargs):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    import os
    import tempfile

    import FreeCAD
    import Part

    doc = FreeCAD.newDocument("Namespace")
    doc.addObject("Part::Feature", "Imported").Shape = Part.makeBox(1, 1, 1)
    doc.recompute()
    path = os.path.join(tempfile.mkdtemp(), doc.Name + ".FCStd")
    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)
    with Kernel(timeout=120) as worker:
        return worker.check(code, path, **kwargs)


@pytest.mark.freecad
def test_a_real_kernel_hands_the_code_intent_and_outcome():
    result = _check("print(sorted(outcome), intent)", intent=INTENT, outcome=DESCRIBED)

    assert result.ok, result.traceback
    assert "['solids', 'volume']" in result.stdout and INTENT in result.stdout


@pytest.mark.freecad
def test_a_real_kernel_call_without_them_has_no_such_names():
    result = _check("print(intent)")

    assert not result.ok and "NameError" in result.traceback


def test_model_code_cannot_overwrite_them():
    from core.kernel.kernel_worker import RESERVED_STEP_NAMES

    assert {"intent", "outcome"} <= RESERVED_STEP_NAMES
