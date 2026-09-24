"""
Level 2 -- `pick_edges`, `bbox` and `by_label` do by construction what the
lint and FreeCAD's own tolerance padding otherwise refuse or mislead: a
counted edge selection, an exact box, and a lookup that does not silently
pick the wrong object.

`A_BOX_PAD` pads a 200 x 120 rectangle 24 mm, standing in for the
Opus/Sonnet Steps in `logs/sessions/60d790aa735b4bbeae48df6be4f24d48.jsonl`
and `logs/sessions/f63f0e4e8a914513b83d7013f39dd7d0.jsonl` that were refused
or rolled back for exactly these three reasons.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


A_BOX_PAD = """
body = doc.addObject('PartDesign::Body', 'Body')
xy = doc.getObject('XY_Plane')
sk = doc.addObject('Sketcher::SketchObject', 'BoxSketch')
body.addObject(sk)
sk.AttachmentSupport = [(xy, '')]
sk.MapMode = 'FlatFace'
sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(200, 0, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(200, 0, 0), App.Vector(200, 120, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(200, 120, 0), App.Vector(0, 120, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(0, 120, 0), App.Vector(0, 0, 0)), False)
doc.recompute()
pad = doc.addObject('PartDesign::Pad', 'BoxPad')
pad.Profile = sk
pad.Label = 'BasePad'
pad.Length = 24
body.addObject(pad)
doc.recompute()

def is_top_edge(edge):
    return all(abs(v.Point.z - 24.0) < 1e-6 for v in edge.Vertexes)
"""


def test_pick_edges_finds_the_four_top_edges(worker):
    code = A_BOX_PAD + "held = pick_edges(body.Shape, is_top_edge, 4)\n"

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert len(result.held) == 4


def test_pick_edges_with_the_wrong_expect_names_what_it_found(worker):
    code = A_BOX_PAD + "pick_edges(body.Shape, is_top_edge, 5)\n"

    result = worker.check(code, None)

    assert not result.ok
    assert "Edge" in result.traceback
    assert "5" in result.traceback and "4" in result.traceback


def test_bbox_of_the_padded_box_is_exact(worker):
    code = A_BOX_PAD + (
        "box = bbox(body.Shape)\n"
        "held = [box.XLength, box.YLength, box.ZLength]\n"
    )

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert result.held == pytest.approx([200.0, 120.0, 24.0], abs=1e-6)


def test_by_label_finds_the_one_object_with_that_label(worker):
    code = A_BOX_PAD + "held = [by_label(doc, 'BasePad').Name]\n"

    result = worker.check(code, None)

    assert result.ok, result.traceback
    assert result.held == ["BoxPad"]


def test_by_label_with_no_match_lists_the_labels_it_found(worker):
    code = A_BOX_PAD + "by_label(doc, 'NoSuchThing')\n"

    result = worker.check(code, None)

    assert not result.ok
    assert "BasePad" in result.traceback
