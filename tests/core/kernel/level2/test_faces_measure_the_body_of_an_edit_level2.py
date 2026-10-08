"""Level 2 -- on an edit the check namespace measures the Body being built, never the imported source it started from; part 202 is that shape of Document."""
import pytest

from core.kernel import kernel_worker
from core.loop.expressions import each_expression_evaluated
from tests.core.kernel import part_202

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def part():
    import FreeCAD

    document = part_202.opened()
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


def _namespace(part):
    return kernel_worker.step_namespace(part, design={"datums": []})


def test_the_fixture_is_an_edit_with_a_source_and_a_body(part):
    body = part.getObject("Body")
    assert body.BaseFeature is not None and body.BaseFeature.TypeId == "Part::Feature"


def test_the_faces_measured_are_the_bodys_and_not_the_sources(part):
    body = part.getObject("Body")
    namespace = _namespace(part)

    assert namespace["_faces"].shape.isSame(body.Shape)
    assert not namespace["_faces"].shape.isSame(body.BaseFeature.Shape)


def test_face_at_and_cylinder_return_faces_of_the_body(part):
    body = part.getObject("Body")
    namespace = _namespace(part)
    face = namespace["face_at"]("+z")

    assert any(face.isSame(each) for each in body.Shape.Faces)
    assert not any(face.isSame(each) for each in body.BaseFeature.Shape.Faces)
    shape = namespace["_faces"].shape
    circle = next(f for f in shape.Faces if f.Surface.__class__.__name__ == "Cylinder")
    diameter, axis = round(circle.Surface.Radius * 2, 3), "xyz"[max(range(3), key=lambda i: abs((circle.Surface.Axis.x, circle.Surface.Axis.y, circle.Surface.Axis.z)[i]))]
    same = [f for f in shape.Faces if f.Surface.__class__.__name__ == "Cylinder"
            and abs(f.Surface.Radius * 2 - diameter) < 1e-3 and abs(getattr(f.Surface.Axis, axis)) > 0.99]
    found = namespace["cylinder"](diameter, axis, expect=len(same))
    found = found if isinstance(found, list) else [found]
    assert all(any(f.isSame(each) for each in body.Shape.Faces) for f in found)


def test_part_240_style_expressions_evaluate_without_raising(part):
    expressions = {
        0: "abs(distance(face_at('+z'), face_at('+z'))) <= 0.5",
        1: "face_at('+z').Area > 0",
        2: "face_at('-y').Area > 0 and face_at('+y').Area > 0",
        3: "abs(distance(face_at('-y'), face_at('+y')) - _faces.shape.BoundBox.YLength) <= 0.5",
        4: "len([f for f in _faces.shape.Faces if f.Surface.__class__.__name__ == 'Cylinder']) > 0",
    }
    namespace = _namespace(part)

    exec(compile(each_expression_evaluated(expressions), "<expressions>", "exec"), namespace)

    assert namespace["defects"] == []
    assert namespace["held"] == [0, 1, 2, 3, 4]


def test_a_larger_solid_outside_the_body_does_not_take_the_measurement(part):
    import FreeCAD

    decoy = part.addObject("Part::Box", "Decoy")
    decoy.Length = decoy.Width = decoy.Height = 5000
    part.recompute()
    try:
        namespace = _namespace(part)
        assert namespace["_faces"].shape.isSame(part.getObject("Body").Shape)
    finally:
        part.removeObject(decoy.Name)
        part.recompute()
