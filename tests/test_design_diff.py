"""
Level 1 — the semantic change-set between two Design versions (FCC-311).

Plan is handed a moved datum reported as a moved datum, not two unrelated
lines of a re-serialized YAML file: matched by stable identity — `id` on a
feature, `name` on a datum — never by position or name text (ADR-0019's
identity rule, reused here rather than reinvented).
"""
from core.design.design import ADDED, CHANGED, REMOVED, diff


def _read(value):
    return {"value": value, "flag": "read"}


def test_an_added_feature_is_reported_as_added():
    changes = diff({"features": []},
                    {"features": [{"id": "hole-40-left", "kind": "Ø40 hole"}]})

    assert changes == [{"section": "features", "kind": ADDED, "id": "hole-40-left",
                        "now": {"id": "hole-40-left", "kind": "Ø40 hole"}}]


def test_a_removed_feature_is_reported_as_removed():
    changes = diff({"features": [{"id": "hole-40-left", "kind": "Ø40 hole"}]},
                    {"features": []})

    assert changes == [{"section": "features", "kind": REMOVED, "id": "hole-40-left",
                        "was": {"id": "hole-40-left", "kind": "Ø40 hole"}}]


def test_a_moved_datum_is_one_changed_entry_not_an_add_and_a_remove():
    before = {"datums": [{"name": "the ring axis", "is": "20 mm from the left end face"}]}
    after = {"datums": [{"name": "the ring axis", "is": "25 mm from the left end face"}]}

    changes = diff(before, after)

    assert changes == [{"section": "datums", "kind": CHANGED, "id": "the ring axis",
                        "was": before["datums"][0], "now": after["datums"][0]}]


def test_an_unrelated_object_left_untouched_is_not_in_the_change_set():
    steady = {"id": "hole-35", "kind": "Ø35 hole"}
    before = {"features": [steady, {"id": "hole-40-left", "kind": "Ø40 hole"}]}
    after = {"features": [steady, {"id": "hole-40-left", "kind": "Ø40 hole",
                                   "topology": "through"}]}

    changes = diff(before, after)

    assert changes == [{"section": "features", "kind": CHANGED, "id": "hole-40-left",
                        "was": {"id": "hole-40-left", "kind": "Ø40 hole"},
                        "now": {"id": "hole-40-left", "kind": "Ø40 hole",
                                "topology": "through"}}]


def test_no_previous_version_reports_every_object_as_added():
    changes = diff({}, {"datums": [{"name": "the left end face", "is": "the flat face"}]})

    assert changes == [{"section": "datums", "kind": ADDED, "id": "the left end face",
                        "now": {"name": "the left end face", "is": "the flat face"}}]


def test_identical_versions_report_nothing():
    part = {"datums": [{"name": "the ring axis", "is": "the front-to-rear axis"}],
            "features": [{"id": "hole-35", "kind": "Ø35 hole"}]}

    assert diff(part, part) == []


def test_envelope_is_not_compared():
    """No object with an identity to match by; a value under a key is not this."""
    before = {"envelope": {"length": _read("150 mm")}}
    after = {"envelope": {"length": _read("160 mm")}}

    assert diff(before, after) == []


def test_a_new_conflict_is_reported_as_added_matched_by_its_address():
    before = {"conflicts": []}
    conflict = {"about": "datums → the left end face", "stands": "flat",
               "was_also_said": "rounded"}
    after = {"conflicts": [conflict]}

    changes = diff(before, after)

    assert changes == [{"section": "conflicts", "kind": ADDED,
                        "id": "datums → the left end face", "now": conflict}]
