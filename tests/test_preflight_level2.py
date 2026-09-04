"""
Level 2 — the Preflight Worker against a real headless FreeCAD.

This is where a fake would lie. A mocked worker never segfaults, never hangs,
and always agrees that the API exists — certifying the exact mistakes Preflight
is built to catch (ADR-0007).

The harness is the Preflight Worker itself: part of the product, not
scaffolding around it.

CRASHING_CODE reliably takes the process down at the C level, the way a bad call
into FreeCAD's C++ core does. Python cannot catch it; only process isolation can.

GENERIC_DEATH_ENDS is the last sentence of the generic death message; anything
the worker said follows it.
"""
import os
import time

import pytest

from core.preflight import PreflightWorker, copy_of, find_freecadcmd

pytestmark = pytest.mark.freecad


def _wait_until(predicate, timeout=2.0, interval=0.01):
    """
    Polls `predicate` instead of a fixed sleep, so the drain thread gets as
    long as it actually needs on a slow CI runner and the test does not pay
    for time it did not need on a fast one. FCC-284.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


def test_wait_until_returns_as_soon_as_the_predicate_is_true():
    """
    The predicate this poll is built for (`fresh._stderr_text()` catching up)
    can turn true well inside the old fixed 0.5s — this pins that the loop
    does not wait out `timeout` regardless. FCC-284.
    """
    started = time.monotonic()

    _wait_until(lambda: True, timeout=2.0)

    assert time.monotonic() - started < 0.5


def test_wait_until_raises_on_timeout_instead_of_hanging():
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        _wait_until(lambda: False, timeout=0.05)

    assert time.monotonic() - started < 0.5


CRASHING_CODE = "import ctypes; ctypes.string_at(0)"

GENERIC_DEATH_ENDS = "Rewrite the Step to avoid whatever crashed the core."


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
    """A Cut with neither Base nor Tool is what the recompute fails on."""
    result = warm.check(
        "box = doc.addObject('Part::Box', 'Box')\n"
        "box.Length = 10\n"
        "cut = doc.addObject('Part::Cut', 'Cut')"
    )

    assert not result.ok
    assert "Cut" in result.traceback


def test_a_failed_recompute_names_the_reason_freecad_already_knows(warm):
    """
    A bare list of names tells the next attempt nothing, so it re-spells the
    same code. FreeCAD keeps the reason on the object. FCC-11.

    The sketch here is empty, so there is nothing for the Pad to pad.
    """
    result = warm.check(
        "body = doc.addObject('PartDesign::Body', 'Body')\n"
        "sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')\n"
        "body.addObject(sketch)\n"
        "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
        "pad.Profile = sketch\n"
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


def test_code_that_crashes_the_core_kills_the_worker_not_us(fresh):
    """
    Code that crashes the core kills the worker, not us. We are still here to
    assert it, which is the point.
    """
    doomed = fresh.pid

    result = fresh.check(CRASHING_CODE)

    assert not result.ok
    assert "died" in result.traceback
    assert fresh.pid != doomed, "the worker was not replaced"
    assert fresh.alive


def test_a_dead_worker_says_what_it_said_on_the_way_out(fresh):
    """
    The production crash: an unknown constraint type name segfaults inside the
    C++ constructor of Sketcher.Constraint, before any Python-level anything.
    The one line FreeCAD leaves on stderr is all the next attempt has to go on,
    and the message used to be built after restart() had already swapped that
    stderr for the new worker's empty one. FCC-19.
    """
    doomed = fresh.pid

    result = fresh.check("Sketcher.Constraint('Concentric', 0, 1)")

    assert not result.ok
    assert GENERIC_DEATH_ENDS in result.traceback
    assert "Unknown exception while processing file" in result.traceback, \
        "the death message carries nothing the worker actually said"
    assert fresh.pid != doomed, "the worker was not replaced"
    assert fresh.alive


def test_a_death_on_the_write_reports_the_dead_workers_stderr(fresh):
    """
    The BrokenPipeError branch reads the stderr of the worker it lost, not of
    the one restart() puts in its place.

    The real race — dying between the liveness check and the write — cannot be
    staged, so the write is what fails here. What is under test is the
    ordering. `_wait_until` is what the drain thread gets to catch up.
    """
    class _Dead:
        def write(self, _):
            raise BrokenPipeError("the worker went while we were talking")

        def flush(self):
            pass

    fresh.check("import sys; sys.stderr.write('the worker had something to say\\n')")
    _wait_until(lambda: "the worker had something to say" in fresh._stderr_text())
    fresh._process.stdin = _Dead()

    result = fresh.check("doc.addObject('Part::Box', 'Box')")

    assert not result.ok
    assert "before it could start" in result.traceback
    assert "the worker had something to say" in result.traceback


def test_a_step_that_writes_cyrillic_to_stderr_is_read_back_intact(fresh):
    """
    The Worker's stderr is the only diagnostic a later death has to offer, and
    an object Label, a path or a message is not obliged to be ASCII. FCC-51.

    `_wait_until` is what the drain thread gets to catch up.
    """
    result = fresh.check(
        "import sys; sys.stderr.write('деталь не побудована\\n'); sys.stderr.flush()"
    )

    assert result.ok, result.traceback
    _wait_until(lambda: "деталь не побудована" in fresh._stderr_text())
    assert "деталь не побудована" in fresh._stderr_text()


def test_an_undecodable_byte_on_stderr_does_not_cost_us_the_diagnostics(fresh):
    """
    The production shape of the bug: one byte the decoder refuses kills the
    drain thread, and every later thing the Worker says is lost — including
    the last words of the death this Step was heading for.

    Written as a raw invalid byte rather than as a locale, because the locale
    that produces this (ascii, inside FreeCAD.app with no LANG) cannot be
    staged from a test: the parent's decoder is fixed at interpreter startup.
    """
    fresh.check(
        r"import sys; sys.stderr.buffer.write(b'\xd0 broken\n'); sys.stderr.flush()"
    )
    _wait_until(lambda: "broken" in fresh._stderr_text())

    result = fresh.check(
        "import sys; sys.stderr.write('and this still arrives\\n'); sys.stderr.flush()"
    )

    assert result.ok, result.traceback
    _wait_until(lambda: "and this still arrives" in fresh._stderr_text())
    assert "and this still arrives" in fresh._stderr_text(), \
        "the drain thread died on the undecodable byte and took the rest with it"


def test_an_undecodable_byte_on_stdout_does_not_hang_the_request(fresh):
    """
    Worse than stderr: `_pump` carries the only end-of-stream signal, so its
    death used to leave the caller waiting out the whole timeout.
    """
    started = time.monotonic()

    result = fresh.check(
        r"import sys; sys.stdout.buffer.write(b'\xd0 broken\n'); sys.stdout.flush()"
        "\ndoc.addObject('Part::Box', 'Box')",
        timeout=20,
    )
    elapsed = time.monotonic() - started

    assert result.ok, result.traceback
    assert elapsed < 15, "the request waited out its timeout instead of being answered"


def test_an_infinite_loop_is_killed_on_timeout(fresh):
    """
    A real hang, killed by a real timeout — only the timeout itself is
    scaffolding, short enough that the wait is not the point of the test.
    """
    hung = fresh.pid

    result = fresh.check("while True:\n    pass", timeout=0.5)

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


def test_a_body_left_in_pieces_is_still_refused(warm):
    """
    The floor under a Step, and all of it since FCC-75 took the named-parameter
    check away: a sketch of disjoint profiles pads into separate lumps and
    nothing raises, so Preflight is the only phase that objects (FCC-27).
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

    result = warm.check(two_lumps, None)

    assert not result.ok
    assert "more than one piece" in result.traceback


def test_a_sweep_of_modelmania_sized_readings_is_answered_in_seconds(warm):
    """
    The reading budget is set against a full Model Mania-sized sweep.

    The four sheets contain 142 outlines. A warm child answers that many simple
    readings well inside the phase budget, so the limit bounds a stopped child
    rather than normal work.
    """
    from core import sheet_reader, sketch_check

    corners = [(0.0, 0.0), (98.2, 0.0), (98.2, 30.0), (0.0, 30.0)]
    entities = [
        sheet_reader.Entity(kind="line", source="traced", role="contour",
                            size=sheet_reader._distance(corners[at],
                                                        corners[(at + 1) % 4]),
                            start=corners[at], end=corners[(at + 1) % 4])
        for at in range(4)]
    outline = sheet_reader.Outline(entities=entities, closed=True, area=3000.0)
    between = [sheet_reader.Between(printed=100.0, text="100", view="the front view",
                                    first=(0.0, 15.0), second=(100.0, 15.0))]

    started = time.monotonic()
    for _ in range(142):
        assert sketch_check.checked_by(warm, outline, "the front view", between)
    elapsed = time.monotonic() - started

    assert elapsed < 20, "the sweep is nowhere near the budget the phase is given"
