"""
Level 2 -- `sdk.Client.agent`, the reader loop, against a real headless kernel and a real FreeCAD.
"""
import json
import os

import pytest

from core.events import APPLIED, BUDGET_EXHAUSTED, COMPLETED, COST, PREFLIGHT_FAILED
from core.agent import reader
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad

BOX = """
import Part
body = doc.addObject('PartDesign::Body', 'Body')
sketch = body.newObject('Sketcher::SketchObject', 'Sketch')
sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]
sketch.MapMode = 'FlatFace'
for a, b in [((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 10)), ((0, 10), (0, 0))]:
    sketch.addGeometry(Part.LineSegment(App.Vector(*a, 0), App.Vector(*b, 0)))
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 10
doc.recompute()
"""

TALLER = """
import Part
body = doc.getObject('Body')
sketch = body.newObject('Sketcher::SketchObject', 'Sketch2')
sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]
sketch.MapMode = 'FlatFace'
for a, b in [((2, 2), (5, 2)), ((5, 2), (5, 5)), ((5, 5), (2, 5)), ((2, 5), (2, 2))]:
    sketch.addGeometry(Part.LineSegment(App.Vector(*a, 0), App.Vector(*b, 0)))
pad = body.newObject('PartDesign::Pad', 'Pad2')
pad.Profile = sketch
pad.Length = 15
doc.recompute()
"""


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
    from sdk import Client

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
        _say(inspect={"tool": "describe_object", "arguments": {"name": "Body"}}),
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
    assert "nope" in _user_text(llm.calls[2])
    assert '"volume": 1000' in _user_text(llm.calls[4])
    assert any(block.get("type") == "image_url" for block in llm.calls[5][-1]["content"])

    saved = client.export_step(str(tmp_path / "out.step"))
    assert _volume_of(saved) == pytest.approx(1000)
    client.close()


def test_a_run_saved_the_way_run_py_saves_it_is_a_solid_a_scorer_can_read(kernel, tmp_path):
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "tools"))
    import run

    llm = ScriptedLLMClient([_say(code=BOX), _say(finish="a 10 mm cube")])
    client = _client(llm, kernel)
    result = client.agent("Make a 10 mm cube")

    step, fcstd = str(tmp_path / "out.step"), str(tmp_path / "out.FCStd")
    assert run.finish(client, result, step) == 0
    assert run.finish(client, result, fcstd) == 0

    client.close()
    import FreeCAD

    assert _volume_of(step) == pytest.approx(1000)
    reopened = FreeCAD.openDocument(fcstd)
    assert any(getattr(o, "Shape", None) is not None and o.Shape.Volume == pytest.approx(1000)
               for o in reopened.Objects)
    FreeCAD.closeDocument(reopened.Name)


def test_an_agent_out_of_turns_is_stopped_and_keeps_what_it_built(kernel, tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_TURNS", 2)
    llm = ScriptedLLMClient([_say(code=BOX), _say(probe="print(1)"), _say(probe="print(2)")])
    client = _client(llm, kernel)

    result = client.agent("Make a 10 mm cube")

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
        _say(code=BOX),
        _say(code="import os\nos._exit(1)"),
        _say(code=TALLER),
        _say(finish="done"),
    ])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert "kernel died" in _user_text(llm.calls[2])
    assert result.ok and result.steps_applied == 2
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


LOOSE_BOX = "doc.addObject('Part::Box', 'Box')\ndoc.recompute()"
SWALLOWED = "try:\n    doc.addObject('Part::Box', 'Box')\nexcept:\n    pass"


def test_a_code_call_that_lint_refuses_never_reaches_the_kernel(kernel):
    from core.lint import lint

    llm = ScriptedLLMClient([_say(code=SWALLOWED), _say(code=BOX), _say(finish="done")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert result.ok and result.steps_applied == 1
    assert json.dumps(lint(SWALLOWED).report())[1:-1] in _user_text(llm.calls[1])
    assert _kinds(client).count(PREFLIGHT_FAILED) == 1
    assert [o.Name for o in client.document.Objects if o.Name == "Box"] == []
    client.close()


def test_a_finish_without_a_body_feature_tree_is_refused_and_says_why(kernel):
    llm = ScriptedLLMClient([_say(code=LOOSE_BOX), _say(finish="a box"),
                             _say(code="doc.removeObject('Box')\n" + BOX),
                             _say(finish="a Body this time")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert result.ok and result.reason == "a Body this time"
    assert "PartDesign::Body" in _user_text(llm.calls[2])
    assert _kinds(client).count(COMPLETED) == 1
    client.close()


def test_an_agent_that_never_builds_a_feature_tree_is_not_finished(kernel):
    llm = ScriptedLLMClient([_say(code=LOOSE_BOX), _say(finish="a box"), _say(finish="still a box")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert COMPLETED not in _kinds(client)
    assert not result.ok
    client.close()


def test_the_agent_builds_into_the_clients_own_document(kernel):
    llm = ScriptedLLMClient([_say(code=BOX), _say(finish="done")])
    client = _client(llm, kernel)
    document = client.document

    client.agent("Make a cube")

    assert client.document is document
    assert [o.TypeId for o in document.Objects if o.TypeId == "PartDesign::Body"] == ["PartDesign::Body"]
    client.close()


def test_a_design_intent_goes_to_the_agent_unless_the_pipeline_is_asked_for(kernel):
    llm = ScriptedLLMClient([_say(code=BOX), _say(finish="done")])
    client = _client(llm, kernel)

    result = client.send("Make a cube", route_first=False, choice="part")

    assert result.ok
    client.close()


def test_a_run_given_its_own_stream_emits_every_event_into_it(kernel):
    from core.events import EventStream

    llm = ScriptedLLMClient([_say(code=BOX), _say(finish="done")])
    client = _client(llm, kernel)
    run = []

    client.send("Make a cube", route_first=False, choice="part", events=EventStream([run.append]))

    kinds = [event.kind for event in run]
    assert APPLIED in kinds and COMPLETED in kinds
    assert client.seen == []
    client.close()


def test_a_run_seeds_the_kernel_once(kernel, monkeypatch):
    seeds = []
    real = kernel.seed

    def recording(*args, **kwargs):
        seeds.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(kernel, "seed", recording)
    llm = ScriptedLLMClient([_say(code=BOX), _say(code="print(1)"), _say(finish="done")])
    client = _client(llm, kernel)

    client.agent("Make a cube")

    assert len(seeds) == 1
    client.close()


def test_a_code_call_that_changes_nothing_is_refused_in_the_models_next_turn(kernel):
    from core.prompts import read_prompt

    llm = ScriptedLLMClient([_say(code="print(1)"), _say(code=BOX), _say(finish="done")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    refusal = json.loads(llm.calls[1][-1]["content"])
    assert refusal["status"] == "failed" and refusal["gate"] == "changed_nothing"
    assert refusal["traceback"] == read_prompt("changed_nothing.md")
    assert result.ok and result.steps_applied == 1
    assert _kinds(client).count(PREFLIGHT_FAILED) == 1
    client.close()
