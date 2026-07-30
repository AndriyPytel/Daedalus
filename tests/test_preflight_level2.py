"""
Level 2 — the Preflight Worker against a real headless FreeCAD.

This is where a fake would lie. A mocked worker never segfaults, never hangs,
and always agrees that the API exists — certifying the exact mistakes Preflight
is built to catch (ADR-0007).

The harness is the Preflight Worker itself: part of the product, not
scaffolding around it.
"""
import os

import pytest

from core.preflight import PreflightWorker, copy_of, find_freecadcmd

pytestmark = pytest.mark.freecad

# Reliably takes the process down at the C level, the way a bad call into
# FreeCAD's C++ core does. Python cannot catch it; only process isolation can.
CRASHING_CODE = "import ctypes; ctypes.string_at(0)"


@pytest.fixture(scope="module")
def freecadcmd():
    try:
        return find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def warm(freecadcmd):
    """One worker shared by the tests that do not kill it."""
    with PreflightWorker(freecadcmd) as worker:
        yield worker


@pytest.fixture
def fresh(freecadcmd):
    """A worker of its own, for the tests that end its life."""
    with PreflightWorker(freecadcmd) as worker:
        yield worker


@pytest.fixture
def saved_document(tmp_path):
    """A real Document on disk with one object in it."""
    import FreeCAD

    doc = FreeCAD.newDocument("Fixture")
    box = doc.addObject("Part::Box", "ExistingBox")
    box.Length = 12
    doc.recompute()
    try:
        yield copy_of(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)


# ── the happy path ────────────────────────────────────────────────────────────


def test_a_clean_step_comes_back_ok_with_a_render(warm, tmp_path):
    png = str(tmp_path / "render.png")

    result = warm.check(
        "box = doc.addObject('Part::Box', 'Box')\nbox.Length = 10",
        render_path=png,
    )

    assert result.ok, result.traceback
    assert [o["name"] for o in result.objects] == ["Box"]
    assert os.path.getsize(png) > 1000, "the render is empty"


def test_the_worker_stays_warm_between_requests(warm):
    first = warm.check("doc.addObject('Part::Box', 'A')")
    second = warm.check("doc.addObject('Part::Box', 'B')")

    assert first.ok and second.ok
    assert warm.restarts == 0
    assert warm.alive


def test_the_document_copy_is_opened_and_seen(warm, saved_document):
    result = warm.check(
        "assert doc.getObject('ExistingBox').Length.Value == 12",
        document=saved_document,
    )

    assert result.ok, result.traceback
    assert [o["name"] for o in result.objects] == ["ExistingBox"]


def test_the_document_is_reloaded_for_every_preflight(warm, saved_document):
    """ADR-0002 — no state may survive from one Preflight to the next."""
    first = warm.check("doc.addObject('Part::Box', 'Leaked')", document=saved_document)
    assert first.ok, first.traceback
    assert sorted(o["name"] for o in first.objects) == ["ExistingBox", "Leaked"]

    second = warm.check("pass", document=saved_document)

    assert second.ok, second.traceback
    assert [o["name"] for o in second.objects] == ["ExistingBox"]


# ── the failures the assistant learns from ────────────────────────────────────


def test_api_that_does_not_exist_comes_back_as_a_traceback(warm):
    """The removed-in-1.0 API this project exists to stop guessing about."""
    result = warm.check("sketch = doc.addObject('Sketcher::SketchObject', 'S')\nsketch.Support = None")

    assert not result.ok
    assert "Support" in result.traceback
    assert warm.alive, "a Python error must not cost us the worker"


def test_a_missing_attribute_comes_back_with_what_the_object_does_have(warm):
    """
    The Origin planes: three attempts went on `body.Origin.XY_Plane`, which
    does not exist, because the AttributeError never mentioned OriginFeatures.
    """
    result = warm.check(
        "body = doc.addObject('PartDesign::Body', 'Body')\n"
        "doc.recompute()\n"
        "plane = body.Origin.XY_Plane\n"
    )

    assert not result.ok
    assert "has no 'XY_Plane'" in result.traceback
    assert "OriginFeatures" in result.traceback, "the way to the planes is not in the failure"


