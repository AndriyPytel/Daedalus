"""
Level 2 — introspection against a real Document.

Every property name asserted here is asserted against the running FreeCAD, not
against a fixture. A fixture would happily agree that `sketch.Support` exists;
this build will not.
"""
import json
import math

import pytest

from core.freecad_apply import build_registry, description, fit_view
from core.tools import ToolError

pytestmark = pytest.mark.freecad


@pytest.fixture
def doc():
    import FreeCAD

    document = FreeCAD.newDocument("ToolsLevel2")
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


@pytest.fixture
def part(doc):
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 10, 20, 5
    fillet = doc.addObject("Part::Fillet", "Fillet")
    fillet.Base = box
    doc.recompute()
    return doc


@pytest.fixture
def tools(doc):
    return build_registry(doc)


def test_snapshot_reports_what_exists_without_dumping_the_tree(tools, part):
    snapshot = tools.call("snapshot")

    assert snapshot["document"] == "ToolsLevel2"
    assert [o["name"] for o in snapshot["objects"]] == ["Box", "Fillet"]
    assert snapshot["object_count"] == 2
    assert set(snapshot["objects"][0]) == {"name", "label", "type", "errors"}
    assert snapshot["objects"][0]["type"] == "Part::Box"


def test_snapshot_carries_the_selection_and_the_active_body(tools, part):
    """Headless has no GUI, so both degrade rather than fail."""
    snapshot = tools.call("snapshot")

    assert snapshot["selection"] == []
    assert snapshot["active_body"] is None


def test_fitting_the_view_where_there_is_no_view_is_not_a_failure():
    """
    Every landed Step fits the viewport, and the whole Level 2 suite applies
    Steps without a GUI. A fit that raised here would fail every one of them.
    """
    assert fit_view() is None


def test_the_overall_size_is_what_is_modelled(tools, part):
    """
    The one number a drawing states outright, and the one nothing else in the
    loop measured: Review judged four renders and a list of type names, so
    "a gear the right size" was not a question it could ask.
    """
    assert tools.call("overall_size")["size_mm"] == [10, 20, 5]


def test_the_origin_datums_do_not_become_the_overall_size(tools, doc):
    """
    A Body carries three planes and three axes, each 1e100 across. Taken
    literally they make every part 2e+100 mm wide — which is exactly what the
    old measurement reported to Preflight. A Body brings its
    Origin with it, so adding one is all it takes to put them in the Document.
    """
    doc.addObject("PartDesign::Body", "Body")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 40, 30, 10
    doc.recompute()

    assert tools.call("overall_size")["size_mm"] == [40, 30, 10]


def test_a_document_with_nothing_modelled_has_no_overall_size(tools, doc):
    doc.addObject("Sketcher::SketchObject", "RootProfile")
    doc.recompute()

    assert tools.call("overall_size")["size_mm"] is None


def test_snapshot_of_an_empty_document_is_still_a_snapshot(tools):
    snapshot = tools.call("snapshot")

    assert snapshot["objects"] == []
    assert snapshot["object_count"] == 0


def test_snapshot_reports_an_object_left_in_error(tools, doc):
    """A Cut with neither Base nor Tool is an object the recompute leaves in error."""
    doc.addObject("Part::Cut", "Cut")
    doc.recompute()

    errors = {o["name"]: o["errors"] for o in tools.call("snapshot")["objects"]}

    assert errors["Cut"], "a broken feature looked healthy in the Snapshot"


def test_property_names_come_from_the_running_freecad(tools, part):
    described = tools.call("describe_object", {"name": "Box"})
    names = {p["name"] for p in described["properties"]}

    assert {"Length", "Width", "Height", "Placement", "Label"} <= names
    assert described["type"] == "Part::Box"


def test_the_removed_1_0_api_is_absent_where_a_fixture_would_have_agreed(tools, doc):
    """`Support` and `Plane` were removed in 1.0; `AttachmentSupport` replaced them."""
    doc.addObject("Sketcher::SketchObject", "Sketch")
    doc.recompute()

    names = {p["name"] for p in tools.call("describe_object", {"name": "Sketch"})["properties"]}

    assert "Support" not in names
    assert "Plane" not in names
    assert "AttachmentSupport" in names
    assert "MapMode" in names


def test_properties_carry_their_type_group_documentation_and_value(tools, part):
    properties = {p["name"]: p for p in tools.call("describe_object", {"name": "Box"})["properties"]}

    length = properties["Length"]
    assert length["type"] == "App::PropertyLength"
    assert length["group"] == "Box"
    assert "length of the box" in length["documentation"].lower()
    assert length["value"] == "10.0 mm"


