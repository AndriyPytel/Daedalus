"""
Level 2 -- Preflight refuses a Step that leaves an untouched sibling feature's
geometry altered (FCC-260).

`core/apply.py` and Preflight track only the NAMES a Step's own code touched
(`created`/`deleted`/`modified`) -- never whether some other feature's shape
moved underneath it. `config/prompts/assertion.md` already documents this
exact failure class: a slot shortened until it passes an acceptance check, a
clamp that no longer opens. Both examples are a feature the Step never named
whose geometry changed anyway, caught here rather than only by accident, if
the feature's name happened to already sit in the Acceptance list.

Reproduced with `Part::Fillet` on a `Part::Box`, the same fixture shape
`test_preflight_worker_recompute_scope.py` already uses for "a dependent left
stale by a scoped recompute" -- here the Box shrinks by an amount the Fillet
still fits, so the Fillet stays Valid and only its own geometry changes,
which is the case that check does not cover.

Left red on purpose: this fixture is the exact shape
`tests/test_apply_level2.py::test_a_dependent_recompute_is_not_reported_as_a_modification`
requires to pass -- a legitimate dependency cascade, not the "slot shortened
by an unrelated retry" class the ticket names. A Preflight-level check strict
enough to catch this fixture is strict enough to break that test, so which
phase FCC-260's check actually belongs in is open; see the ticket for the
resolution before turning this green.
"""
import pytest

from core.preflight import PreflightWorker, copy_of, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with PreflightWorker() as running:
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


@pytest.fixture
def a_box_with_a_fillet_and_an_unrelated_box():
    """The same Fillet fixture, plus a second Box nothing else depends on."""
    import FreeCAD

    doc = FreeCAD.newDocument("GeometryDiffFalsePositiveFixture")
    box = doc.addObject("Part::Box", "Box")
    box.Length = box.Width = box.Height = 20
    fillet = doc.addObject("Part::Fillet", "Fillet")
    fillet.Base = box
    fillet.Edges = [(1, 2.0, 2.0)]
    other = doc.addObject("Part::Box", "Other")
    other.Length = other.Width = other.Height = 5
    other.Placement.Base.x = 100
    doc.recompute()
    try:
        yield copy_of(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_feature_that_moved_without_being_named_touched_fails_preflight(worker, a_box_with_a_fillet):
    result = worker.check(
        "box = doc.getObject('Box')\n"
        "box.Length = 15\n"
        "box.Width = 15\n"
        "box.Height = 15\n",
        a_box_with_a_fillet,
    )

    assert not result.ok, "Fillet's geometry moved as a side effect and was not caught"


def test_the_failure_names_the_feature_and_says_it_was_not_declared_touched(worker, a_box_with_a_fillet):
    said = worker.check(
        "box = doc.getObject('Box')\n"
        "box.Length = 15\n"
        "box.Width = 15\n"
        "box.Height = 15\n",
        a_box_with_a_fillet,
    ).traceback

    assert "Fillet" in said, said
    assert "not" in said and "touched" in said, \
        "the failure does not say the feature was never declared touched: %s" % said


def test_a_step_that_only_touches_what_it_declares_passes(worker, a_box_with_a_fillet_and_an_unrelated_box):
    result = worker.check(
        "other = doc.getObject('Other')\n"
        "other.Length = 8\n",
        a_box_with_a_fillet_and_an_unrelated_box,
    )

    assert result.ok, result.traceback