def test_the_geometry_modules_are_in_scope_without_an_import(warm):
    """
    `Part.Circle` is how every version of FreeCAD spells sketch geometry. Left
    out of the namespace it raised NameError, which read as "Part does not
    exist here" and sent the assistant off inventing Sketcher.Circle.
    """
    result = warm.check(
        "sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')\n"
        "sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10), False)\n"
    )

    assert result.ok, result.traceback


def test_the_hint_names_a_class_by_its_name_not_a_descriptor_repr(warm):
    """TypeId on a class is an unbound descriptor; its repr named nothing usable."""
    result = warm.check("Sketcher.GeometryFacade.addCircle(1)")

    assert not result.ok
    assert "GeometryFacade has no 'addCircle'" in result.traceback
    assert "Base.BaseClass" not in result.traceback


def test_a_hint_is_not_offered_when_there_is_no_object_to_introspect(warm):
    result = warm.check("raise ValueError('nothing to introspect here')")

    assert not result.ok
    assert "It has:" not in result.traceback


def test_a_recompute_that_leaves_an_object_in_error_is_a_failure(warm):
    result = warm.check(
        "box = doc.addObject('Part::Box', 'Box')\n"
        "box.Length = 10\n"
        "cut = doc.addObject('Part::Cut', 'Cut')"   # no Base/Tool — recompute fails
    )

    assert not result.ok
    assert "Cut" in result.traceback


def test_a_failed_recompute_names_the_reason_freecad_already_knows(warm):
    """
    A bare list of names tells the next attempt nothing, so it re-spells the
    same code. FreeCAD keeps the reason on the object. FCC-11.
    """
    result = warm.check(
        "body = doc.addObject('PartDesign::Body', 'Body')\n"
        "sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')\n"
        "body.addObject(sketch)\n"
        "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
        "pad.Profile = sketch\n"          # the sketch is empty — nothing to pad
        "body.addObject(pad)\n"
    )

    assert not result.ok
    assert "Pad (PartDesign::Pad): Linked shape object is empty" in result.traceback


def test_every_object_left_in_error_gets_its_own_line(warm):
    result = warm.check(
        "a = doc.addObject('Part::Cut', 'CutA')\n"
        "b = doc.addObject('Part::Cut', 'CutB')\n"
    )

    assert not result.ok
    reasons = [line for line in result.traceback.splitlines() if "(Part::Cut): " in line]
    assert len(reasons) == 2, result.traceback
    assert reasons[0].startswith("CutA (Part::Cut): ")
    assert reasons[1].startswith("CutB (Part::Cut): ")


# ── the failures the whole design rests on ────────────────────────────────────


def test_code_that_crashes_the_core_kills_the_worker_not_us(fresh):
    doomed = fresh.pid

    result = fresh.check(CRASHING_CODE)

    assert not result.ok
    assert "died" in result.traceback
    assert fresh.pid != doomed, "the worker was not replaced"
    assert fresh.alive
    # And we are still here to assert it — which is the point.


def test_an_infinite_loop_is_killed_on_timeout(fresh):
    hung = fresh.pid

    result = fresh.check("while True:\n    pass", timeout=3)

    assert not result.ok
    assert "timed out" in result.traceback
    assert fresh.pid != hung
    assert fresh.alive


def test_the_worker_still_works_after_it_died(fresh):
    fresh.check(CRASHING_CODE)

    result = fresh.check("doc.addObject('Part::Box', 'Box')")

    assert result.ok, result.traceback
    assert fresh.restarts == 1


def test_worker_death_is_never_raised_at_the_caller(fresh):
    """It restarts silently; the caller sees a result, not an incident."""
    for _ in range(2):
        result = fresh.check(CRASHING_CODE)
        assert not result.ok

    assert fresh.restarts == 2
    assert fresh.check("pass").ok
