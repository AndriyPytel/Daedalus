"""
Level 2 — the render Review judges by, against a real FreeCAD.

The renderer is software-rasterised because a headless FreeCAD has no GL
context, and the two things that made it stop resembling the viewport were a
mean-depth sort instead of a depth test, and no edges at all. Both are checked
on the pixels, which is the only place either shows.

What is deliberately not checked: anti-aliasing, per-object colour, materials
and perspective. Review does not judge by those, and the render is not trying
to be a screenshot.
"""
import numpy as np
import pytest
from PIL import Image

from core.kernel.preflight_render import _BACKGROUND, _EDGE, render

pytestmark = pytest.mark.freecad


@pytest.fixture
def doc():
    import FreeCAD

    document = FreeCAD.newDocument("RenderLevel2")
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


def _rendered(doc, tmp_path, name):
    doc.recompute()
    path = str(tmp_path / (name + ".png"))
    assert render(doc, path) == path
    return np.array(Image.open(path).convert("RGB"))


def _a_box(doc, size=40, name="Box"):
    box = doc.addObject("Part::Box", name)
    box.Length = box.Width = box.Height = size
    return box


def _shades(image):
    """The face greys: every colour that is neither background nor an edge."""
    colours = {tuple(c) for c in np.unique(image.reshape(-1, 3), axis=0)}
    return colours - {_BACKGROUND, _EDGE}


def test_a_box_is_drawn_with_its_edges(doc, tmp_path):
    """
    Without edges, two adjacent faces of a similar shade merge into one blob —
    the larger half of why the render did not look like the viewport.
    """
    _a_box(doc)

    image = _rendered(doc, tmp_path, "box")

    drawn = int((image == np.array(_EDGE)).all(axis=2).sum())
    assert drawn > 500, "the box has no edges drawn: %d pixels" % drawn


def test_a_box_shows_three_distinguishable_face_shades(doc, tmp_path):
    """An isometric cube shows exactly three faces, and they must not match."""
    _a_box(doc)

    shades = sorted(_shades(_rendered(doc, tmp_path, "box")))

    assert len(shades) == 3, "expected three face shades, got %s" % (shades,)
    greys = sorted(shade[0] for shade in shades)
    assert min(b - a for a, b in zip(greys, greys[1:])) >= 8, \
        "two faces are too close to tell apart: %s" % greys


def test_nothing_at_all_renders_to_nothing(doc, tmp_path):
    assert render(doc, str(tmp_path / "empty.png")) is None


def test_a_bodys_origin_does_not_set_the_framing(doc, tmp_path):
    """
    A PartDesign Body brings an Origin with it: three axes and three datum
    planes, each 1e100 across. Taken literally they own the bounding box and
    the part lands in one pixel — and their edges, sampled per pixel, ask for
    an array of 1e100 points. Neither is geometry; the viewport does not draw
    them either. A Body brings its Origin with it, so adding one is all it
    takes to put them in the Document.
    """
    doc.addObject("PartDesign::Body", "Body")
    _a_box(doc, name="Solid")

    image = _rendered(doc, tmp_path, "body")

    drawn = int((image != np.array(_BACKGROUND)).any(axis=2).sum())
    assert drawn > 40000, \
        "the part is %d pixels — the Origin's datums took the framing" % drawn
    assert len(_shades(image)) == 3, "the datum planes were shaded as surfaces"


def _a_rod_through(doc):
    """A rod poking out of both ends of the box — in front of it and behind it."""
    import FreeCAD

    rod = doc.addObject("Part::Cylinder", "Rod")
    rod.Radius, rod.Height = 5, 90
    rod.Placement = FreeCAD.Placement(FreeCAD.Vector(20, 20, -25), FreeCAD.Rotation(0, 0, 0))
    return rod


def test_a_solid_wholly_inside_another_shows_no_pixel_of_itself(doc, tmp_path):
    """
    A ball buried in the middle of a cube. Nothing of it can be visible, and it
    cannot move the framing either — its bounding box is inside the cube's — so
    the two renders must be the same file, pixel for pixel.

    Measured against the mean-depth sort this replaces: 1280 pixels of the ball
    came through the front of the cube.
    """
    import FreeCAD

    _a_box(doc)
    box_alone = _rendered(doc, tmp_path, "box-alone")

    ball = doc.addObject("Part::Sphere", "Ball")
    ball.Radius = 12
    ball.Placement = FreeCAD.Placement(FreeCAD.Vector(20, 20, 20), FreeCAD.Rotation(0, 0, 0))
    with_ball = _rendered(doc, tmp_path, "box-and-ball")

    leaked = int((box_alone != with_ball).any(axis=2).sum())
    assert not leaked, "%d pixels of a buried solid came through" % leaked


