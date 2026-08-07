"""
Level 2 — Preflight checks the Step against the operation the Plan named (FCC-66).

Preflight used to measure the result: solids, faces, volume, a bounding box.
That check worked on geometry a planner can integrate in its head and went
silent on everything else, so `config/prompts/plan.md` told Plan to promise
only what it could work out — and Plan obliged, promising on two Steps of
eighteen. What is checked now is the operation and its numbers, which Plan
writes for every Step and which are exact for a tooth flank as much as for a
cylinder.

Run against a real headless FreeCAD through the real Preflight Worker: what is
being checked is what FreeCAD built, not what a fake would agree to (ADR-0007).
"""
import pytest

from core.preflight import PreflightWorker, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with PreflightWorker() as running:
        yield running


BOX = ("box = doc.addObject('Part::Box', 'Box')\n"
       "box.Length, box.Width, box.Height = 40, 30, 10\n")

# The Step the 2026-08-07 gear run died on: a Pad of the root circle, in a
# Body, whose code was right and whose measurement was not.
PAD_IN_A_BODY = (
    "body = doc.addObject('PartDesign::Body', 'Body')\n"
    "sketch = doc.addObject('Sketcher::SketchObject', 'RootProfile')\n"
    "body.addObject(sketch)\n"
    "sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 17.5), False)\n"
    "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
    "body.addObject(pad)\n"
    "pad.Profile = sketch\n"
    "pad.Length = 10\n")


def pad(**properties):
    return {"type": "PartDesign::Pad", "properties": properties}


def test_a_step_that_builds_what_the_plan_named_passes(worker):
    result = worker.check(BOX, None, parameters={
        "type": "Part::Box", "properties": {"Length": 40, "Width": 30, "Height": 10}})

    assert result.ok, result.traceback


def test_a_pad_in_a_body_is_no_longer_refused_for_being_counted_twice(worker):
    """
    The failure this ticket exists for. A Body carries its Tip's Shape, so the
    old measurement saw one Pad as two solids and refused a Step whose code was
    correct — three times over, until the run was blocked.
    """
    result = worker.check(PAD_IN_A_BODY, None,
                          parameters=pad(Length=10, Profile="RootProfile"))

    assert result.ok, result.traceback


def test_a_number_the_step_did_not_build_is_refused(worker):
    result = worker.check(PAD_IN_A_BODY, None, parameters=pad(Length=12))

    assert not result.ok, "a Pad of 10 mm passed as the 12 mm the Plan named"


def test_the_refusal_names_the_property_the_plan_and_what_was_built(worker):
    result = worker.check(PAD_IN_A_BODY, None, parameters=pad(Length=12))

    said = result.traceback
    assert "did not build the operation the Plan named" in said, said
    assert "Length: the Plan named 12, the Step built 10" in said, said
    assert "against the Plan's numbers" in said, said


def test_a_step_that_built_another_type_than_the_plan_named_is_refused(worker):
    result = worker.check(BOX, None, parameters=pad(Length=10))

    assert not result.ok
    assert "PartDesign::Pad" in result.traceback
    assert "Part::Box" in result.traceback


def test_a_step_that_built_nothing_of_the_named_type_is_refused(worker):
    """
    ADR-0010 catches a Step that changed nothing at all. A Step that changed
    something else is what this catches: the sketch grew a circle, and the Pad
    the Plan named was never created.
    """
    grew_a_circle = (
        "sketch = doc.addObject('Sketcher::SketchObject', 'RootProfile')\n"
        "sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 17.5), False)\n")

    result = worker.check(grew_a_circle, None, parameters=pad(Length=10))

    assert not result.ok
    assert "PartDesign::Pad" in result.traceback


@pytest.fixture
def a_padded_body(tmp_path):
    """A saved Document that already has the Pad, so a Step can only edit it."""
    import FreeCAD

    doc = FreeCAD.newDocument("Edited")
    try:
        exec(compile(PAD_IN_A_BODY, "<setup>", "exec"),  # noqa: S102
             {"doc": doc, "App": FreeCAD, "Part": __import__("Part")})
        doc.recompute()
        path = str(tmp_path / "edited.FCStd")
        doc.saveAs(path)
    finally:
        FreeCAD.closeDocument(doc.Name)
    return path


