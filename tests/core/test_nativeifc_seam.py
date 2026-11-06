"""
Level 2 -- a Document that is no nativeifc project is untouched by the nativeifc seam of the kernel: no IFC digest in its snapshot or signature, nothing to save, and its objects keep their Labels as names.
"""
import FreeCAD
import pytest

from core import nativeifc
from core.kernel.kernel_worker import _properties_snapshot, signature_of, signatures_match

pytestmark = pytest.mark.freecad


@pytest.fixture
def document():
    doc = FreeCAD.newDocument("Plain")
    doc.addObject("Part::Box", "Box").Label = "Cube"
    doc.recompute()
    yield doc
    FreeCAD.closeDocument(doc.Name)


def test_a_plain_document_has_no_ifc_in_its_digest_signature_or_names(document):
    box = document.getObject("Box")

    assert nativeifc.projects(document) == [] and nativeifc.fingerprints(document) == {}
    assert "IFC" not in _properties_snapshot(document)["Box"]
    assert set(signature_of(document)) == {"count", "names", "volume", "links"}
    assert signature_of(document)["volume"] == pytest.approx(box.Shape.Volume)
    assert nativeifc.name(box) == "Cube"


def test_a_signature_without_ifc_matches_one_without_ifc(document):
    assert signatures_match(signature_of(document), signature_of(document))
