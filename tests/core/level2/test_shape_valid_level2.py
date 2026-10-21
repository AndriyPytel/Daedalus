"""The `shape_valid` Tool answers for the modelled solids of a real Document."""
import pytest

from core.freecad_apply import build_registry

pytestmark = pytest.mark.freecad


def test_a_box_is_a_valid_shape():
    FreeCAD = pytest.importorskip("FreeCAD")
    doc = FreeCAD.newDocument("ShapeValid")
    try:
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        assert build_registry(doc).call("shape_valid") == {"valid": True}
    finally:
        FreeCAD.closeDocument(doc.Name)
