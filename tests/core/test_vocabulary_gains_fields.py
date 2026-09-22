"""
FCC-416: the closed vocabulary gains the fields the facts are already
written in -- a canonical order for a feature `id`'s `place` tokens, a
`direction` field for a datum-measured value, and a closed `goes_through`
flag beside the free-text `topology` field. This ticket changes what the
vocabulary declares, not any comparison or Gate logic that reads it --
`merge()` is untouched, and these tests are its regression proof.
"""
import re

from core.design.core import PLACE, canonical_place
from core.design.merge import merge
from core.prompts import read_prompt


def _language_md():
    return read_prompt("language.md")


def test_place_list_matches_the_canonical_order_function():
    """
    `PLACE`, the order `canonical_place` sorts by, must be the same list
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


# Every same-token-set fillet/chamfer id pair `docs/research/
# fillet-prompt-adherence.md` (§3, "The permutation-duplicate id
# measurement, verified and extended") quotes verbatim, across the 3 runs
# it found duplicate pairs in. `canonical_place` must collapse each pair
# to one spelling.
_DUPLICATE_ID_PAIRS_FROM_THE_RESEARCH_DOC = (
    ("fillet-inner-front", "fillet-front-inner"),      # mm-effort-sweep/opus-low
    ("fillet-inner-rear", "fillet-rear-inner"),        # mm-effort-sweep/opus-low
    ("fillet-front-outer", "fillet-outer-front"),      # mm-effort-sweep/opus-medium
    ("fillet-rear-outer", "fillet-outer-rear"),        # mm-effort-sweep/opus-medium
    ("fillet-front-left", "fillet-left-front"),        # mm-effort-sweep/opus-medium
    ("fillet-front-right", "fillet-right-front"),      # mm-effort-sweep/opus-medium
    ("fillet-rear-left", "fillet-left-rear"),          # mm-effort-sweep/opus-medium
    ("fillet-rear-right", "fillet-right-rear"),        # mm-effort-sweep/opus-medium
    ("fillet-left-rear", "fillet-rear-left"),          # mm-openai-sweep/gpt-5.6-luna-high
    ("fillet-right-rear", "fillet-rear-right"),        # mm-openai-sweep/gpt-5.6-luna-high
    ("fillet-inner-top", "fillet-top-inner"),          # mm-openai-sweep/gpt-5.6-luna-high
    ("fillet-inner-bottom", "fillet-bottom-inner"),    # mm-openai-sweep/gpt-5.6-luna-high
    ("fillet-inner-left", "fillet-left-inner"),        # mm-openai-sweep/gpt-5.6-luna-high
    ("fillet-inner-right", "fillet-right-inner"),      # mm-openai-sweep/gpt-5.6-luna-high
)


def _canonical_id(feature_id):
    kind, place = feature_id.split("-", 1)
    return kind + "-" + "-".join(canonical_place(place.split("-")))


def test_canonical_place_collapses_every_quoted_duplicate_pair_to_one_id():
    for first, second in _DUPLICATE_ID_PAIRS_FROM_THE_RESEARCH_DOC:
        assert _canonical_id(first) == _canonical_id(second), (first, second)


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
