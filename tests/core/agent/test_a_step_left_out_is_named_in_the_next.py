"""A Step sent with `leaves_out` naming the previous Step is accepted; without it the order guard refuses it."""
import json

from core.events import TOOL_RESULT, EventStream
from core.llm import ScriptedLLMClient
from core.loop.execute import execute
from tests.event_helpers import events_of
from tests.loop_helpers import FakeTools, StubKernel

STEPS = ["First", "Second", "Third"]


def _written(third):
    llm = ScriptedLLMClient([
        json.dumps({"code": "first()", "step": 1, "label": "Step"}),
        third,
        json.dumps({"declare": {"steps": [{"step": 1}, {"step": 3}],
                                "unbuildable": "Step 2 has no face"}})])
    events = EventStream()
    written = execute(STEPS, FakeTools().registry, llm, events, kernel=StubKernel())
    refusals = [e for e in events_of(events, TOOL_RESULT)
                if e.data.get("result", {}).get("status") == "refused"]
    return written, refusals


def test_a_step_that_names_the_one_before_it_as_left_out_is_accepted():
    written, refusals = _written(json.dumps(
        {"code": "third()", "step": 3, "label": "Step", "leaves_out": {"2": "no face"}}))

    assert not refusals and 3 in written.steps


def test_without_leaves_out_the_step_after_a_gap_is_refused():
    written, refusals = _written(json.dumps({"code": "third()", "step": 3, "label": "Step"}))

    assert refusals and "Step 2" in refusals[0].data["result"]["traceback"]
    assert 3 not in written.steps
