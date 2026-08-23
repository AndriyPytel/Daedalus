"""
Level 2 — the wrapped Assertion phase (FCC-189) against a real Document.

`tests/test_rlm_wrapper_assertion.py` proves the shape of the seam against a
fake `ToolRegistry` — `look`, `delegate` and `trap` reachable, `run` refused
(`core/loop.py :: NOT_ARMED_FOR_RUN`) — which is the right level for that
claim but agrees with any measurement a check happens to make. This proves
the one thing a fake cannot: that `check_assertions`, called the way
`assertion()` calls it with `agentic=True`, judges a real Shape rather than
the number the mock was told to hand back — a 30 mm cube passes a 30 mm
check and a 20 mm cube does not, both through the same wrapped phase and the
same real `PreflightWorker` subprocess `tests/test_assertion_level2.py`
already opens one Document copy at a time in.
"""
import json

import pytest

from core.apply import ApplyPipeline
from core.events import ASSERTION, EventStream
from core.freecad_tools import build_registry
from core.llm import ScriptedLLMClient
from core.loop import Budgets, assertion
from core.preflight import PreflightWorker, find_freecadcmd

pytestmark = pytest.mark.freecad

ACCEPTANCE = ["Shape.Volume is 27000 mm3"]
DESCRIBED = {"features": []}
CHECK_30MM = "assert abs(doc.getObject('Box').Shape.Volume - 27000) < 1e-6"


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with PreflightWorker() as running:
        yield running


def _cube(name, length):
    import FreeCAD

    document = FreeCAD.newDocument(name)
    box = document.addObject("Part::Box", "Box")
    box.Length = box.Width = box.Height = length
    document.recompute()
    return document


def _registry(document, worker):
    pipeline = ApplyPipeline(document=document, worker=worker,
                             dispatch=lambda func, timeout=None: func())
    return build_registry(document=document, pipeline=pipeline)


def _budgets():
    return Budgets(attempts_per_step=3, agent_turns=5)


def test_the_wrapped_assertion_phase_judges_a_real_30mm_cube_as_ok(worker):
    import FreeCAD

    document = _cube("AssertionWrapperOk", 30)
    try:
        tools = _registry(document, worker)
        llm = ScriptedLLMClient([json.dumps({"code": CHECK_30MM})])
        events = EventStream()

        checked = assertion(ACCEPTANCE, DESCRIBED, tools, llm, events,
                            budgets=_budgets(), agentic=True)

        assert checked.failed is False
        assert events.of(ASSERTION)[0].data["ok"] is True
    finally:
        FreeCAD.closeDocument(document.Name)


def test_the_wrapped_assertion_phase_judges_a_real_cube_that_is_not_30mm_as_not_ok(worker):
    import FreeCAD

    document = _cube("AssertionWrapperFail", 20)
    try:
        tools = _registry(document, worker)
        llm = ScriptedLLMClient([json.dumps({"code": CHECK_30MM})])
        events = EventStream()

        checked = assertion(ACCEPTANCE, DESCRIBED, tools, llm, events,
                            budgets=_budgets(), agentic=True)

        assert checked.failed is True
        assert events.of(ASSERTION)[0].data["ok"] is False
    finally:
        FreeCAD.closeDocument(document.Name)