def test_describe_object_summarises_the_shape(tools, part):
    shape = tools.call("describe_object", {"name": "Box"})["shape"]

    assert shape["faces"] == 6
    assert shape["edges"] == 12
    assert shape["volume"] == pytest.approx(1000.0)
    assert shape["bound_box"] == [0, 0, 0, 10, 20, 5]


def test_describe_object_accepts_a_label_when_the_name_does_not_match(tools, part, doc):
    doc.getObject("Box").Label = "Base Plate"
    doc.recompute()

    assert tools.call("describe_object", {"name": "Base Plate"})["name"] == "Box"


def test_describe_object_raises_on_a_label_collision(tools, part, doc):
    import FreeCAD

    param = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Document")
    duplicates_were_allowed = param.GetBool("DuplicateLabels", False)
    param.SetBool("DuplicateLabels", True)
    try:
        doc.getObject("Box").Label = "Profile"
        doc.getObject("Fillet").Label = "Profile"
        doc.recompute()

        with pytest.raises(ToolError) as raised:
            tools.call("describe_object", {"name": "Profile"})
    finally:
        param.SetBool("DuplicateLabels", duplicates_were_allowed)

    assert "Box" in str(raised.value) and "Fillet" in str(raised.value)


def test_describe_object_on_a_ghost_says_what_does_exist(tools, part):
    with pytest.raises(ToolError) as raised:
        tools.call("describe_object", {"name": "Ghost"})

    assert "Box" in str(raised.value) and "Fillet" in str(raised.value)


def test_supported_types_come_from_the_document_itself(tools):
    supported = tools.call("supported_types")

    assert supported["count"] > 50
    assert "Part::Box" in supported["types"]


def test_supported_types_can_be_filtered(tools):
    supported = tools.call("supported_types", {"prefix": "PartDesign::"})

    assert supported["types"], "no PartDesign types in this build"
    assert all(t.startswith("PartDesign::") for t in supported["types"])
    assert "Part::Box" not in supported["types"]


def test_supported_types_does_not_report_a_confident_wrong_absence(tools, doc):
    """
    supportedTypes() only knows about modules that have been imported. A user
    who never opened PartDesign would otherwise be told PartDesign::Pad does
    not exist — which is exactly the kind of wrong-but-certain answer
    introspection is supposed to eliminate.
    """
    raw = {t for t in doc.supportedTypes() if t.startswith("PartDesign::")}
    reported = set(tools.call("supported_types", {"prefix": "PartDesign::"})["types"])

    assert "PartDesign::Pad" in reported
    assert reported >= raw


def test_a_type_is_describable_before_any_object_of_it_exists(tools, doc):
    """
    The gap `supported_types` and `describe_object` leave between them: one
    reports that PartDesign::Pocket exists, the other needs a Pocket to already
    be in the Document.
    """
    described = tools.call("describe_type", {"type_id": "PartDesign::Pocket"})

    assert described["type"] == "PartDesign::Pocket"
    names = {p["name"] for p in described["properties"]}
    assert {"Type", "Length", "Reversed", "Midplane"} <= names
    assert not doc.Objects, "describing a type put something in the Document"


def test_an_enumeration_reports_the_spellings_this_build_accepts(tools):
    """
    `'Through all'` is not one of them, and assigning it is what killed Step 2
    of the recorded run.

    A length is a number, not a choice, and has nothing to spell.
    """
    properties = {p["name"]: p for p in
                  tools.call("describe_type", {"type_id": "PartDesign::Pocket"})["properties"]}

    assert "ThroughAll" in properties["Type"]["enumeration"]
    assert "Through all" not in properties["Type"]["enumeration"]
    assert "enumeration" not in properties["Length"]


def test_describing_a_type_leaves_the_document_and_the_session_as_they_were(tools, part, doc):
    """
    The Tool instantiates the type somewhere, and it may not be here. A scratch
    Document left open, or the user's Document no longer the active one, is the
    same defect as writing to theirs.
    """
    import FreeCAD

    before = [o.Name for o in doc.Objects]
    documents = set(FreeCAD.listDocuments())
    FreeCAD.setActiveDocument(doc.Name)

    tools.call("describe_type", {"type_id": "PartDesign::Pad"})

    assert [o.Name for o in doc.Objects] == before
    assert set(FreeCAD.listDocuments()) == documents
    assert FreeCAD.ActiveDocument is doc


def test_a_type_this_build_does_not_have_says_so_rather_than_inventing_it(tools):
    with pytest.raises(ToolError) as raised:
        tools.call("describe_type", {"type_id": "PartDesign::Pocketing"})

    assert "PartDesign::Pocketing" in str(raised.value)
    assert "supported_types" in str(raised.value)


def test_describe_type_cannot_be_used_to_apply_anything(tools, doc):
    """
    Read-only in the only sense that matters: there is no argument through
    which code, a value or a Document could be passed.
    """
    schema = {described["name"]: described for described in tools.describe()}["describe_type"]

    assert set(schema["parameters"]["properties"]) == {"type_id"}
    assert schema["parameters"]["properties"]["type_id"]["type"] == "string"


