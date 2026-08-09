"""
Level 2 — introspection against a real Document.

Every property name asserted here is asserted against the running FreeCAD, not
against a fixture. A fixture would happily agree that `sketch.Support` exists;
this build will not (ADR-0006, ADR-0007).
"""
import json

import pytest

from core.freecad_tools import build_registry, description
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


# ── Snapshot ──────────────────────────────────────────────────────────────────


def test_snapshot_reports_what_exists_without_dumping_the_tree(tools, part):
    snapshot = tools.call("snapshot")

    assert snapshot["document"] == "ToolsLevel2"
    assert [o["name"] for o in snapshot["objects"]] == ["Box", "Fillet"]
    assert snapshot["object_count"] == 2
    # A summary per object, not its properties.
    assert set(snapshot["objects"][0]) == {"name", "label", "type", "errors"}
    assert snapshot["objects"][0]["type"] == "Part::Box"


def test_snapshot_carries_the_selection_and_the_active_body(tools, part):
    snapshot = tools.call("snapshot")

    # Headless has no GUI, so both degrade rather than fail.
    assert snapshot["selection"] == []
    assert snapshot["active_body"] is None


def test_the_overall_size_is_what_is_modelled(tools, part):
    """
    The one number a drawing states outright, and the one nothing else in the
    loop measured: Review judged four renders and a list of type names, so
    "a gear the right size" was not a question it could ask. FCC-66.
    """
    assert tools.call("overall_size")["size_mm"] == [10, 20, 5]


def test_the_origin_datums_do_not_become_the_overall_size(tools, doc):
    """
    A Body carries three planes and three axes, each 1e100 across. Taken
    literally they make every part 2e+100 mm wide — which is exactly what the
    old measurement reported to Preflight before FCC-66.
    """
    doc.addObject("PartDesign::Body", "Body")     # brings its Origin with it
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
    doc.addObject("Part::Cut", "Cut")   # no Base/Tool
    doc.recompute()

    errors = {o["name"]: o["errors"] for o in tools.call("snapshot")["objects"]}

    assert errors["Cut"], "a broken feature looked healthy in the Snapshot"


# ── Introspection: the property names are the running FreeCAD's ───────────────


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


def test_describe_object_on_a_ghost_says_what_does_exist(tools, part):
    with pytest.raises(ToolError) as raised:
        tools.call("describe_object", {"name": "Ghost"})

    assert "Box" in str(raised.value) and "Fillet" in str(raised.value)


# ── Supported types ───────────────────────────────────────────────────────────


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


# ── Dependencies ──────────────────────────────────────────────────────────────


def test_dependencies_report_both_directions(tools, part):
    fillet = tools.call("dependencies", {"name": "Fillet"})
    box = tools.call("dependencies", {"name": "Box"})

    assert fillet["depends_on"] == ["Box"]
    assert box["used_by"] == ["Fillet"]


def test_dependencies_on_a_ghost_says_what_does_exist(tools, part):
    with pytest.raises(ToolError, match="No object named 'Ghost'"):
        tools.call("dependencies", {"name": "Ghost"})


# ── Materials (FCC-38) ────────────────────────────────────────────────────────


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


def test_a_name_that_matches_nothing_is_an_empty_list_not_an_error(tools):
    assert tools.call("materials", {"name": "unobtainium"}) == {"materials": [], "count": 0}


# ── The Description Review judges by (FCC-81) ─────────────────────────────────


def _plate(doc, holes=1):
    """
    A plate with Ø8 holes through it, built as a real PartDesign tree.

    A `Part::Box` would describe as a box and prove nothing: the Description's
    first layer is the feature tree, and it is trustworthy only because Lint
    forces the tree to be genuine PartDesign (ADR-0012).
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
    # The profile sits on XY and the material is above it, so the cut goes up.
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
    picture. FCC-81.
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
    assert description(doc) == {"features": [], "geometry": {"solids": 0, "surfaces": []}}


# ── No Document ───────────────────────────────────────────────────────────────


def test_no_open_document_is_a_tool_error_not_a_crash():
    registry = build_registry(lambda: None)

    import FreeCAD
    for document in list(FreeCAD.listDocuments().values()):
        FreeCAD.closeDocument(document.Name)

    with pytest.raises(ToolError, match="no open Document"):
        registry.call("snapshot")
