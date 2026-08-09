"""
Level 1 — the merge and the rendering of the part Design describes.

No provider, no FreeCAD: the merge is the single most load-bearing piece of
logic in FCC-93, so it is tested on its own rather than only through a run. The
numbers are the ones from the run that named the defect
(`logs/sessions/daefe57b593448e9baf83422fc37af1e.jsonl`, mm25-p1-1.pdf).
"""
from core.design import merge, render


def _read(value):
    return {"value": value, "flag": "read"}


DEPTH_OFF_THE_SECTION = {
    "envelope": {"depth front to rear": _read("100 mm")},
    "features": [{"id": "hole-40-left", "kind": "Ø40 hole", "topology": "through",
                  "position": {"the left end face": _read("20 mm")},
                  "seen_in": ["front view"]}],
}


# ── merge only ────────────────────────────────────────────────────────────────


def test_a_field_already_filled_is_not_replaced():
    """
    The 100 mm read off SECTION B-B was not overruled or argued with in the run
    that named this defect — it was simply gone, and the Plan said 150 × 50 × 50.
    A later source may not write over a value, whatever it thinks it saw.
    """
    grown = merge(DEPTH_OFF_THE_SECTION,
                  {"envelope": {"depth front to rear": _read("50 mm")}})

    assert grown["envelope"]["depth front to rear"] == _read("100 mm")


def test_the_disagreement_is_recorded_rather_than_reconciled():
    """
    Two incompatible dimensions stay recorded as a conflict and are not settled
    by the machine — the FCC-59 boundary. The known cost is accepted: the first
    pass is authoritative even where it was wrong, so the disagreement has to
    reach the user at the Plan Gate instead.
    """
    grown = merge(DEPTH_OFF_THE_SECTION,
                  {"features": [{"id": "hole-40-left",
                                 "position": {"the left end face": _read("25 mm")}}]})

    held = grown["features"][0]["position"]["the left end face"]
    assert held == _read("20 mm"), "the later pass overwrote a value that was already read"

    conflict = grown["conflicts"][0]
    assert "hole-40-left" in conflict["about"] and "the left end face" in conflict["about"]
    assert conflict["stands"] == _read("20 mm")
    assert conflict["was_also_said"] == _read("25 mm")


def test_an_empty_field_is_filled_and_a_new_feature_is_added():
    """The model grows. Merge-only would be useless if it only ever refused."""
    grown = merge(DEPTH_OFF_THE_SECTION, {
        "features": [
            {"id": "hole-40-left", "position": {"the top face": _read("25 mm")},
             "seen_in": ["SECTION B-B"]},
            {"id": "hole-35", "kind": "Ø35 hole", "topology": "through"},
        ],
    })

    left = grown["features"][0]
    assert left["position"] == {"the left end face": _read("20 mm"),
                                "the top face": _read("25 mm")}
    assert left["seen_in"] == ["front view", "SECTION B-B"]
    assert [feature["id"] for feature in grown["features"]] == ["hole-40-left", "hole-35"]
    assert "conflicts" not in grown, "filling an empty field is not a disagreement"


def test_features_are_matched_by_id_and_not_by_position_in_the_list():
    """
    The rule is enforced in the loop by `id`, not only in the prompt: a fragment
    that names one feature must not land on whichever one happens to be first.
    """
    grown = merge({"features": [{"id": "a", "kind": "boss"}, {"id": "b"}]},
                  {"features": [{"id": "b", "kind": "pocket"}]})

    assert grown["features"] == [{"id": "a", "kind": "boss"}, {"id": "b", "kind": "pocket"}]


def test_a_datum_keeps_the_name_it_was_first_given():
    grown = merge({"datums": [{"name": "the left end face", "is": "the flat left face"}]},
                  {"datums": [{"name": "the left end face", "is": "somewhere else"},
                              {"name": "the ring axis", "is": "the Ø40 bore axis"}]})

    assert grown["datums"][0]["is"] == "the flat left face"
    assert [datum["name"] for datum in grown["datums"]] == ["the left end face",
                                                            "the ring axis"]
    assert grown["conflicts"][0]["stands"] == "the flat left face"


def test_what_is_not_defined_accumulates_without_repeating_itself():
    entry = {"what": "the general tolerance", "looked_in": ["the notes"]}
    grown = merge(merge({}, {"not_defined": [entry]}), {"not_defined": [entry]})

    assert grown["not_defined"] == [entry]


def test_a_fragment_that_is_not_a_model_leaves_the_part_as_it_was():
    """
    Design answering with prose is a phase that did not do its job, and losing
    the model over it would be worse than growing nothing. The empty answer and
    the unusable one are the same case, the way an empty acceptance list is.
    """
    assert merge(DEPTH_OFF_THE_SECTION, "a 150 x 50 x 50 block") == DEPTH_OFF_THE_SECTION
    assert merge(DEPTH_OFF_THE_SECTION, None) == DEPTH_OFF_THE_SECTION
    assert merge(None, {"features": []}) == {"features": []}


def test_merging_does_not_touch_what_it_was_given():
    """The model on the stream is what the Gate showed; growth is a new one."""
    held = {"features": [{"id": "hole-40-left", "kind": "Ø40 hole"}]}
    merge(held, {"features": [{"id": "hole-40-left", "topology": "through"},
                              {"id": "hole-35"}]})

    assert held == {"features": [{"id": "hole-40-left", "kind": "Ø40 hole"}]}


# ── what the Plan Gate shows ──────────────────────────────────────────────────


THE_PART = {
    "datums": [{"name": "the left end face", "is": "the flat face at the left"}],
    "envelope": {"length": _read("150 mm"), "depth front to rear": _read("100 mm")},
    "features": [{"id": "hole-40-left", "kind": "Ø40 hole", "topology": "through",
                  "position": {"the left end face": _read("20 mm")},
                  "seen_in": ["front view", "SECTION B-B"]}],
    "conflicts": [{"about": "features → hole-40-left → position → the left end face",
                   "stands": _read("20 mm"), "was_also_said": _read("25 mm")}],
    "not_defined": [{"what": "the general tolerance", "looked_in": ["the notes"]}],
}


def test_the_part_reads_as_a_structure_and_not_as_json():
    written = render(THE_PART)

    assert "{" not in written and '"' not in written, "the Gate was shown a JSON dump"
    for heading in ("Datums", "Envelope", "Features", "In conflict", "Not defined"):
        assert heading in written, "the Gate lost the " + heading + " section"


def test_every_value_carries_its_datum_and_its_flag_on_the_line():
    """FCC-87's discipline, structural now rather than one line of prose per value."""
    written = render(THE_PART)

    assert "the left end face: 20 mm (read)" in written
    assert "length: 150 mm (read)" in written
    assert "seen in: front view, SECTION B-B" in written
    assert "- hole-40-left" in written
    assert "kind: Ø40 hole" in written and "topology: through" in written


def test_a_conflict_and_a_gap_are_shown_apart_from_the_part():
    written = render(THE_PART)
    conflicts = written.split("In conflict")[1]

    assert "stands: 20 mm (read)" in conflicts
    assert "was also said: 25 mm (read)" in conflicts
    assert "the general tolerance" in written.split("Not defined")[1]


def test_a_part_nobody_has_described_renders_as_nothing():
    assert render({}) == ""
    assert render(None) == ""
