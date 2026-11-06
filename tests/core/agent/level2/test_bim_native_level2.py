"""
Level 2 -- a native IFC opens in nativeifc under freecadcmd, is edited and saved, and the BIM Domain judges the saved IFC: a named removal lands, a do-nothing finish and any other deletion are refused, each condition of the Assertion fails with its name, and an unedited file scores 0.
"""
import json
import os
import sys

import pytest

import sdk
from core import nativeifc
from core.agent.bim import native_measured, native_refusals
from core.agent.domain import BIM
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from tests.native_helpers import flat

pytestmark = pytest.mark.freecad

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

EDIT_PROPERTY_AND_OBJECT = """
from nativeifc import ifc_tools
import ifcopenshell

window = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcWindow"][0]
window.OverallWidth = App.Units.Quantity("1000 mm")
door = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcDoor"][0]
ifc_tools.remove_ifc_element(door, delete_obj=True)
project = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcProject"][0]
ifc_tools.save_ifc(project, SAVED)
"""
REMOVE_WINDOW2 = """
from nativeifc import ifc_tools
same = [o for o in doc.Objects if getattr(o, "IfcClass", None) in ("IfcWindow", "IfcOpeningElement") and ifc_tools.get_ifc_element(o).Name == "Window_WIN2"]
for gone in sorted(same, key=lambda o: o.IfcClass != "IfcWindow"):
    ifc_tools.remove_ifc_element(gone, delete_obj=True)
doc.recompute()
"""
REMOVE_OPENING_FIRST = """
from nativeifc import ifc_tools
opening = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcOpeningElement" and ifc_tools.get_ifc_element(o).Name == "Window_WIN2"][0]
ifc_tools.remove_ifc_element(opening, delete_obj=True)
doc.recompute()
"""
REMOVE_WINDOW_KEEP_OPENING = """
from nativeifc import ifc_tools
window = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcWindow" and ifc_tools.get_ifc_element(o).Name == "Window_WIN2"][0]
ifc_tools.remove_ifc_element(window, delete_obj=True)
doc.recompute()
"""
REMOVE_DOOR = """
from nativeifc import ifc_tools
door = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcDoor"][0]
ifc_tools.remove_ifc_element(door, delete_obj=True)
doc.recompute()
"""
REMOVE_SPACES = """
from nativeifc import ifc_tools
for space in [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcSpace"]:
    ifc_tools.remove_ifc_element(space, delete_obj=True)
doc.recompute()
"""
UNFILL_WINDOW2 = """
from nativeifc import ifc_tools
window = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcWindow" and ifc_tools.get_ifc_element(o).Name == "Window_WIN2"][0]
element = ifc_tools.get_ifc_element(window)
ifc_tools.api_run("feature.remove_filling", ifc_tools.get_ifcfile(window), element=element)
doc.recompute()
"""
UNPLACE_WALL1 = """
from nativeifc import ifc_tools
wall = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcWall" and o.Label == "Wall_W1"][0]
ifc_tools.api_run("spatial.unassign_container", ifc_tools.get_ifcfile(wall), products=[ifc_tools.get_ifc_element(wall)])
doc.recompute()
"""


ADD_DOOR = """
import ifcopenshell.util.representation
from nativeifc import ifc_tools
project = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcProject"][0]
f = ifc_tools.get_ifcfile(project)
run = lambda command, **kwargs: ifc_tools.api_run(command, f, **kwargs)
wall = [e for e in f.by_type("IfcWall") if e.Name == "Wall_W1"][0]
ctx = ifcopenshell.util.representation.get_context(f, "Model", "Body", "MODEL_VIEW")
building = wall.ContainedInStructure[0].RelatingStructure
cx, cy, W, H = 1.0, 0.0, 0.9, 2.1


def box(xd, yd, h):
    profile = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0., 0.)), None), xd, yd)
    solid = f.createIfcExtrudedAreaSolid(profile, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((cx, cy, 0.)), f.createIfcDirection((0., 0., 1.)), f.createIfcDirection((1., 0., 0.))), f.createIfcDirection((0., 0., 1.)), h)
    return f.createIfcProductDefinitionShape(None, None, [f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", [solid])])


def placement():
    return f.createIfcLocalPlacement(None, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0., 0., 0.)), None, None))


opening = run("root.create_entity", ifc_class="IfcOpeningElement", name="Door_D2")
opening.Representation, opening.ObjectPlacement = box(W, 0.3, H), placement()
run("feature.add_feature", feature=opening, element=wall)
door = run("root.create_entity", ifc_class="IfcDoor", name="Door_D2", predefined_type="DOOR")
door.OverallWidth, door.OverallHeight = W, H
door.Representation, door.ObjectPlacement = box(W, 0.05, H), placement()
run("feature.add_filling", opening=opening, element=door)
run("spatial.assign_container", products=[door], relating_structure=building)
ifc_tools.create_object(door, doc, f)
doc.recompute()
"""


