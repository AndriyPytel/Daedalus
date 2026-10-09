"""
A scripted run on part 202 emits exactly the recorded sequence of event kinds.
"""
import json

import pytest

from core.events import EventStream
from core.freecad_apply import ApplyPipeline, build_registry
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from core.loop.gate import APPROVE, Approval
from core.loop.run import run
from loop_helpers import design_reply, everything, plan_reply
from tests.core.kernel import part_202

pytestmark = pytest.mark.freecad

A_DESIGN = {"features": [{"id": "hub-raise", "length": {"value": 8.0, "flag": "read"}}]}

STEP = """
doc.getObject("HubRaise").Length = design["features"]["hub-raise"]["length"]["value"]
"""

RECORDED = [
    'intent', 'call_started', 'writing', 'cost', 'design', 'part', 'call_started', 'writing',
    'cost', 'plan', 'gate', 'gate', 'call_started', 'writing', 'cost', 'tool_call',
    'step_declared', 'tool_call', 'tool_result', 'applied', 'tool_result', 'call_started',
    'writing', 'cost', 'step_started', 'tool_call', 'tool_result', 'call_started', 'writing',
    'cost', 'review_suspected', 'tool_call', 'tool_result', 'call_started', 'writing', 'cost', 'tool_call', 'tool_result',
    'assertion', 'call_started', 'writing', 'cost', 'review_verdict', 'completed'
]


def test_the_event_kinds_of_a_scripted_run_on_part_202_are_the_recorded_sequence(tmp_path):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    doc = part_202.opened()
    try:
        with Kernel(timeout=120) as worker:
            pipeline = ApplyPipeline(document=doc, worker=worker,
                                     dispatch=lambda func, timeout=None: func(),
                                     render_dir=str(tmp_path),
                                     confirm=lambda kinds: False)
            events = EventStream()
            llm = ScriptedLLMClient([
                design_reply(A_DESIGN),
                plan_reply("Raise the hub to 8 mm", satisfies=everything(A_DESIGN)),
                json.dumps({"code": STEP, "step": 1, "label": "Raise the hub"}),
                json.dumps({"declare": {"steps": [{"step": 1, "label": "Raise the hub"}]}}),
                json.dumps({"declare": {"suspected": []}}),
                json.dumps({"declare": {"checks": [{"entry": n, "expr": "True"} for n in range(32)]}}),
                json.dumps({"declare": {"verdict": "pass", "reason": "The hub is 8 mm."}}),
            ])
            result = run("Raise the hub to 8 mm", llm=llm,
                         tools=build_registry(doc, pipeline=pipeline), events=events,
                         gate=lambda steps, **kwargs: Approval(APPROVE))
    finally:
        import FreeCAD
        FreeCAD.closeDocument(doc.Name)

    kinds = [event.kind for event in events]
    assert result.ok, result.reason
    assert kinds == RECORDED
