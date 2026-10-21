"""A Step may swap the edited Body's BaseFeature for a code-built solid; the edit is still measured against what was imported."""
import pytest

from core.edit_guard import ORIGINAL_BASE
from core.freecad_apply import base_addresses, export_step, run_in_transaction
from core.lint import lint

pytestmark = pytest.mark.freecad

SWAP = """
import Part
rebuilt = doc.addObject("Part::Feature", "Rebuilt")
rebuilt.Shape = Part.makeCylinder(5, 20)
body = doc.getObject("Body")
body.BaseFeature = rebuilt
"""


def test_the_swap_passes_lint_and_leaves_the_imported_base_as_the_measure(tmp_path):
    FreeCAD = pytest.importorskip("FreeCAD")
    import Part

    doc = FreeCAD.newDocument("Swap")
    try:
        imported = doc.addObject("Part::Feature", "Imported")
        imported.Shape = Part.makeBox(10, 10, 10)
        imported.Label = ORIGINAL_BASE
        body = doc.addObject("PartDesign::Body", "Body")
        body.BaseFeature = imported
        doc.recompute()
        before = base_addresses(doc)["base"]

        assert lint(SWAP).ok
        run_in_transaction(doc, SWAP)

        assert body.BaseFeature.Name == "Rebuilt"
        assert base_addresses(doc)["base"] == before
        assert body.Shape.isValid() and abs(body.Shape.Volume - 1570.796) < 0.01
        written = export_step(doc, str(tmp_path / "out.step"), "Body")
        assert Part.read(written).Volume == pytest.approx(body.Shape.Volume, rel=1e-3)
    finally:
        FreeCAD.closeDocument(doc.Name)