def test_dependencies_report_both_directions(tools, part):
    fillet = tools.call("dependencies", {"name": "Fillet"})
    box = tools.call("dependencies", {"name": "Box"})

    assert fillet["depends_on"] == ["Box"]
    assert box["used_by"] == ["Fillet"]


def test_dependencies_on_a_ghost_says_what_does_exist(tools, part):
    with pytest.raises(ToolError, match="No object named 'Ghost'"):
        tools.call("dependencies", {"name": "Ghost"})


def test_materials_carry_the_uuid_shape_material_needs(tools):
    """
    A uuid is the one thing about a material card that cannot be introspected
    and cannot be guessed — `describe_object` reports that ShapeMaterial is a
    Materials::PropertyMaterial and stops there.
    """
    answer = tools.call("materials", {"name": "alumin"})

    assert answer["count"], "this build ships no aluminium card at all"
    for card in answer["materials"]:
        assert "alumin" in card["name"].lower()
        assert card["uuid"], "a card came back with no uuid, which is the point of the Tool"


def test_a_materials_density_is_reported_so_a_card_can_be_chosen_by_it(tools):
    """The prompt says 2700 kg/m³; the card that has it is picked, not edited."""
    with_density = [card for card in tools.call("materials", {"name": "alumin"})["materials"]
                    if card["density"]]

    assert with_density, "no aluminium card reported a density, so none can be chosen by one"
    assert any("2.7" in card["density"] for card in with_density), \
        "no aluminium card reads as 2700 kg/m³: %s" % [c["density"] for c in with_density]


def test_a_card_with_no_density_is_still_listed_rather_than_dropped(tools):
    """Not every card carries every quantity; a missing one is None, not a failure."""
    everything = tools.call("materials", {})["materials"]

    assert everything, "the Tool reported no materials at all"
    assert all("density" in card for card in everything)


def test_materials_unfiltered_is_a_page_and_says_so(tools):
    answer = tools.call("materials", {})

    assert len(answer["materials"]) <= 40
    if answer["count"] > len(answer["materials"]):
        assert "filter by name" in answer["truncated"]


def test_a_name_that_matches_nothing_hands_back_the_shelf_rather_than_nothing(tools):
    """
    The recorded run asked for "plain carbon steel"; the cards are named
    "Steel-Generic". An empty list reads as "this build has no materials", and
    Execute spent three replans on that reading rather than picking a card.
    """
    answer = tools.call("materials", {"name": "unobtainium"})

    assert answer["count"], "a name off a title block came back with nothing to choose from"
    assert "unobtainium" in answer["nothing_matched"]
    assert any("steel" in card["name"].lower() for card in answer["materials"]), \
        "the whole shelf was promised and the steels are not on it"


def _plate(doc, holes=1):
    """
    A plate with Ø8 holes through it, built as a real PartDesign tree.

    A `Part::Box` would describe as a box and prove nothing: the Description's
    first layer is the feature tree, and it is trustworthy only because Lint
    forces the tree to be genuine PartDesign.

    The profile sits on XY and the material is above it, so the cut goes up.
    """
    import FreeCAD as App
    import Part
    import Sketcher

    length = 10 * (holes + 1)
    body = doc.addObject("PartDesign::Body", "Body")
    profile = doc.addObject("Sketcher::SketchObject", "BaseProfile")
    body.addObject(profile)
    corners = [(0, 0), (length, 0), (length, 20), (0, 20)]
    for start, end in zip(corners, corners[1:] + corners[:1]):
        profile.addGeometry(Part.LineSegment(App.Vector(start[0], start[1], 0),
                                             App.Vector(end[0], end[1], 0)), False)
    for edge in range(4):
        profile.addConstraint(Sketcher.Constraint("Coincident", edge, 2, (edge + 1) % 4, 1))
    named = profile.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 20.0))
    profile.renameConstraint(named, "plate_width")

    pad = doc.addObject("PartDesign::Pad", "Pad")
    body.addObject(pad)
    pad.Profile = profile
    pad.Length = 10
    doc.recompute()

    centres = doc.addObject("Sketcher::SketchObject", "HoleProfile")
    body.addObject(centres)
    for index in range(holes):
        centres.addGeometry(Part.Circle(App.Vector(10 + index * 10, 10, 0),
                                        App.Vector(0, 0, 1), 4.0), False)
    hole = doc.addObject("PartDesign::Hole", "Hole")
    body.addObject(hole)
    hole.Profile = centres
    hole.Diameter = 8.0
    hole.DepthType = "ThroughAll"
    hole.Reversed = True
    doc.recompute()
    return doc


