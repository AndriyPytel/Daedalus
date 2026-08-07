"""
Level 2 — Preflight measures the throwaway copy against the Step's Promise (FCC-62).

A Step used to be prose, and nothing measured what it produced. Preflight
checked that the code did not raise, that the recompute left nothing in error,
that a Body was one piece (FCC-27) and that something changed at all
(ADR-0010) — none of which notices a fillet that applied nought edges of twelve.

Run against a real headless FreeCAD through the real Preflight Worker: what is
being checked is what FreeCAD measures, not what a fake would agree to
(ADR-0007).
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


# A 40 x 30 x 10 box: one solid, six faces, 12000 mm3.
BOX = ("box = doc.addObject('Part::Box', 'Box')\n"
       "box.Length, box.Width, box.Height = 40, 30, 10\n")

# The 2026-08-06 pattern: a fillet that applies nothing and says so in a
# property of its own rather than failing.
FILLET_THAT_TAKES_NOTHING = BOX + (
    "note = doc.addObject('App::FeaturePython', 'Note')\n")


def test_a_step_that_keeps_its_promise_passes(worker):
    result = worker.check(BOX, None, promise={
        "solids": 1, "faces": 6, "volume": 12000.0, "bbox": [40, 30, 10]})

    assert result.ok, result.traceback


def test_a_step_that_misses_its_promise_is_refused(worker):
    result = worker.check(BOX, None, promise={"faces": 18})

    assert not result.ok, "a Step that produced something else was passed on"


def test_the_refusal_says_what_was_promised_and_what_was_measured(worker):
    result = worker.check(BOX, None, promise={"faces": 18})

    said = result.traceback
    assert "did not produce what it promised" in said, said
    assert "promised +18" in said, said
    assert "measured +6" in said, said
    assert "correct the code against the measurement" in said, said


def test_a_fillet_that_rounds_nothing_no_longer_reaches_apply(worker):
    """
    The failure this ticket exists for: code that runs cleanly, recomputes
    clean, and produced none of the twelve rounded edges it was for.
    """
    result = worker.check(FILLET_THAT_TAKES_NOTHING, None, promise={"faces": 12})

    assert not result.ok
    assert "faces: promised +12, measured +6" in result.traceback


def test_a_step_with_no_promise_still_runs(worker):
    """
    Where Plan had nothing measurable to promise, the structural delta alone
    applies. ADR-0010 stays as the floor and is not replaced.
    """
    assert worker.check(BOX, None).ok
    assert worker.check(BOX, None, promise=None).ok
    assert worker.check(BOX, None, promise={}).ok


def test_a_size_may_be_close_and_a_count_may_not(worker):
    """
    A count is exact; a size is the model's arithmetic. 12000 promised against
    12000 measured leaves room for a 1% slip, and a face count does not.
    """
    assert worker.check(BOX, None, promise={"volume": 12100.0}).ok
    assert not worker.check(BOX, None, promise={"volume": 15000.0}).ok
    assert not worker.check(BOX, None, promise={"solids": 2}).ok


def test_the_promise_is_measured_as_a_delta_not_as_a_total(worker):
    """
    A Step is one operation, so its Promise is about what the operation does.
    A second box in a Document that already has one adds one solid, not two.
    """
    second = ("two = doc.addObject('Part::Box', 'Two')\n"
              "two.Length, two.Width, two.Height = 40, 30, 10\n"
              "two.Placement.Base.x = 100\n")
    result = worker.check(BOX + second, None, promise={"solids": 2, "faces": 12})

    assert result.ok, result.traceback


def test_what_was_measured_comes_back_for_the_reader(worker):
    result = worker.check(BOX, None)

    assert result.measured["solids"] == 1
    assert result.measured["faces"] == 6
    assert result.measured["volume"] == pytest.approx(12000.0)
