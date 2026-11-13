"""
Level 2 -- `sdk.Client.agent(domain=...)`: the Assembly Domain refuses `finish` until its Assertion holds, and the Part Domain is the loop it was.
"""
import json

import pytest

from core.agent.domain import ASSEMBLY, BIM
from core.events import APPLIED, COMPLETED, COST, DOMAIN, TOOL_RESULT, EventStream
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from tests.assembly_helpers import PARTS, SOLVED
from tests.bim_helpers import FLAT, WITHOUT_SPACE

pytestmark = pytest.mark.freecad


JOIN = """
import JointObject
assembly = doc.getObject('Assembly')
joint = doc.getObject('Joints').newObject('App::FeaturePython', 'Joint')
JointObject.Joint(joint, JointObject.JointTypes.index('Fixed'))
joint.Reference1 = [doc.getObject('A'), ['', '']]
joint.Reference2 = [doc.getObject('B'), ['', '']]
joint.Offset1 = App.Placement(App.Vector(10, 0, 0), App.Rotation())
doc.recompute()
assembly.solve()
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
    client.seen = []
    client.watch(client.seen.append)
    return client


def _refusals(client):
    return [e.data["result"]["text"] for e in client.seen
            if e.kind == TOOL_RESULT and e.data.get("name") == "finish_refused"]


def test_an_assembly_is_built_refused_for_its_missing_joint_then_accepted(kernel, tmp_path):
    llm = ScriptedLLMClient([
        _say(code=PARTS + "JointObject.GroundedJoint(group.newObject('App::FeaturePython', 'G'), a)\n"),
        _say(finish="assembled"),
        _say(code=JOIN),
        _say(finish="assembled"),
    ])
    client = _client(llm, kernel)

    result = client.agent("Assemble the two boxes.", domain=ASSEMBLY)

    assert result.ok, result.reason
    assert "no joint" in _refusals(client)[0]
    kinds = [e.kind for e in client.seen]
    assert kinds.count(APPLIED) == 2 and COMPLETED in kinds
    client.close()


def test_a_solved_assembly_is_accepted_without_a_feature_tree(kernel):
    llm = ScriptedLLMClient([_say(code=SOLVED), _say(finish="assembled")])
    client = _client(llm, kernel)

    result = client.agent("Assemble the two boxes.", domain=ASSEMBLY)

    assert result.ok and not _refusals(client)
    client.close()


def test_the_part_domain_still_refuses_a_loose_solid(kernel):
    llm = ScriptedLLMClient([_say(code="import Part\nf = doc.addObject('Part::Feature', 'F')\n"
                                       "f.Shape = Part.makeBox(1, 1, 1)"),
                             _say(finish="a box")])
    client = _client(llm, kernel)

    client.agent("Make a box.", cancelled=lambda: len(llm.calls) >= 2)

    assert "not a PartDesign::Body" in _refusals(client)[0]
    client.close()


def test_the_assembly_prompt_carries_the_api_and_its_traps(kernel):
    llm = ScriptedLLMClient([_say(finish="x")])
    client = _client(llm, kernel)

    client.agent("Assemble.", domain=ASSEMBLY, cancelled=lambda: len(llm.calls) >= 1)

    system = llm.calls[0][0]["content"]
    client.close()
    assert "Assembly::AssemblyObject" in system and "setJointConnectors" in system


def test_a_domain_that_does_not_exist_is_refused(kernel):
    client = _client(ScriptedLLMClient([]), kernel)

    with pytest.raises(ValueError, match="no such Domain"):
        client.agent("Assemble.", domain="teapot")
    client.close()


def test_the_domain_comes_from_the_documents_assembly_with_no_call_and_from_the_model_otherwise(kernel):
    llm = ScriptedLLMClient(["part"])
    client = _client(llm, kernel)

    client.send("Make a bracket.", route_first=False, cancelled=lambda: len(llm.calls) >= 1)
    client.document.addObject("Assembly::AssemblyObject", "Assembly")
    client.send("Make a bracket.", route_first=False, cancelled=lambda: len(llm.calls) >= 1)

    assert [e.data["domain"] for e in client.seen if e.kind == DOMAIN] == ["part", ASSEMBLY]
    assert len(llm.calls) == 1
    client.close()


def test_a_subscribed_but_still_empty_stream_receives_the_domain_and_its_cost(kernel):
    client = _client(ScriptedLLMClient(["part"]), kernel)
    seen = []
    stream = EventStream([seen.append])

    client.send("Make a bracket.", route_first=False, events=stream, cancelled=lambda: True)
    client.close()

    assert {DOMAIN, COST} <= {event.kind for event in seen}


def test_a_flat_without_a_space_is_refused_then_accepted_once_it_has_one(kernel):
    mend = ("import Arch\nspace = Arch.makeSpace(doc.getObject('Room'))\n"
            "level = [o for o in doc.Objects if getattr(o, 'IfcType', '') == 'Building Storey'][0]\n"
            "level.addObject(space)\ndoc.recompute()")
    llm = ScriptedLLMClient([_say(code=WITHOUT_SPACE), _say(finish="a flat"), _say(code=mend),
                             _say(finish="a flat")])
    client = _client(llm, kernel)

    result = client.agent("Model the flat.", domain=BIM)

    assert result.ok, result.reason
    assert "no Space" in _refusals(client)[0]
    client.close()


def test_a_valid_flat_is_accepted_and_written_as_ifc4(kernel, tmp_path):
    llm = ScriptedLLMClient([_say(code=FLAT), _say(finish="a flat")])
    client = _client(llm, kernel)

    result = client.agent("Model the flat.", domain=BIM)

    assert result.ok and not _refusals(client)
    path = client.export_ifc(str(tmp_path / "flat.ifc"))
    assert "IFC4" in open(path).read(400)
    client.close()


def test_the_bim_prompt_teaches_the_arch_api_and_its_traps(kernel):
    llm = ScriptedLLMClient([_say(finish="x")])
    client = _client(llm, kernel)

    client.agent("Model the flat.", domain=BIM, cancelled=lambda: len(llm.calls) >= 1)

    system = llm.calls[0][0]["content"]
    client.close()
    assert "Arch.makeWall" in system and "Hosts" in system and "makeSpace" in system
