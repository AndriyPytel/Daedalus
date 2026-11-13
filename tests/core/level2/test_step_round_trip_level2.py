"""
Level 2 -- a Step whose Body is valid in memory but does not survive a STEP round trip is refused.

OCC's `isValid()` is True for the Body below, and the STEP written from it reads back with
unorientable faces in every precision mode: a racetrack pad stood on a flange that has a lobe
blended into it, the ledge left on the lobe being the bad face.
"""
import math

import pytest

pytestmark = pytest.mark.freecad

FreeCAD = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")
V = FreeCAD.Vector

END_R = SIDE_Y = 99.29
END_C = 384.62


def _document_with(*pads):
    doc = FreeCAD.newDocument("round_trip")
    body = doc.addObject("PartDesign::Body", "Body")
    plane = [f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane"][0]
    for name, z, length, draw in pads:
        sketch = doc.addObject("Sketcher::SketchObject", name)
        body.addObject(sketch)
        sketch.AttachmentSupport = [(plane, "")]
        sketch.MapMode = "FlatFace"
        sketch.AttachmentOffset = FreeCAD.Placement(V(0, 0, z), FreeCAD.Rotation())
        draw(sketch)
        doc.recompute()
        pad = doc.addObject("PartDesign::Pad", name + "Pad")
        body.addObject(pad)
        pad.Profile = sketch
        pad.Length = length
        doc.recompute()
    return doc


def _racetrack(sketch):
    axis = V(0, 0, 1)
    sketch.addGeometry(Part.LineSegment(V(-END_C, SIDE_Y, 0), V(END_C, SIDE_Y, 0)), False)
    sketch.addGeometry(Part.ArcOfCircle(Part.Circle(V(END_C, 0, 0), axis, END_R), -math.pi / 2, math.pi / 2), False)
    sketch.addGeometry(Part.LineSegment(V(END_C, -SIDE_Y, 0), V(-END_C, -SIDE_Y, 0)), False)
    sketch.addGeometry(Part.ArcOfCircle(Part.Circle(V(-END_C, 0, 0), axis, END_R), math.pi / 2, 3 * math.pi / 2), False)


def _lobe(sketch):
    def unit(a, b):
        d = b - a
        return d * (1.0 / d.Length)

    def arc(centre, radius, start, end):
        a = math.atan2(start.y - centre.y, start.x - centre.x)
        b = math.atan2(end.y - centre.y, end.x - centre.x)
        sweep = (b - a) % (2 * math.pi)
        if sweep > math.pi:
            a, b, sweep = b, a, 2 * math.pi - sweep
        mid = a + sweep / 2
        return Part.ArcOfCircle(start, V(centre.x + radius * math.cos(mid), centre.y + radius * math.sin(mid), 0), end)

    lobe_r, blend_r, chord_y = 46.76, 36.56, 89.29
    lobe, inner, outer, end = V(-339.17, 99.54, 0), V(-264.178, 135.85, 0), V(-415.813, 132.22, 0), V(-END_C, 0, 0)
    on_side = V(inner.x, SIDE_Y, 0)
    on_end = end + unit(end, outer) * END_R
    lobe_inner = lobe + unit(lobe, inner) * lobe_r
    lobe_outer = lobe + unit(lobe, outer) * lobe_r
    for piece in (Part.LineSegment(on_side, V(inner.x, chord_y, 0)),
                  Part.LineSegment(V(inner.x, chord_y, 0), V(on_end.x, chord_y, 0)),
                  Part.LineSegment(V(on_end.x, chord_y, 0), on_end),
                  arc(outer, blend_r, on_end, lobe_outer),
                  arc(lobe, lobe_r, lobe_outer, lobe_inner),
                  arc(inner, blend_r, lobe_inner, on_side)):
        sketch.addGeometry(piece, False)


def _flange_with_a_lobe_under_an_arm_pad():
    return _document_with(("Flange", 0, 21, _racetrack), ("Lobe", 0, 21, _lobe), ("Arms", 21, 103, _racetrack))


@pytest.fixture
def broken_body():
    doc = _flange_with_a_lobe_under_an_arm_pad()
    yield doc
    FreeCAD.closeDocument(doc.Name)


def test_the_body_is_valid_in_memory_and_still_refused_after_a_round_trip(broken_body):
    from core.kernel.step_rules import _body_defects

    assert broken_body.Body.Shape.isValid()
    refusal = _body_defects(broken_body)

    assert "does not survive a STEP round trip" in refusal
    assert "Body:" in refusal
    assert "plane" in refusal and "area" in refusal


def test_the_refusal_names_at_most_five_faces(broken_body):
    from core.manifold import step_round_trip_fc

    assert step_round_trip_fc(broken_body.Body.Shape).count("plane") <= 5


def test_a_body_that_survives_the_round_trip_is_not_refused():
    from core.kernel.step_rules import _body_defects
    from core.manifold import step_round_trip_fc

    doc = _document_with(("Flange", 0, 21, _racetrack), ("Arms", 21, 103, _racetrack))
    try:
        assert step_round_trip_fc(doc.Body.Shape) is None
        assert _body_defects(doc) is None
    finally:
        FreeCAD.closeDocument(doc.Name)
