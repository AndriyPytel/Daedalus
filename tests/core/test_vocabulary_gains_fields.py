"""
The closed vocabulary gains the fields the facts are already written in --
a canonical order for a feature `id`'s `place` tokens, a `direction` field
for a datum-measured value, and a closed `goes_through` flag beside the
free-text `topology` field. This changes what the vocabulary declares,
not any comparison or Gate logic that reads it -- `merge()` is untouched,
and these tests are its regression proof.
"""
import re

from core.design.core import PLACE
from core.design.merge import merge
from core.prompts import read_prompt


def _language_md():
    return read_prompt("language.md")


def test_place_list_matches_the_language_prompt():
    """
    `PLACE` must be the same list
    `language.md` declares -- read out of the prompt's own wording, not
    copied here, so the two cannot drift apart silently.
    """
    listed = re.search(r"place\s+is one or two of \(([^)]+)\)", _language_md())
    tokens = tuple(word.strip() for word in listed.group(1).split(","))

    assert tokens == PLACE


def test_language_md_declares_the_canonical_place_order():
    """The id vocabulary must say two place tokens are always written in
    `PLACE`'s order, or a model has no way to know which of two readings
    is the one to write."""
    assert "front-inner` never `inner-front" in _language_md()


def _read(value):
    return {"value": value, "flag": "read"}


A_HOLE_MEASURED_FROM_A_NAMED_END = {
    "features": [{"id": "hole-left", "kind": "Ø10 hole",
                  "position": {"the left end face": _read("20 mm")},
                  "direction": "from the left end"}],
}


def test_merge_leaves_a_filled_direction_field_unchanged():
    grown = merge({}, A_HOLE_MEASURED_FROM_A_NAMED_END)

    assert grown["features"][0]["direction"] == "from the left end"


A_HOLE_WITH_NO_DIRECTION_ESTABLISHED = {
    "features": [{"id": "hole-centre", "kind": "Ø10 hole",
                  "position": {"the centre plane": _read("0 mm")}}],
}


def test_merge_leaves_direction_absent_rather_than_guessed():
    grown = merge({}, A_HOLE_WITH_NO_DIRECTION_ESTABLISHED)

    assert "direction" not in grown["features"][0]


A_SLOT_THROUGH_IN_PROSE_BUT_UNFLAGGED = {
    "features": [{"id": "slot-centre", "kind": "slot",
                  "topology": "runs through the boss from the top face"}],
}


def test_a_feature_with_through_in_its_prose_but_no_flag_is_not_flagged():
    """
    `topology` carries "through" inside a longer sentence; the closed
    `goes_through` flag is never written. Reading the flag alone -- the
    only way C10 will read it -- must not see this feature as
    through, whatever its prose says.
    """
    grown = merge({}, A_SLOT_THROUGH_IN_PROSE_BUT_UNFLAGGED)

    feature = grown["features"][0]
    assert "through" in feature["topology"]
    assert feature.get("goes_through") is not True


def test_language_md_declares_goes_through_read_in_isolation_from_topology():
    assert "goes_through" in _language_md()
    assert "never inferred from the word \"through\"" in _language_md()


THE_BOSS_CENTRE_READING = {
    "features": [{"id": "boss-centre", "kind": "boss",
                  "seen_in": ["the isometric on page 1", "the iso view, page 1"]}],
}


def test_merge_of_the_boss_centre_reading_does_not_raise_and_leaves_seen_in_unchanged():
    """
    Two `seen_in` values naming the same view in different words -- proof
    that adding fields did not touch `merge()`'s existing, unrelated
    behaviour: `seen_in` is not deduplicated by this ticket, in scope or
    out.
    """
    grown = merge({}, THE_BOSS_CENTRE_READING)

    assert grown["features"][0]["seen_in"] == [
        "the isometric on page 1", "the iso view, page 1"]