def test_a_step_that_edits_an_existing_feature_is_checked_too(worker, a_padded_body):
    """
    Most Steps create; a Step that changes an existing Pad's Length creates
    nothing, and would otherwise read as "the Step built nothing". What the
    Step touched is observed either way, so the check reads what it modified.
    """
    lengthen = "doc.getObject('Pad').Length = 25\n"

    assert worker.check(lengthen, a_padded_body, parameters=pad(Length=25)).ok
    assert not worker.check(lengthen, a_padded_body, parameters=pad(Length=10)).ok


def test_a_link_is_compared_by_the_name_the_plan_wrote(worker):
    """
    Plan writes the sketch the way it named it in an earlier Step. Whether that
    is the Name or the Label is not a distinction Plan makes, so neither does
    this: a Step renamed for the reader still answers to what the Plan wrote.
    """
    relabelled = PAD_IN_A_BODY + "sketch.Label = 'Root profile'\n"

    assert worker.check(relabelled, None, parameters=pad(Profile="RootProfile")).ok
    assert worker.check(relabelled, None, parameters=pad(Profile="Root profile")).ok
    assert not worker.check(relabelled, None, parameters=pad(Profile="ToothProfile")).ok


def test_a_number_is_exact_and_has_no_tolerance(worker):
    """
    A Promise carried the model's arithmetic and needed 5% of room. A parameter
    is the number Plan wrote and Execute typed, so a Step off by 1% is as wrong
    as one off by half.
    """
    assert not worker.check(PAD_IN_A_BODY, None, parameters=pad(Length=10.1)).ok
    assert not worker.check(PAD_IN_A_BODY, None, parameters=pad(Length=15)).ok


def test_an_enumeration_and_a_flag_are_compared_as_written(worker):
    assert worker.check(PAD_IN_A_BODY, None, parameters=pad(Type="Length", Midplane=False)).ok
    assert not worker.check(PAD_IN_A_BODY, None, parameters=pad(Midplane=True)).ok


def test_a_property_the_named_type_does_not_have_is_refused(worker):
    result = worker.check(PAD_IN_A_BODY, None, parameters=pad(Radius=10))

    assert not result.ok
    assert "Radius" in result.traceback


def test_geometry_inside_a_sketch_names_a_type_that_is_no_document_object(worker):
    """
    A Step that draws one line makes no object of its own. Its parameters are
    for the Plan Gate to show and there is nothing here to check them against,
    so the Step runs on the floor ADR-0010 and FCC-27 already provide.
    """
    drew_a_circle = (
        "sketch = doc.addObject('Sketcher::SketchObject', 'RootProfile')\n"
        "sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 17.5), False)\n")

    result = worker.check(drew_a_circle, None, parameters={
        "type": "Part.Circle", "properties": {"Radius": 17.5}})

    assert result.ok, result.traceback


def test_a_step_with_no_parameters_still_runs(worker):
    assert worker.check(BOX, None).ok
    assert worker.check(BOX, None, parameters=None).ok
    assert worker.check(BOX, None, parameters={}).ok


def test_a_body_left_in_pieces_is_still_refused(worker):
    """
    The parameter check does not replace FCC-27: a Pad whose parameters are
    exactly what the Plan named can still leave the Body in two lumps.
    """
    two_lumps = (
        "body = doc.addObject('PartDesign::Body', 'Body')\n"
        "sketch = doc.addObject('Sketcher::SketchObject', 'RootProfile')\n"
        "body.addObject(sketch)\n"
        "sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), False)\n"
        "sketch.addGeometry(Part.Circle(App.Vector(50, 0, 0), App.Vector(0, 0, 1), 5), False)\n"
        "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
        "body.addObject(pad)\n"
        "pad.Profile = sketch\n"
        "pad.Length = 10\n")

    result = worker.check(two_lumps, None, parameters=pad(Length=10))

    assert not result.ok
    assert "more than one piece" in result.traceback