@pytest.fixture
def described(doc):
    return description(_plate(doc))


def _feature(described, name):
    return {entry["name"]: entry for entry in described["features"]}[name]


def test_a_feature_is_described_by_its_parameters_not_by_its_type_alone(described):
    """
    `{name, label, type}` says a `PartDesign::Pad` exists and not one dimension
    of it, so there was nothing to judge and the whole weight fell on the
    picture.
    """
    assert _feature(described, "Pad")["parameters"]["Length"] == "10.0 mm"
    assert _feature(described, "Hole")["parameters"]["Diameter"] == "8.0 mm"
    assert _feature(described, "Hole")["parameters"]["DepthType"] == "ThroughAll"


def test_a_hole_reads_off_the_shape_although_nobody_asked_for_it(described):
    """
    The Acceptance list is written before the part exists, so a hole nobody
    thought to name is in no list — but it is in the `Shape` unconditionally.
    """
    assert described["geometry"]["solids"] == 1
    assert [entry for entry in described["geometry"]["surfaces"]
            if entry["kind"] == "Cylinder"] == [
        {"kind": "Cylinder", "diameter_mm": 8.0, "axis": [0.0, 0.0, 1.0], "count": 1}]


def test_the_inventory_reports_how_much_material_there_is(doc):
    """
    A Pocket extruded away from the solid cuts nothing, and the feature tree,
    the face families and the overall size all come out identical whether it
    cut or not. Volume is the one reading that tells the two apart.

    20 x 20 x 10 less one 8mm bore through it.
    """
    plate = description(_plate(doc))["geometry"]

    assert plate["volume_mm3"] == pytest.approx(20 * 20 * 10 - math.pi * 4 ** 2 * 10, rel=1e-3)


def test_twenty_identical_holes_are_one_entry_rather_than_twenty(doc):
    """Without grouping, the inventory of a real part is a dump."""
    surfaces = description(_plate(doc, holes=20))["geometry"]["surfaces"]

    assert [entry for entry in surfaces if entry["kind"] == "Cylinder"] == [
        {"kind": "Cylinder", "diameter_mm": 8.0, "axis": [0.0, 0.0, 1.0], "count": 20}]
    assert len(surfaces) == 2, "the flats grouped into one entry too: %s" % surfaces


def test_the_named_driving_constraints_are_described_and_the_geometry_is_not(described):
    """
    Sketch geometry is the form in which a defect is formally present and
    practically invisible, and it is the most tokens for the least judgement.
    A named constraint is the same fact, said once.
    """
    profile = _feature(described, "BaseProfile")

    assert profile["constraints"] == {"plate_width": "20 mm"}
    assert "Geometry" not in profile.get("parameters", {})
    assert "LineSegment" not in json.dumps(described)


def test_a_document_with_nothing_modelled_is_still_described(doc):
    assert description(doc) == {"features": [],
                                "geometry": {"solids": 0, "volume_mm3": 0.0, "surfaces": []}}


def test_a_bound_box_read_after_tessellation_lands_inside_the_curved_face():
    """
    `Shape.BoundBox` is taken off the triangulation where there is one, so on a
    curved face it reports the mesh's chords and not the surface — inside the
    true extreme, by the sagitta. In the GUI the ViewProvider tessellates every
    shape it draws, while Preflight loads the Document fresh off disk and has
    no triangulation at all: the same Step's code then reads two different
    numbers in the two places, which is how a part came out with every cut
    0.18 mm off X while Preflight saw it exact.

    The face is exact either way, and so is `optimalBoundingBox()`. Where the
    mesh happens to put a vertex the reading is exact, which is why this is a
    trap and not a constant: it is wrong on the sides the chords cut across,
    and only there.
    """
    import Part

    shape = Part.makeCylinder(25, 10)
    assert shape.BoundBox.YMax == pytest.approx(25.0, abs=1e-9)

    shape.tessellate(1.0)

    assert shape.BoundBox.YMax < 25.0 - 1e-3, \
        "this build no longer reads the BoundBox off the mesh; the trap can go"
    assert shape.optimalBoundingBox().YMax == pytest.approx(25.0, abs=1e-6)
    assert shape.cleaned().BoundBox.YMax == pytest.approx(25.0, abs=1e-9)
    faces = [f for f in shape.Faces if type(f.Surface).__name__ == "Cylinder"]
    assert faces[0].Surface.Radius == pytest.approx(25.0, abs=1e-9)


def test_no_open_document_is_a_tool_error_not_a_crash():
    registry = build_registry(lambda: None)

    import FreeCAD
    for document in list(FreeCAD.listDocuments().values()):
        FreeCAD.closeDocument(document.Name)

    with pytest.raises(ToolError, match="no open Document"):
        registry.call("snapshot")
