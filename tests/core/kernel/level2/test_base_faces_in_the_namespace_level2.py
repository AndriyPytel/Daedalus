"""Level 2 -- a real kernel hands a Step's code the base part's addresses as `base_faces` when the call carries them, and not otherwise."""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

FACES = {"plane z = 10": 100.0, "cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45": 478.9}


def _check(code, **kwargs):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    import FreeCAD
    import Part

    doc = FreeCAD.newDocument("Faces")
    doc.addObject("Part::Feature", "Imported").Shape = Part.makeBox(1, 1, 1)
    doc.recompute()
    path = doc.Name + ".FCStd"
    import os
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), path)
    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)
    with Kernel(timeout=120) as worker:
        return worker.check(code, path, **kwargs)


def test_the_code_reads_base_faces_as_the_address_to_area_map():
    result = _check("print(sorted(base_faces))", base_faces=FACES)

    assert result.ok, result.traceback
    assert "plane z = 10" in result.stdout and "cone axis" in result.stdout


def test_a_call_without_base_faces_has_no_such_name():
    result = _check("print(sorted(base_faces))")

    assert not result.ok and "NameError" in result.traceback
