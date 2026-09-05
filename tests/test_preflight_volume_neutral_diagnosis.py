"""
Level 1 -- FCC-246's own diagnosis rule, scored against the replay FCC-245
recorded (tests/recorded/preflight_volume_neutral_failures.json).

FCC-245 replayed all 12 volume-neutral failures by flipping `Reversed` and
recomputing, and recorded whether the flip changed the volume
(`reversed_flip_changed_volume`). `_idle_feature_cause` is exactly that
comparison, called on the fixture's own recorded volumes rather than a live
FreeCAD recompute, so this test can score the diagnosis without FreeCAD: a
flip that moves the volume beyond `_VOLUME_TOLERANCE` names a wrong
direction, a flip that does not names a profile that misses the solid
either way.
"""
import json
import os

from core.kernel_worker import _idle_feature_cause, _diagnose_idle_feature

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "recorded", "preflight_volume_neutral_failures.json")


def _fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_the_diagnosis_rule_matches_every_recorded_cause():
    data = _fixture()
    right, wrong = 0, 0
    for case in data["cases"]:
        got = _idle_feature_cause(case["before_volume_mm3"],
                                   case["after_volume_with_reversed_flipped_mm3"])
        want = "direction" if case["reversed_flip_changed_volume"] else "no-overlap"
        if got == want:
            right += 1
        else:
            wrong += 1
    assert (right, wrong) == (12, 0)


def test_a_feature_with_no_reversed_property_cannot_be_diagnosed():
    class NoReversed:
        Name = "Fillet1"
        TypeId = "PartDesign::Fillet"

    assert _diagnose_idle_feature(NoReversed(), 100.0, 100.0) is None
