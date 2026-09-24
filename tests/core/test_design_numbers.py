"""
A dimensional value in the Design model carries its number next to its text
-- `mm` for a length, `deg` for an angle, a list for a range -- so a Step
reads `design[...]["mm"]` instead of parsing prose. `merge` refuses a value
that names a single mm/deg magnitude and does not carry it, checked only
under `kind_vocabulary: closed` the same way `_refused_kinds` is, so a
recorded fixture written against the older prose-only grammar is untouched.
`distilled` keeps the number, and `lint` already accepts a Step reading it
by index.
"""
from core.design import core
from core.design.merge import merge
from core.design.report import distilled
from core.lint import lint


CLOSED = "closed"


def _read(value):
    return {"value": value, "flag": "read"}


def test_a_single_mm_value_with_no_mm_field_is_refused():
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "pad-outer", "radius": _read("24 mm")}]})

    assert grown["features"][0].get("radius") is None
    conflict = grown["conflicts"][0]
    assert conflict["about"] == "features → pad-outer → radius"
    assert conflict["kind"] == core.A_CONFLICT


def test_a_single_mm_value_carrying_its_number_is_written_normally():
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "pad-outer",
                                      "radius": dict(_read("24 mm"), mm=24)}]})

    assert grown["features"][0]["radius"] == {"value": "24 mm", "flag": "read", "mm": 24}
    assert grown.get("conflicts") in (None, [])


def test_a_single_degree_value_with_no_deg_field_is_refused():
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "lug-slot-8x", "angle": _read("45°")}]})

    assert grown["features"][0].get("angle") is None
    assert grown["conflicts"][0]["about"] == "features → lug-slot-8x → angle"


def test_a_degree_value_carrying_its_number_is_written_normally():
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "lug-slot-8x",
                                      "angle": dict(_read("45°"), deg=45)}]})

    assert grown["features"][0]["angle"]["deg"] == 45
    assert grown.get("conflicts") in (None, [])


def test_a_range_is_a_list_under_mm_and_is_refused_without_it():
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "pad-outer",
                                      "position": {"the bottom face":
                                                    _read("from 0 to 24 mm")}}]})

    assert grown["features"][0]["position"].get("the bottom face") is None
    assert grown["conflicts"][0]["about"] == (
        "features → pad-outer → position → the bottom face")


def test_a_range_carrying_its_list_is_written_normally():
    entry = dict(_read("from 0 to 24 mm"), mm=[0, 24])
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "pad-outer",
                                      "position": {"the bottom face": entry}}]})

    assert grown["features"][0]["position"]["the bottom face"]["mm"] == [0, 24]
    assert grown.get("conflicts") in (None, [])


def test_an_envelope_value_is_checked_the_same_way():
    grown = merge({}, {"kind_vocabulary": CLOSED, "envelope": {"height": _read("45 mm")}})

    assert grown["envelope"].get("height") is None
    assert grown["conflicts"][0]["about"] == "envelope → height"


def test_a_count_with_no_unit_is_not_a_dimension_and_raises_nothing():
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "fillet-outer", "edges": _read("4")}]})

    assert grown["features"][0]["edges"] == _read("4")
    assert grown.get("conflicts") in (None, [])


def test_prose_naming_more_than_one_magnitude_is_left_to_the_reader():
    """
    A field whose text carries more than one measurement -- an explanatory
    `tangent_to`, an envelope entry giving two different faces' distances --
    names no single number to attach, so it is not refused.
    """
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "fillet-outer",
                                      "tangent_to": _read(
                                          "70.4 mm either side and 42 mm either side")}]})

    assert grown["features"][0]["tangent_to"] == _read(
        "70.4 mm either side and 42 mm either side")
    assert grown.get("conflicts") in (None, [])


def test_a_refused_dimension_is_a_gap_the_next_turn_may_fill():
    first = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "pad-outer", "radius": _read("24 mm")}]})

    grown = merge(first, {"kind_vocabulary": CLOSED,
                          "features": [{"id": "pad-outer",
                                        "radius": dict(_read("24 mm"), mm=24)}]})

    assert grown["features"][0]["radius"]["mm"] == 24


def test_an_unmarked_offer_is_not_checked_for_its_number():
    """A recorded model has no `kind_vocabulary` marker -- it was written
    before this ticket, against the prose-only grammar -- so it takes the
    same legacy unvalidated path `_refused_kinds` already takes."""
    grown = merge({}, {"features": [{"id": "pad-outer", "radius": _read("24 mm")}]})

    assert grown["features"][0]["radius"] == _read("24 mm")
    assert grown.get("conflicts") in (None, [])


def test_distilled_keeps_the_number_beside_the_text():
    model = {"features": [{"id": "pad-outer",
                            "thickness": dict(_read("24 mm"), mm=24, **{"from": "a callout"})}]}

    document = distilled(model)

    assert document["features"][0]["thickness"]["mm"] == 24
    assert document["features"][0]["thickness"]["value"] == "24 mm"
    assert "from" not in document["features"][0]["thickness"]


def test_a_step_reading_the_number_by_key_passes_lint():
    assert lint("pad.Length = design['features']['pad-outer']['thickness']['mm']\n").ok


def test_design_md_documents_the_mm_and_deg_fields():
    from core.prompts import read_prompt

    prompt = read_prompt("design.md")

    assert '"mm": 100' in prompt
    assert '"deg": 45' in prompt
    assert '"mm": [0, 24]' in prompt