def _say(**turn):
    return json.dumps(turn)


@pytest.fixture(autouse=True)
def geometry_as_found():
    from nativeifc import ifc_generator

    found = ifc_generator.generate_geometry
    yield
    ifc_generator.generate_geometry = found


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=180) as running:
        yield running


@pytest.fixture
def given(kernel, tmp_path):
    ifc = flat(str(tmp_path / "input.ifc"))
    return ifc, sdk.native_document(ifc, str(tmp_path / "work.ifc"), str(tmp_path / "input.FCStd"))


def _edit(kernel, seed, intent, turns, stop=None):
    llm = ScriptedLLMClient(turns)
    client = sdk.Client(document=seed, kernel=kernel, llm=llm)
    result = client.agent(intent, domain=BIM, base=True,
                          cancelled=(lambda: len(llm.calls) >= stop) if stop else None)
    labels = [o.Label for o in client.document.Objects]
    client.close()
    return result, labels, llm


@pytest.fixture(params=["flat", "task"])
def opened(request, tmp_path):
    if request.param == "flat":
        ifc = flat(str(tmp_path / "input.ifc"))
    else:
        ifc = os.path.join(os.environ.get("DAE_TEST_DATA_DIR", os.path.join(os.path.dirname(ROOT), "test-data")),
                           "bim", "edit", "edit_17", "input.ifc")
        if not os.path.isfile(ifc):
            pytest.skip("no BIM benchmark data")
    return ifc, sdk.native_document(ifc, str(tmp_path / "work.ifc"), str(tmp_path / "input.FCStd"))


def test_nativeifc_edits_a_property_and_an_object_headless_and_ifcopenshell_reads_them_back(kernel, opened, tmp_path):
    ifc, seed = opened
    saved = str(tmp_path / "saved.ifc")
    kernel.check("pass", document=seed)

    result = kernel.check("SAVED = %r\n" % saved + EDIT_PROPERTY_AND_OBJECT)

    assert result.ok, result.traceback
    import ifcopenshell
    before, after = ifcopenshell.open(ifc), ifcopenshell.open(saved)
    assert after.schema == "IFC4"
    assert len(after.by_type("IfcDoor")) == len(before.by_type("IfcDoor")) - 1
    assert sorted(w.OverallWidth for w in after.by_type("IfcWindow")) != sorted(w.OverallWidth for w in before.by_type("IfcWindow"))


def test_a_named_removal_lands_without_asking_and_the_assertion_holds(kernel, given):
    result, labels, llm = _edit(kernel, given[1], "Remove Window_WIN2.",
                                [_say(code=REMOVE_WINDOW2), _say(finish="removed")])

    assert result.ok, json.dumps(llm.calls[-1][-1]["content"])
    assert not any(label.startswith("Window_WIN2") for label in labels)


@pytest.mark.parametrize("code, refused", [
    (REMOVE_OPENING_FIRST, "still in the IFC"),
    (REMOVE_WINDOW_KEEP_OPENING, "has no Door or Window filling it"),
])
def test_a_removal_done_in_the_wrong_order_is_refused(kernel, given, code, refused):
    result, _, llm = _edit(kernel, given[1], "Remove Window_WIN2, and close its opening in the wall.",
                           [_say(code=code), _say(finish="removed")], stop=3)

    assert not result.ok
    assert refused in json.dumps(llm.calls[-1][-1]["content"])


def test_every_edit_made_through_ifcopenshell_alone_lands_and_is_the_saved_ifc(kernel, given, tmp_path):
    code = """
from nativeifc import ifc_tools
window = [o for o in doc.Objects if getattr(o, "IfcClass", None) == "IfcWindow" and ifc_tools.get_ifc_element(o).Name == "%s"][0]
ifc_tools.api_run("attribute.edit_attributes", ifc_tools.get_ifcfile(window), product=ifc_tools.get_ifc_element(window), attributes={"OverallWidth": %s})
"""
    turns = [_say(code=code % ("Window_WIN1", 1.4)), _say(code=code % ("Window_WIN2", 1.5)), _say(finish="widened")]
    client = sdk.Client(document=given[1], kernel=kernel, llm=ScriptedLLMClient(turns))
    result = client.agent("Widen Window_WIN1 to 1.4 m and Window_WIN2 to 1.5 m.", domain=BIM, base=True)
    saved = client.export_ifc(str(tmp_path / "saved.ifc"))
    client.close()

    import ifcopenshell
    assert result.ok, result.reason
    assert result.steps_applied == 2
    assert {1.4, 1.5} <= {w.OverallWidth for w in ifcopenshell.open(saved).by_type("IfcWindow")}


