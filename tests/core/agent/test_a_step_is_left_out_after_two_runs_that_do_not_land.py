"""Where nobody answers, a Step that two code runs have not landed is left out and Execute moves on."""
import json

from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop.execute import execute
from core.nobody import nobody_answering
from tests.loop_helpers import FakeTools, StubKernel

STEPS = ["First", "Second", "Third"]


def _turns():
    probe = json.dumps({"code": "print(1)"})
    return [json.dumps({"code": "first()", "step": 1, "label": "Step"}), probe, probe,
            json.dumps({"code": "second()", "step": 2, "label": "Step"}),
            json.dumps({"code": "third()", "step": 3, "label": "Step"}),
            json.dumps({"declare": {"steps": [{"step": 1}, {"step": 3}]}})]


def test_the_third_run_on_a_step_leaves_it_out_and_the_next_step_is_accepted():
    llm = ScriptedLLMClient(_turns())
    with nobody_answering():
        written = execute(STEPS, FakeTools().registry, llm, EventStream(), kernel=StubKernel())

    assert list(written.left_out) == ["2"] and 2 not in written.steps and 3 in written.steps


def test_a_person_at_the_gate_gets_no_cap():
    llm = ScriptedLLMClient(_turns()[:3] + [json.dumps({"code": "second()", "step": 2, "label": "Step"}),
                                           json.dumps({"code": "third()", "step": 3, "label": "Step"}),
                                           json.dumps({"declare": {"steps": []}})])
    written = execute(STEPS, FakeTools().registry, llm, EventStream(), kernel=StubKernel())

    assert not written.left_out and {1, 2, 3} <= set(written.steps)
