"""
Level 2 -- the grader's mesh timeout, caught before `finish`.

`steps/149.step` is a BRepCheck-valid, watertight solid whose sub-millimetre fillets and chamfers make
the grader's mesher run past its 180 s timeout, which scores a valid part 0. The other five are
recorded STEPs that scored above 0.9 and must pass.

`steps/00013697.step` meshes in 0.1 s and reads back whole, but the grader exports it again before it
scores, and the STEP that export writes does not read back within hours.
"""
import pytest

pytestmark = pytest.mark.freecad

Part = pytest.importorskip("FreeCAD") and pytest.importorskip("Part")

import os

STEPS = os.path.join(os.path.dirname(__file__), "steps")
SCORED_WELL = ("132", "128", "147", "218", "105")


def _read(name):
    return Part.read(os.path.join(STEPS, name + ".step"))


def test_the_step_the_grader_cannot_mesh_is_refused():
    from core.manifold import unmeshable_fc

    assert unmeshable_fc(_read("149"), limit=10) is not None


def test_the_refusal_names_the_sub_millimetre_features():
    from core.manifold import unmeshable_fc

    message = unmeshable_fc(_read("149"), limit=10)

    assert "sub-millimetre fillets and chamfers" in message
    assert "mm wide at (" in message


def test_the_step_the_grader_cannot_read_back_after_its_own_export_is_refused():
    from core.manifold import unmeshable_fc

    assert unmeshable_fc(_read("00013697"), limit=10) is not None


@pytest.mark.parametrize("name", SCORED_WELL)
def test_a_step_that_scored_well_is_not_refused(name):
    from core.manifold import unmeshable_fc

    assert unmeshable_fc(_read(name)) is None


@pytest.fixture
def kernel():
    from core.kernel.kernel import Kernel, find_freecadcmd

    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


BODY = """
import FreeCAD as App
import Part
body = doc.addObject("PartDesign::Body", "%(name)s")
sketch = doc.addObject("Sketcher::SketchObject", "%(name)sSketch")
body.addObject(sketch)
corners = [(%(x)d, 0), (%(x)d + 20, 0), (%(x)d + 20, 20), (%(x)d, 20)]
for start, end in zip(corners, corners[1:] + corners[:1]):
    sketch.addGeometry(Part.LineSegment(App.Vector(start[0], start[1], 0), App.Vector(end[0], end[1], 0)), False)
pad = doc.addObject("PartDesign::Pad", "%(name)sPad")
body.addObject(pad)
pad.Profile = sketch
pad.Length = 10
doc.recompute()
"""


def _facts(kernel, code):
    from core.agent.reader import PART_FACTS

    built = kernel.rehearse(code)
    assert built.ok, built.traceback
    return kernel.measure(PART_FACTS, timeout=90)


def test_a_good_solid_is_measured_meshable_and_reads_back_valid(kernel):
    facts = _facts(kernel, BODY % {"name": "Body", "x": 0})

    assert facts["tree_faults"] == [] and facts["solids"] == 1
    assert facts["unmeshable"] is None and facts["step_readback"] is True


def test_a_document_of_two_bodies_has_two_solids(kernel):
    from core.agent.reader import refusals

    facts = _facts(kernel, BODY % {"name": "One", "x": 0} + BODY % {"name": "Two", "x": 100})

    assert facts["solids"] == 2
    assert "2 separate solids" in refusals(facts, None, asserting=False)[0]


def test_a_broken_tree_skips_the_mesh(kernel):
    facts = _facts(kernel, "obj = doc.addObject('Part::Feature', 'Solid')\nobj.Shape = Part.makeBox(10, 10, 10)\ndoc.recompute()")

    assert facts["tree_faults"] == ["Solid is a Part::Feature, not a PartDesign::Body"]
    assert "unmeshable" not in facts and "step_readback" not in facts


@pytest.mark.parametrize("name", SCORED_WELL)
def test_a_step_that_scored_well_reads_back_valid_and_manifold(name):
    from core.manifold import mesh_facts_fc

    assert mesh_facts_fc(_read(name)) == {"unmeshable": None, "step_readback": True}
