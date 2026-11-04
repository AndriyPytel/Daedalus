"""
Level 2 -- a text-only Intent is built part by part: each part in its own Session on its own Document, then joined in the Assembly that passes the Assertion.
"""
import json
import re

import pytest

import sdk
from core.kernel import find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad

BODY = """
import Part
body = doc.addObject("PartDesign::Body", "Body")
sketch = body.newObject("Sketcher::SketchObject", "Sketch")
sketch.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
sketch.MapMode = "FlatFace"
for a, b in (((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 10)), ((0, 10), (0, 0))):
    sketch.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sketch
pad.Length = %s
doc.recompute()
print("SUBMARK %s")
"""

JOIN = """
import JointObject
assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
assembly.Type = "Assembly"
group = assembly.newObject("Assembly::JointGroup", "Joints")


def bring(path, label):
    source = App.openDocument(path)
    body = doc.copyObject(source.getObject("Body"), True)
    App.closeDocument(source.Name)
    body.Label = label
    assembly.addObject(body)
    return body


a = bring("%(PlateA)s", "PlateA")
b = bring("%(PlateB)s", "PlateB")
doc.recompute()
JointObject.GroundedJoint(group.newObject("App::FeaturePython", "Ground"), a)
joint = group.newObject("App::FeaturePython", "Joint")
JointObject.Joint(joint, JointObject.JointTypes.index("Fixed"))
joint.Reference1 = [a, ["", ""]]
joint.Reference2 = [b, ["", ""]]
joint.Offset1 = App.Placement(App.Vector(10, 0, 0), App.Rotation())
joint.Offset2 = App.Placement()
doc.recompute()
assembly.solve()
"""


class Script(ScriptedLLMClient):
    """Fills `%(Name)s` in a turn with the path the last `build_part` answer gave for that part."""

    def __init__(self, turns):
        super().__init__(turns)
        self.paths = {}

    def stream(self, messages, **overrides):
        last = messages[-1]["content"]
        found = re.search(r'"name": "(\w+)".*?"path": "([^"]+)"', last if isinstance(last, str) else "")
        if found:
            self.paths[found.group(1)] = found.group(2)
        self.turns[0] = self.turns[0] % self.paths if "%(" in self.turns[0] else self.turns[0]
        return super().stream(messages, **overrides)


def _part(name, height):
    return json.dumps({"build_part": {"name": name, "intent": "%s, a 10 x 10 x %s plate." % (name, height)}})


def _code(text):
    return json.dumps({"code": text})


def _finish(text):
    return json.dumps({"finish": text})


@pytest.fixture
def client():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    llm = Script([
        _part("PlateA", 5), _code(BODY % (5, "a")), _finish("built"),
        _part("PlateB", 5), _code(BODY % (5, "b")), _finish("built"),
        _code(JOIN), _finish("joined"),
    ])
    running = sdk.Client(llm=llm)
    yield running, llm
    running.close()


def _texts(messages):
    return " ".join(m["content"] if isinstance(m["content"], str) else json.dumps(m["content"]) for m in messages)


def test_two_parts_built_by_sub_agents_are_joined_into_an_assembly_that_passes_the_assertion(client):
    running, llm = client

    result = running.agent("PARENTMARK build two plates.", domain=sdk.ASSEMBLY)

    assert result.ok, result.reason
    assembly = [o for o in running.document.Objects if o.TypeId == "Assembly::AssemblyObject"]
    assert len(assembly) == 1
    assert sorted(o.Label for o in assembly[0].Group if o.TypeId == "PartDesign::Body") == ["PlateA", "PlateB"]


def test_the_parent_never_sees_a_sub_agents_transcript_and_the_sub_agent_never_sees_the_parents(client):
    running, llm = client

    running.agent("PARENTMARK build two plates.", domain=sdk.ASSEMBLY)

    parent, sub = llm.calls[0], llm.calls[2]
    assert "PARENTMARK" in _texts(parent) and "SUBMARK" not in _texts(parent)
    assert "SUBMARK" in _texts(sub) and "PARENTMARK" not in _texts(sub)
    final = llm.calls[-1]
    told = _texts(final)
    assert "SUBMARK" not in told and "Sketcher::" not in told, told[told.find("SUBMARK") - 300:told.find("SUBMARK") + 100]
    assert '"volume"' in _texts(final) and '"faces"' in _texts(final)


def test_a_sub_agent_that_spends_its_share_of_the_cap_answers_not_ok_and_the_parent_carries_on():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    llm = Script([_part("PlateA", 5), _code(BODY % (5, "a")), _code(BODY.replace("Body", "Body2") % (5, "a")),
                  _finish("gave up")])
    llm.last_cost = {"usd": 3.0}
    running = sdk.Client(llm=llm)
    try:
        running.agent("PARENTMARK build a plate.", domain=sdk.ASSEMBLY, budget_usd=10.0,
                      cancelled=lambda: sum(e.data.get("usd", 0) for e in running.events if e.kind == "cost") >= 10)
    finally:
        running.close()

    told = _texts(llm.calls[-1])
    assert '"ok": false' in told and "budget" in told
