"""
A rib's sloped, rounded top gains two fields the closed vocabulary already
has the shape for -- `slope` and `rounding`, in the drawing's own terms --
so a Step reads them instead of re-deriving them with `atan`/`sin` each
attempt. This changes what the vocabulary declares, not any comparison or
Gate logic that reads it -- `merge()` is untouched, and these tests are its
regression proof, the same shape as the vocabulary's other closed fields.
"""
from core.design.merge import merge
from core.prompts import read_prompt


def _language_md():
    return read_prompt("language.md")


def test_language_md_declares_the_rib_top_slope_field():
    assert "`slope`" in _language_md()


def test_language_md_declares_the_rib_top_rounding_field():
    assert "`rounding`" in _language_md()


def test_language_md_tells_a_step_to_read_the_fields_not_derive_them():
    assert "never re-derived" in _language_md()


A_RIB_WITH_SLOPE_AND_ROUNDING = {
    "features": [{"id": "rib-centre", "kind": "rib",
                  "height": {"value": "15 mm"}, "thickness": {"value": "4 mm"},
                  "slope": {"value": "18.43 deg"}, "rounding": {"value": "2 mm"}}],
}


def test_merge_leaves_filled_slope_and_rounding_fields_unchanged():
    grown = merge({}, A_RIB_WITH_SLOPE_AND_ROUNDING)

    feature = grown["features"][0]
    assert feature["slope"]["value"] == "18.43 deg"
    assert feature["rounding"]["value"] == "2 mm"


A_RIB_WITH_NEITHER_ESTABLISHED = {
    "features": [{"id": "rib-left", "kind": "rib",
                  "height": {"value": "10 mm"}}],
}
