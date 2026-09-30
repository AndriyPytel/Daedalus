"""
Level 2 -- the two comparison arms of `copilot.sdk.Client`, `agent` and `oneshot`,
against a real headless kernel and a real FreeCAD: one model choosing its own
route over the kernel, and one model call executed once.
"""
import json
import os

import pytest

from core.events import (APPLIED, BUDGET_EXHAUSTED, COMPLETED, COST, DESIGN, FAILED, GATE,
                         PLAN, PREFLIGHT_FAILED, REVIEW_VERDICT)
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad

BOX = ("b = doc.addObject('Part::Box', 'Box')\nb.Length = 10\nb.Width = 10\nb.Height = 10\n"
       "doc.recompute()")


def _say(**turn):
    return json.dumps(turn)


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=120) as running:
        yield running


def _client(llm, kernel):
    from copilot.sdk import Client

    client = Client(kernel=kernel, llm=llm)
    seen = []
    client.watch(seen.append)
    client.seen = seen
    return client


def _kinds(client):
    return [event.kind for event in client.seen]


def _volume_of(step_path):
    import Part

    return Part.read(step_path).Volume


def _user_text(call):
    content = call[-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


def test_the_agent_picks_its_own_route_and_the_step_it_built_is_exported(kernel, tmp_path):
    llm = ScriptedLLMClient([
        _say(probe="print(len(doc.Objects))"),
        _say(code="raise ValueError('nope')"),
        _say(code=BOX),
        _say(inspect={"tool": "describe_object", "arguments": {"name": "Box"}}),
        _say(render=True),
        _say(finish="a 10 mm cube"),
    ])
    client = _client(llm, kernel)

    result = client.agent("Make a 10 mm cube")

    assert result.ok, result.reason
    assert result.steps_applied == 1
    kinds = _kinds(client)
    assert kinds.count(COST) == 6
    assert kinds.count(APPLIED) == 1
    assert kinds.count(PREFLIGHT_FAILED) == 1
    assert COMPLETED in kinds
    assert not {DESIGN, PLAN, GATE, REVIEW_VERDICT} & set(kinds)
    assert "nope" in _user_text(llm.calls[2])
    assert '"volume": 1000' in _user_text(llm.calls[4])
    assert any(block.get("type") == "image_url" for block in llm.calls[5][-1]["content"])

    saved = client.export_step(str(tmp_path / "out.step"))
    assert _volume_of(saved) == pytest.approx(1000)
    client.close()


def test_an_agent_out_of_turns_is_stopped_and_keeps_what_it_built(kernel, tmp_path):
    llm = ScriptedLLMClient([_say(code=BOX), _say(probe="print(1)"), _say(probe="print(2)")])
    client = _client(llm, kernel)

    result = client.agent("Make a 10 mm cube", max_turns=2)

    assert result.ok and result.steps_applied == 1
    assert BUDGET_EXHAUSTED in _kinds(client)
    assert len(llm.calls) == 2
    assert _volume_of(client.export_step(str(tmp_path / "out.step"))) == pytest.approx(1000)
    client.close()


def test_an_agent_is_stopped_by_the_budget_ceiling(kernel):
    llm = ScriptedLLMClient([_say(code=BOX), _say(probe="print(1)")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube", cancelled=lambda: len(llm.calls) >= 1)

    assert len(llm.calls) == 1
    assert BUDGET_EXHAUSTED in _kinds(client)
    assert result.steps_applied == 1
    client.close()


def test_a_crash_in_the_agents_code_does_not_end_the_run(kernel):
    llm = ScriptedLLMClient([
        _say(code="import os\nos._exit(1)"),
        _say(code=BOX),
        _say(finish="done"),
    ])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert "kernel died" in _user_text(llm.calls[1])
    assert result.ok and result.steps_applied == 1
    client.close()


def test_a_reply_that_is_no_tool_call_is_asked_again(kernel):
    llm = ScriptedLLMClient(["I think a cube.", _say(code=BOX), _say(finish="done")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert "JSON" in _user_text(llm.calls[1])
    assert result.ok
    client.close()


def test_the_agent_that_built_nothing_has_nothing_to_export(kernel):
    llm = ScriptedLLMClient([_say(finish="I give up")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert not result.ok and result.steps_applied == 0
    client.close()


def test_oneshot_is_one_call_executed_once_and_exported(tmp_path):
    from copilot.sdk import NO_KERNEL

    llm = ScriptedLLMClient(["Here you go:\n```python\n%s\n```" % BOX])
    from copilot.sdk import Client

    client = Client(kernel=NO_KERNEL, llm=llm)
    seen = []
    client.watch(seen.append)

    result = client.oneshot("Make a 10 mm cube")

    assert result.ok, result.reason
    assert len(llm.calls) == 1
    kinds = [event.kind for event in seen]
    assert kinds.count(COST) == 1 and kinds.count(APPLIED) == 1 and COMPLETED in kinds
    assert _volume_of(client.export_step(str(tmp_path / "out.step"))) == pytest.approx(1000)
    client.close()


def test_oneshot_does_not_retry_a_script_that_raises_and_keeps_the_geometry_it_made():
    from copilot.sdk import NO_KERNEL, Client

    llm = ScriptedLLMClient(["```python\n%s\nmissing_name()\n```" % BOX])
    client = Client(kernel=NO_KERNEL, llm=llm)
    seen = []
    client.watch(seen.append)

    result = client.oneshot("Make a cube")

    assert not result.ok
    assert "missing_name" in result.reason
    assert len(llm.calls) == 1
    kinds = [event.kind for event in seen]
    assert PREFLIGHT_FAILED in kinds and FAILED in kinds
    assert [o.Name for o in client.document.Objects] == ["Box"]
    client.close()


def _a_box_pipeline(code_turns):
    return ScriptedLLMClient(["design",
                              json.dumps({"declare": {"design": {}, "question": ""}}),
                              json.dumps({"declare": {"steps": ["Add a 10mm cube"]}})]
                             + code_turns
                             + [json.dumps({"declare": {"steps": [{"step": 1, "label": "box"}]}}),
                                json.dumps({"declare": {"suspected": []}}),
                                json.dumps({"declare": {"verdict": "pass", "reason": "a cube"}})])


class _WatchedKernel(Kernel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.building = []

    def check(self, code, **kwargs):
        if kwargs.get("builds"):
            self.building.append(code)
        return super().check(code, **kwargs)


def test_nopreflight_lands_a_step_through_apply_alone_and_the_pipeline_still_finishes(tmp_path):
    from copilot.sdk import Client
    from core.loop.budgets import Budgets

    llm = _a_box_pipeline([_say(code="doc.addObject('Part::Box', 'Box')", step=1, label="box")])
    with _WatchedKernel(timeout=120) as watched:
        client = Client(kernel=watched, llm=llm)
        seen = []
        client.watch(seen.append)

        result = client.send("Add a 10mm cube", budgets=Budgets(preflight=False))

        assert result.ok, result.reason
        assert watched.building == []
        assert [o.Name for o in client.document.Objects] == ["Box"]
        assert REVIEW_VERDICT in [event.kind for event in seen]
        client.close()


def test_nopreflight_hands_an_apply_failure_back_like_any_other(tmp_path):
    from copilot.sdk import Client
    from core.loop.budgets import Budgets

    llm = _a_box_pipeline([
        _say(code="missing_name()", step=1, label="bad"),
        _say(code="doc.addObject('Part::Box', 'Box')", step=1, label="box")])
    with _WatchedKernel(timeout=120) as watched:
        client = Client(kernel=watched, llm=llm)
        seen = []
        client.watch(seen.append)

        result = client.send("Add a 10mm cube", budgets=Budgets(preflight=False))

        assert result.ok, result.reason
        failed = [e for e in seen if e.kind == PREFLIGHT_FAILED]
        assert [e.data["gate"] for e in failed] == ["rollback"]
        assert "missing_name" in failed[0].data["traceback"]
        client.close()
