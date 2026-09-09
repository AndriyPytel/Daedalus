"""
Level 1 -- `core.loop._geometry_regressions`'s own comparison rule.

The Assertion phase measures a per-feature geometry fingerprint (Volume and
BoundBox) on every `check_assertions` call and hands it back as
`Checked.geometry`. `_geometry_regressions` is the pure comparison this
build makes of two such snapshots: a feature present in both, whose
fingerprint moved past `_GEOMETRY_TOLERANCE`, and whose FreeCAD Object.Name
is not exactly one of the Names a retried Step actually touched, is a
regression -- the "slot shortened until it passes" class
config/prompts/assertion.md documents, caught even though nothing declared
touching it.

No FreeCAD import: fingerprints are plain tuples, the same shape
`core/kernel_worker.py::_geometry_of` produces off a live Shape.
"""
from core.loop.assertion import _geometry_regressions

A_BOX = (1000.0, 0.0, 0.0, 0.0, 10.0, 10.0, 10.0)
A_BOX_MOVED = (1000.0, 5.0, 0.0, 0.0, 15.0, 10.0, 10.0)


def test_a_feature_that_moved_and_was_never_named_in_a_retry_is_a_regression():
    baseline = {"Box": A_BOX, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    geometry = {"Box": A_BOX_MOVED, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    assert _geometry_regressions(baseline, geometry, retried_names=()) == ["Box"]


def test_a_feature_named_in_what_a_retry_touched_is_not_a_regression():
    baseline = {"Box": A_BOX}
    geometry = {"Box": A_BOX_MOVED}
    retried = ["Box"]
    assert _geometry_regressions(baseline, geometry, retried_names=retried) == []


def test_a_feature_with_no_change_is_not_a_regression():
    baseline = {"Box": A_BOX}
    geometry = {"Box": A_BOX}
    assert _geometry_regressions(baseline, geometry, retried_names=()) == []


def test_a_feature_created_since_the_baseline_is_not_a_regression():
    baseline = {"Box": A_BOX}
    geometry = {"Box": A_BOX, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    assert _geometry_regressions(baseline, geometry, retried_names=()) == []


def test_an_empty_baseline_reports_nothing_moved():
    assert _geometry_regressions({}, {"Box": A_BOX_MOVED}, retried_names=()) == []


def test_a_feature_the_current_snapshot_no_longer_has_is_not_a_regression():
    baseline = {"Box": A_BOX, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    geometry = {"Box": A_BOX}
    assert _geometry_regressions(baseline, geometry, retried_names=()) == []


def test_a_shorter_names_feature_is_reported_when_only_the_longer_ones_name_was_retried():
    """
    The join is exact Names, not substring: retrying SlotChamfer excuses only
    the Name "SlotChamfer", never "Slot" even though it is a prefix of it.
    Both moved; only the one actually named as touched is excused.
    """
    baseline = {"Slot": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
                "SlotChamfer": (5.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    geometry = {"Slot": (12.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
                "SlotChamfer": (6.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    retried_names = ["SlotChamfer"]

    assert _geometry_regressions(baseline, geometry, retried_names) == ["Slot"]


def test_a_duplicate_kind_freecad_auto_numbers_is_not_excused_by_its_own_prefix():
    """
    FreeCAD auto-suffixes a repeated TypeId's Name (Fillet, Fillet001, ...).
    Retrying Fillet001 must not excuse Fillet by prefix: only the exact Name
    retried is excluded.
    """
    baseline = {"Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
                "Fillet001": (5.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    geometry = {"Fillet": (12.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
                "Fillet001": (6.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    retried_names = ["Fillet001"]

    assert _geometry_regressions(baseline, geometry, retried_names) == ["Fillet"]
