"""
Level 2 — introspection against a real Document.

Every property name asserted here is asserted against the running FreeCAD, not
against a fixture. A fixture would happily agree that `sketch.Support` exists;
this build will not (ADR-0006, ADR-0007).
"""
import pytest

from core.freecad_tools import build_registry
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


# ── No Document ───────────────────────────────────────────────────────────────


def test_no_open_document_is_a_tool_error_not_a_crash():
    registry = build_registry(lambda: None)

    import FreeCAD
    for document in list(FreeCAD.listDocuments().values()):
        FreeCAD.closeDocument(document.Name)

    with pytest.raises(ToolError, match="no open Document"):
        registry.call("snapshot")
