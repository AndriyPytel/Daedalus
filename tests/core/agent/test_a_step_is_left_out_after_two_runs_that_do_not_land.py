"""Where nobody answers, a Step whose own code runs have twice not landed is left out; probes never count."""
import json

from core import kernel
from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop.execute import execute
from core.nobody import nobody_answering
from tests.loop_helpers import FakeTools, StubKernel

STEPS = ["First", "Second", "Third"]


class _BadCodeFails(StubKernel):
    def check(self, code, **kwargs):
        result = super().check(code, **kwargs)
        return kernel.KernelResult(ok=False, traceback="boom") if "bad()" in code else result


def _code(step, code=None):
    return json.dumps({"code": code or "s%d()" % step, "step": step, "label": "Step"})


def _written(turns, mode=nobody_answering):
    with mode():
        return execute(STEPS, FakeTools().registry, ScriptedLLMClient(turns), EventStream(),
                       kernel=_BadCodeFails())


def test_probes_never_leave_the_first_step_out():
    probe = json.dumps({"code": "print(1)"})
    written = _written([probe, probe, probe, _code(1), _code(2), _code(3),
                        json.dumps({"declare": {"steps": []}})])

    assert not written.left_out and {1, 2, 3} <= set(written.steps)


def test_the_step_whose_own_runs_failed_twice_is_left_out_not_the_first():
    written = _written([_code(1), _code(2), _code(3, "bad()"), _code(3, "bad()"), _code(3, "bad()"),
                        json.dumps({"declare": {"steps": []}})])

    assert list(written.left_out) == ["3"] and {1, 2} <= set(written.steps)
    assert 3 not in written.steps


def test_a_step_failing_once_in_each_of_two_phases_is_left_out_in_the_second():
    runs_of = {}
    done = json.dumps({"declare": {"steps": []}})
    with nobody_answering():
        first = execute(STEPS, FakeTools().registry,
                        ScriptedLLMClient([_code(1, "bad()"), done]), EventStream(),
                        kernel=_BadCodeFails(), runs_of=runs_of)
        second = execute(STEPS, FakeTools().registry,
                         ScriptedLLMClient([_code(1, "bad()"), _code(1, "bad()"), done]), EventStream(),
                         kernel=_BadCodeFails(), runs_of=runs_of)

    assert not first.left_out and list(second.left_out) == ["1"]
