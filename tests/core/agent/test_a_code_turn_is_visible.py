"""
A code turn is readable while it runs: the code it is running, and what the
kernel printed or raised when it stopped.
"""
import json

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.events import Event, EventStream, TOOL_CALL, TOOL_RESULT
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient
from core.narrate import narrate
from tests.event_helpers import events_of


class OneKernel:
    def __init__(self, result):
        self._result = result

    def check(self, code, **kwargs):
        return self._result


def test_the_code_a_turn_runs_is_announced_before_it_runs(tmp_path):
    """
    The panel builds its named, boxed row off `TOOL_CALL`; the agent path
    emitted only `TOOL_RESULT`, so a Step's code was never shown as code and
    the user read the raw reply stream instead.
    """
    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')"}),
        json.dumps({"declare": {"built": "it"}}),
    ])
    stream = EventStream()

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
              kernel=OneKernel(KernelResult(ok=True)), stream=stream)

    called = events_of(stream, TOOL_CALL)
    assert [event.data["arguments"]["code"] for event in called] == [
        "doc.addObject('Part::Box', 'Box')"]
    assert list(stream).index(called[0]) < list(stream).index(events_of(stream, TOOL_RESULT)[0])


def _result(**payload):
    return Event(kind=TOOL_RESULT, data={"name": "execute", "result": payload})


def test_what_a_step_printed_is_narrated_under_its_status():
    said = narrate(_result(status="ok", ok=True, stdout="vol 696349.54 solids 1\n"))

    assert said.status == "execute → ok"
    assert "696349.54" in said.body
    assert said.title


def test_a_step_that_raised_says_so_and_carries_the_traceback():
    said = narrate(_result(status="failed", ok=False,
                           stdout="vol before 570685.835\n",
                           traceback="AssertionError: right bore removed 0.000"))

    assert said.status == "execute → failed"
    assert "570685.835" in said.body
    assert "AssertionError" in said.body


def test_a_quiet_step_that_worked_grows_no_empty_fold():
    said = narrate(_result(status="ok", ok=True))

    assert said.status == "execute → ok"
    assert not said.title and not said.body