def test_a_file_without_storeys_is_not_asked_to_put_its_elements_in_one(kernel, tmp_path):
    seed = sdk.native_document(flat(str(tmp_path / "input.ifc"), storeys=False), str(tmp_path / "work.ifc"),
                               str(tmp_path / "input.FCStd"))

    result, _, _ = _edit(kernel, seed, "Remove Window_WIN2.", [_say(code=REMOVE_WINDOW2), _say(finish="removed")])

    assert result.ok, result.reason


def test_a_door_added_with_a_shape_lands_without_the_live_document_generating_geometry(kernel, tmp_path):
    seed = sdk.native_document(flat(str(tmp_path / "input.ifc"), fills=False), str(tmp_path / "work.ifc"),
                               str(tmp_path / "input.FCStd"))
    llm = ScriptedLLMClient([_say(code=ADD_DOOR), _say(finish="added")])
    client = sdk.Client(document=seed, kernel=kernel, llm=llm)
    result = client.agent("Add Door_D2 to Wall_W1.", domain=BIM, base=True)
    shapes = [o.Shape.isNull() for o in client.document.Objects
              if getattr(o, "IfcClass", None) == "IfcDoor" and o.Label.startswith("Door_D2")]
    client.close()

    assert result.ok, json.dumps(llm.calls[-1][-1]["content"])
    assert shapes == [True]


def test_a_process_with_a_gui_keeps_its_shapes(monkeypatch):
    import FreeCAD
    from nativeifc import ifc_generator

    found = ifc_generator.generate_geometry
    monkeypatch.setattr(FreeCAD, "GuiUp", True)

    assert nativeifc.without_geometry() is False
    assert ifc_generator.generate_geometry is found


def test_the_kernel_and_a_live_document_that_builds_shapes_agree_on_the_signature_of_a_native_edit(kernel, tmp_path):
    import FreeCAD
    import Part
    from core.kernel.kernel_worker import signature_of, signatures_match

    seed = sdk.native_document(flat(str(tmp_path / "input.ifc"), fills=False), str(tmp_path / "work.ifc"),
                               str(tmp_path / "input.FCStd"))
    stepped = kernel.check(ADD_DOOR, document=seed)
    live = FreeCAD.openDocument(seed)
    exec(ADD_DOOR, {"doc": live, "App": FreeCAD})
    door = [o for o in live.Objects if getattr(o, "IfcClass", None) == "IfcDoor" and o.Label.startswith("Door_D2")][0]
    door.Shape = Part.makeBox(900, 50, 2100)
    landed = signature_of(live)
    FreeCAD.closeDocument(live.Name)

    assert stepped.ok, stepped.traceback
    assert signatures_match(landed, stepped.signature)


def test_a_finish_that_changed_nothing_is_refused(kernel, given):
    result, _, llm = _edit(kernel, given[1], "Remove Window_WIN2.", [_say(finish="done"), _say(finish="done")], stop=2)

    assert not result.ok
    assert "given" in json.dumps(llm.calls[1][-1]["content"])


def test_a_deletion_the_intent_does_not_name_is_refused(kernel, given):
    result, labels, _ = _edit(kernel, given[1], "Remove Window_WIN2.", [_say(code=REMOVE_DOOR)], stop=2)

    assert any(label.startswith("Door") for label in labels)
    assert not result.ok


@pytest.mark.parametrize("code, refused", [
    (REMOVE_SPACES, "IfcSpace"),
    (UNFILL_WINDOW2, "Window_WIN2"),
    (UNPLACE_WALL1, "Wall_W1"),
])
def test_each_condition_fails_the_assertion_by_name(kernel, given, code, refused):
    kernel.check("pass", document=given[1])
    edited = kernel.check(code)

    assert edited.ok, edited.traceback
    found = native_refusals(native_measured(kernel))

    assert any(refused in line for line in found), found


def test_a_file_the_edit_left_unchanged_scores_zero():
    sys.path.insert(0, os.path.join(ROOT, "harness"))
    import bim_score

    wall = {"c": [0.0, 0.0], "len": 4.0, "h": 2.6, "thk": 0.2}
    counts = dict.fromkeys(bim_score.TYPES, 1)
    measure = {"opens": True, "counts": counts, "walls": [wall], "spaces": [], "openings": [], "adjacency": []}
    reference = measure | {"counts": counts | {"IfcWall": 2}, "walls": [wall, wall | {"c": [4.0, 0.0]}]}

    assert bim_score.edit_combine(measure, reference, measure)["score"] == 0.0
