"""
Level 1 -- FCC-260's own comparison rule, `core.loop._geometry_regressions`.

The Assertion phase measures a per-feature geometry fingerprint (Volume and
BoundBox) on every `check_assertions` call and hands it back as
`Checked.geometry`. `_geometry_regressions` is the pure comparison this
build makes of two such snapshots: a feature present in both, whose
fingerprint moved past `_GEOMETRY_TOLERANCE`, and whose Name is not a
substring of anything in `entries_a_retry_touched` (core/loop.py:2463,
FCC-260) is a regression -- the "slot shortened until it passes" class
config/prompts/assertion.md documents, caught even though nothing declared
touching it.

No FreeCAD import: fingerprints are plain tuples, the same shape
`core/preflight_worker.py::_geometry_of` produces off a live Shape.
"""
from core.loop import _geometry_regressions

A_BOX = (1000.0, 0.0, 0.0, 0.0, 10.0, 10.0, 10.0)
A_BOX_MOVED = (1000.0, 5.0, 0.0, 0.0, 15.0, 10.0, 10.0)


def test_a_feature_that_moved_and_was_never_named_in_a_retry_is_a_regression():
    baseline = {"Box": A_BOX, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    geometry = {"Box": A_BOX_MOVED, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    assert _geometry_regressions(baseline, geometry, retried=()) == ["Box"]


def test_a_feature_named_in_what_a_retry_touched_is_not_a_regression():
    baseline = {"Box": A_BOX}
    geometry = {"Box": A_BOX_MOVED}
    retried = ["the Box is 15mm long"]
    assert _geometry_regressions(baseline, geometry, retried=retried) == []


def test_a_feature_with_no_change_is_not_a_regression():
    baseline = {"Box": A_BOX}
    geometry = {"Box": A_BOX}
    assert _geometry_regressions(baseline, geometry, retried=()) == []


def test_a_feature_created_since_the_baseline_is_not_a_regression():
    baseline = {"Box": A_BOX}
    geometry = {"Box": A_BOX, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    assert _geometry_regressions(baseline, geometry, retried=()) == []


def test_an_empty_baseline_reports_nothing_moved():
    assert _geometry_regressions({}, {"Box": A_BOX_MOVED}, retried=()) == []


def test_a_feature_the_current_snapshot_no_longer_has_is_not_a_regression():
    baseline = {"Box": A_BOX, "Fillet": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    geometry = {"Box": A_BOX}
    assert _geometry_regressions(baseline, geometry, retried=()) == []


def test_a_shorter_names_feature_is_wrongly_excused_by_a_longer_ones_retry():
    """
    Pinned, not fixed (FCC-260): the join between `retried`'s Acceptance
    citations and `geometry`'s FreeCAD Names is substring, because no exact
    one exists in this codebase (see `_geometry_regressions`'s own
    docstring). "Slot" is a substring of "SlotChamfer", so a citation that
    only ever names SlotChamfer excuses Slot too, whether or not anything
    actually retried Slot. Both moved here; the check catches neither.
    """
    baseline = {"Slot": (10.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
                "SlotChamfer": (5.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    geometry = {"Slot": (12.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
                "SlotChamfer": (6.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)}
    retried = ["features -> SlotChamfer -> width"]

    assert _geometry_regressions(baseline, geometry, retried) == [], \
        "Slot moved and nothing named Slot itself, but this is the accepted false negative"