def test_the_part_of_a_rod_outside_the_box_is_still_drawn(doc, tmp_path):
    """
    The other half of the same claim: occlusion, not deletion. The cube alone
    is three flat greys; the rod's curved surface is many, so the shades are
    what say some of it survived.
    """
    _a_box(doc)
    _a_rod_through(doc)

    shades = _shades(_rendered(doc, tmp_path, "box-and-rod"))

    assert len(shades) > 10, \
        "a rod poking out of both ends of the box added %d shades" % (len(shades) - 3)


def test_the_result_does_not_depend_on_the_order_the_objects_were_added(doc, tmp_path):
    """
    A depth test is order-independent; a painter's sort is only as good as its
    key. Adding the rod first must render the same picture.
    """
    _a_box(doc)
    _a_rod_through(doc)
    box_first = _rendered(doc, tmp_path, "box-first")

    import FreeCAD
    other = FreeCAD.newDocument("RenderLevel2Reversed")
    try:
        _a_rod_through(other)
        _a_box(other)
        rod_first = _rendered(other, tmp_path, "rod-first")
    finally:
        FreeCAD.closeDocument(other.Name)

    assert np.array_equal(box_first, rod_first)


def _padded_body(doc):
    import FreeCAD
    import Part

    body = doc.addObject("PartDesign::Body", "Body")
    sketch = body.newObject("Sketcher::SketchObject", "Sketch")
    sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
    sketch.MapMode = "FlatFace"
    sketch.addGeometry(Part.Circle(FreeCAD.Vector(), FreeCAD.Vector(0, 0, 1), 20))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sketch
    pad.Length = 40
    hole = body.newObject("Sketcher::SketchObject", "HoleSketch")
    hole.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
    hole.MapMode = "FlatFace"
    hole.addGeometry(Part.Circle(FreeCAD.Vector(), FreeCAD.Vector(0, 0, 1), 10))
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = hole
    pocket.Type = "ThroughAll"
    pocket.Reversed = True
    doc.recompute()
    return body


def test_a_body_is_drawn_as_it_ends_and_not_as_each_feature_stood(doc):
    """A Pad's Shape is the Body before the Pocket; drawn too, it closed the bored top a recorded run's Review judged by."""
    from core.kernel.preflight_render import _tessellated

    body = _padded_body(doc)
    assert body.Shape.Volume < doc.getObject("Pad").Shape.Volume

    triangles, _ = _tessellated(doc)

    assert len(triangles) == len(body.Shape.tessellate(0.2)[1])


def test_the_source_a_body_edits_is_not_drawn_beside_the_body(doc):
    """A Body whose BaseFeature is a Part::Feature already holds that shape once; tessellating both doubles the render's cost."""
    import Part

    source = doc.addObject("Part::Feature", "Source")
    source.Shape = Part.makeBox(40, 40, 40)
    body = doc.addObject("PartDesign::Body", "Body")
    body.BaseFeature = source
    doc.recompute()
    assert not body.Shape.isNull() and not source.Shape.isNull()

    from core.kernel.preflight_render import _tessellated

    triangles, _ = _tessellated(doc)

    assert len(triangles) == len(body.Shape.tessellate(0.2)[1])


def test_the_batched_rasteriser_draws_the_pixels_the_triangle_by_triangle_one_did(doc, tmp_path, monkeypatch):
    """The depth test is per pixel, so the batch must agree with the single-triangle fill on every pixel, edges included."""
    from core.kernel import preflight_render

    _padded_body(doc)
    batched = _rendered(doc, tmp_path, "batched")

    def one_by_one(pixels, depth, size):
        width, height = size
        zbuffer = np.full((height, width), -np.inf)
        owner = np.full((height, width), -1, dtype=np.int64)
        for index in range(len(pixels)):
            preflight_render._fill_triangle(zbuffer, owner, index, pixels[index], depth[index])
        return zbuffer, owner

    monkeypatch.setattr(preflight_render, "_rasterize", one_by_one)
    single = _rendered(doc, tmp_path, "single")

    assert (batched != single).any(axis=2).sum() == 0
