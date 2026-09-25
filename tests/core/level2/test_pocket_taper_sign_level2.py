"""
Level 2 — the sign of `TaperAngle` for an inward Pocket draft.

A Pocket's material-removal direction and its taper direction are separate
knobs, and only a real cut on a real Body answers which sign of `TaperAngle`
narrows the profile going in. This measures it once and pins
`config/prompts/system/execute.md` to state the same sign.
"""
import pathlib

import FreeCAD as App
import Part
import pytest

pytestmark = pytest.mark.freecad

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXECUTE_MD = ROOT / "config" / "prompts" / "system" / "execute.md"

NEGATIVE_NARROWS = ("A Pocket's inward draft — the profile narrowing as it "
                    "goes deeper — takes a negative `TaperAngle`; a positive "
                    "one widens it going in.")
POSITIVE_NARROWS = ("A Pocket's inward draft — the profile narrowing as it "
                    "goes deeper — takes a positive `TaperAngle`; a negative "
                    "one widens it going in.")


@pytest.fixture
def doc():
    document = App.newDocument("TaperSignLevel2")
    try:
        yield document
    finally:
        App.closeDocument(document.Name)


def _tapered_pocket(doc, angle):
    """A round Pocket, 10 mm deep, cut into a 20 mm pad with the given `TaperAngle`."""
    body = doc.addObject("PartDesign::Body", "Body")
    profile = doc.addObject("Sketcher::SketchObject", "BaseProfile")
    body.addObject(profile)
    corners = [(0, 0), (50, 0), (50, 50), (0, 50)]
    for start, end in zip(corners, corners[1:] + corners[:1]):
        profile.addGeometry(Part.LineSegment(App.Vector(start[0], start[1], 0),
                                             App.Vector(end[0], end[1], 0)), False)
    pad = doc.addObject("PartDesign::Pad", "Pad")
    body.addObject(pad)
    pad.Profile = profile
    pad.Length = 20
    doc.recompute()

    hole = doc.addObject("Sketcher::SketchObject", "HoleProfile")
    body.addObject(hole)
    hole.Placement = App.Placement(App.Vector(0, 0, 20), App.Rotation())
    hole.addGeometry(Part.Circle(App.Vector(25, 25, 0), App.Vector(0, 0, 1), 8.0), False)
    pocket = doc.addObject("PartDesign::Pocket", "Pocket")
    body.addObject(pocket)
    pocket.Profile = hole
    pocket.Length = 10
    pocket.TaperAngle = angle
    doc.recompute()
    return pocket


def _hole_area(shape, z):
    """The area of the pocket's own hole wire at height `z`, the smaller of the two the slice returns."""
    wires = sorted(shape.slice(App.Vector(0, 0, 1), z), key=lambda w: w.BoundBox.XLength)
    return Part.Face(wires[0]).Area


def test_a_negative_taper_angle_narrows_the_pocket_going_in(doc):
    pocket = _tapered_pocket(doc, -5)

    opening = _hole_area(pocket.Shape, 19.99)
    bottom = _hole_area(pocket.Shape, 10.01)

    assert bottom < opening, "TaperAngle -5 widened the pocket going in: %.3f at the opening, %.3f at the bottom" % (opening, bottom)


def test_a_positive_taper_angle_widens_the_pocket_going_in(doc):
    pocket = _tapered_pocket(doc, 5)

    opening = _hole_area(pocket.Shape, 19.99)
    bottom = _hole_area(pocket.Shape, 10.01)

    assert bottom > opening, "TaperAngle +5 narrowed the pocket going in: %.3f at the opening, %.3f at the bottom" % (opening, bottom)


def test_execute_md_states_the_sign_this_test_just_measured(doc):
    pocket = _tapered_pocket(doc, -5)
    negative_narrows = _hole_area(pocket.Shape, 10.01) < _hole_area(pocket.Shape, 19.99)
    expected = NEGATIVE_NARROWS if negative_narrows else POSITIVE_NARROWS

    text = " ".join(EXECUTE_MD.read_text(encoding="utf-8").split())

    assert expected in text, "execute.md does not state the sign the test measured"
