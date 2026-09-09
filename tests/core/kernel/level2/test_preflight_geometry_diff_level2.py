"""
Level 2 -- the geometry fingerprint FCC-260's Assertion check compares, off a
real headless FreeCAD Document.

The check itself lives in the Assertion phase (core/loop.py::run,
core/loop.py::_geometry_regressions), keyed on `entries_a_retry_touched`
(core/loop.py:2463) rather than on anything Preflight can see -- Preflight's
own `_recompute_scope` means nothing outside a Step's declared scope can ever
have a different Shape within one Preflight call (`core/kernel_worker.py`),
so a per-Step "untouched but moved" check can only ever fire on a legitimate
dependency cascade (`tests/test_apply_level2.py::
test_a_dependent_recompute_is_not_reported_as_a_modification`). What
Preflight can measure, and what `check_assertions` hands the Assertion phase
every time it runs, is the current fingerprint -- Volume and BoundBox, read
straight off `KernelResult.geometry` (`core/kernel_worker.py::
_geometry_snapshot`). This is that wiring, proved against a real Document;
the comparison itself is Level 1 (tests/test_geometry_regression_across_a_run.py,
tests/test_loop.py).

Reproduced with `Part::Fillet` on a `Part::Box`, the same fixture shape
`test_preflight_worker_recompute_scope.py` already uses for "a dependent left
stale by a scoped recompute": editing the Box moves the Fillet's own Shape as
a side effect, without the Step ever naming Fillet.
"""
import pytest

from core.kernel import Kernel, copy_of, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


@pytest.fixture
def a_box_with_a_fillet():
    """A Fillet on one edge of a 20 mm Box, saved so a later Step never touches it directly."""
    import FreeCAD

    doc = FreeCAD.newDocument("GeometryDiffFixture")
    box = doc.addObject("Part::Box", "Box")
    box.Length = box.Width = box.Height = 20
    fillet = doc.addObject("Part::Fillet", "Fillet")
    fillet.Base = box
    fillet.Edges = [(1, 2.0, 2.0)]
    doc.recompute()
    try:
        yield copy_of(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_successful_check_carries_a_fingerprint_for_every_feature(worker, a_box_with_a_fillet):
    result = worker.check("pass\n", a_box_with_a_fillet)

    assert result.ok, result.traceback
    assert set(result.geometry) == {"Box", "Fillet"}
    assert all(len(fingerprint) == 7 for fingerprint in result.geometry.values()), \
        "Volume plus a six-number BoundBox is the whole of the fingerprint"


def test_editing_the_box_moves_the_fillets_own_fingerprint_too(worker, a_box_with_a_fillet):
    before = worker.check("pass\n", a_box_with_a_fillet).geometry["Fillet"]

    after = worker.check(
        "box = doc.getObject('Box')\n"
        "box.Length = 15\n"
        "box.Width = 15\n"
        "box.Height = 15\n",
        a_box_with_a_fillet,
    ).geometry["Fillet"]

    assert before != after, \
        "Fillet's geometry did not move as a side effect of resizing Box -- fixture is wrong"


def test_a_check_that_touches_nothing_reports_the_same_fingerprint_twice(worker, a_box_with_a_fillet):
    first = worker.check("pass\n", a_box_with_a_fillet).geometry
    second = worker.check("pass\n", a_box_with_a_fillet).geometry

    assert first == second, "an unedited copy fingerprinted differently across two calls"
