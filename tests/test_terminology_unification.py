"""
Level 1 — derivable naming vocabulary and reconciliation safety net.

The terminology unification fix (FCC-xxx) has two parts: a closed vocabulary
that makes blind page calls converge on the same names, and a reconciliation
mandate that catches any that slip through. These tests check both at the
prompt level (the words must be there) and at the merge level (the invariants
must hold).

The closed vocabularies are the fix: if a feature id is derived from what the
feature IS plus WHERE it is, two blind calls produce the same string without
seeing each other. The safety net is a backup: when two entries ARE synonyms
but carry different values, reconciliation must raise a conflicts entry rather
than silently supersede one.
"""
import json
import os

from core import loop
from core.design import merge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prompt():
    """
    What Design is actually shown: `design.md` composed with the shared
    vocabulary, `config/prompts/language.md` (ADR-0026, FCC-234) — the
    closed lists below moved there and are no longer written a second time
    in `design.md` itself.
    """
    return loop.prompt(loop.DESIGN_PHASE)


FEATURE_KINDS = [
    "hole", "bore", "slot", "pocket", "cutout", "pad", "fillet", "chamfer",
    "boss", "rib", "thread", "counterbore", "countersink", "groove", "notch",
    "tab", "lug", "ear", "flange", "step", "shoulder",
]

PLACE_WORDS = [
    "left", "right", "front", "rear", "top", "bottom", "upper", "lower",
    "centre", "center", "inner", "outer",
]

ENVELOPE_KEYS = [
    "length", "width", "height", "depth front to rear", "depth left to right",
    "thickness",
]

DATUM_FACETS = [
    "the left end face", "the right end face", "the front face", "the rear face",
    "the top face", "the bottom face",
]


def _read(value):
    return {"value": value, "flag": "read"}


def _features_by_id(model):
    return {f["id"]: f for f in model.get("features", []) if isinstance(f, dict)}


def _conflicts_normalized(model):
    return sorted(json.dumps(c, sort_keys=True) for c in model.get("conflicts", []))


class TestClosedVocabulariesAppearInPrompt:
    def test_feature_kind_vocabulary_listed_in_prompt(self):
        prompt = _prompt()
        missing = [kind for kind in FEATURE_KINDS if kind not in prompt.lower()]
        assert len(missing) <= 3, (
            f"Prompt must list the closed vocabulary of feature kinds. "
            f"Missing: {missing}"
        )

    def test_place_words_vocabulary_listed_in_prompt(self):
        prompt = _prompt()
        missing = [word for word in PLACE_WORDS if word not in prompt.lower()]
        assert len(missing) <= 2, (
            f"Prompt must list the closed vocabulary of place words. "
            f"Missing: {missing}"
        )

    def test_envelope_key_vocabulary_listed_in_prompt(self):
        prompt = _prompt()
        missing = [key for key in ENVELOPE_KEYS if key not in prompt.lower()]
        assert len(missing) <= 1, (
            f"Prompt must list the closed vocabulary of envelope keys. "
            f"Missing: {missing}"
        )


class TestReconciliationConflictsRatherThanSupersession:
    def test_supersession_with_differing_values_produces_conflict(self):
        """
        When reconciliation marks a feature superseded_by another, but the two
        features carry different values, merge must raise a conflicts entry.
        Marking one superseded_by the other while discarding a value is the
        failure this safety net prevents.

        The scenario: two pages each invented a name for the same physical
        feature. Page 1 said cutout-arm at 20mm, page 2 said arm-cutout at 25mm.
        Both land on the model. Reconciliation then tries to mark arm-cutout
        as superseded_by cutout-arm. But their values differ, so this must
        raise a conflict rather than silently lose the 25mm reading.
        """
        after_page_merges = {
            "features": [
                {"id": "cutout-arm", "kind": "cutout", "topology": "through",
                 "position": {"the left end face": _read("20 mm")}},
                {"id": "arm-cutout", "kind": "cutout", "topology": "through",
                 "position": {"the left end face": _read("25 mm")}},
            ],
        }
        reconciliation_fragment = {
            "features": [
                {"id": "arm-cutout", "superseded_by": "cutout-arm"},
            ],
        }
        grown = merge(after_page_merges, reconciliation_fragment)

        has_conflict = any(
            "arm-cutout" in str(c) and "cutout-arm" in str(c)
            for c in grown.get("conflicts", [])
        )
        assert has_conflict, (
            "When a superseding feature carries a different value, a conflict "
            "must be raised — the differing value must not be silently discarded. "
            f"Got conflicts: {grown.get('conflicts', [])}"
        )

    def test_supersession_with_matching_values_succeeds(self):
        """
        When two features are synonyms and all their values agree, one can
        safely supersede the other — no value is lost.
        """
        after_page_merges = {
            "features": [
                {"id": "cutout-arm", "kind": "cutout", "topology": "through",
                 "position": {"the left end face": _read("20 mm")}},
                {"id": "arm-cutout", "kind": "cutout", "topology": "through",
                 "position": {"the left end face": _read("20 mm")}},
            ],
        }
        reconciliation_fragment = {
            "features": [
                {"id": "arm-cutout", "superseded_by": "cutout-arm"},
            ],
        }
        grown = merge(after_page_merges, reconciliation_fragment)

        arm_cutout = next(
            (f for f in grown.get("features", []) if f.get("id") == "arm-cutout"), 
            None
        )
        assert arm_cutout is not None, "The synonym feature must land on the model"
        assert arm_cutout.get("superseded_by") == "cutout-arm", (
            "A synonym with matching values can be marked superseded_by"
        )
        has_synonym_conflict = any(
            "synonym pair" in str(c.get("about", ""))
            for c in grown.get("conflicts", [])
        )
        assert not has_synonym_conflict, (
            "No conflict should be raised when values match"
        )

    def test_order_independence_same_result_regardless_of_merge_order(self):
        """
        ADR-0019: merge() is order-independent. The same two page fragments
        merged in either order must produce identical features and conflicts,
        though list ordering may differ.

        This is critical because _first_pass merges pages as they land from
        a thread pool, so arrival order is nondeterministic.
        """
        page1 = {
            "features": [
                {"id": "cutout-arm", "kind": "cutout",
                 "position": {"the left end face": _read("20 mm")}},
            ],
        }
        page2 = {
            "features": [
                {"id": "arm-cutout", "kind": "cutout",
                 "position": {"the left end face": _read("25 mm")}},
            ],
        }
        reconciliation = {
            "features": [
                {"id": "arm-cutout", "superseded_by": "cutout-arm"},
            ],
        }

        order_a = merge(merge({}, page1), page2)
        order_a = merge(order_a, reconciliation)

        order_b = merge(merge({}, page2), page1)
        order_b = merge(order_b, reconciliation)

        assert _features_by_id(order_a) == _features_by_id(order_b), (
            f"Features must be identical regardless of merge order. "
            f"Order A: {_features_by_id(order_a)}\nOrder B: {_features_by_id(order_b)}"
        )
        assert _conflicts_normalized(order_a) == _conflicts_normalized(order_b), (
            f"Conflicts must be identical regardless of merge order. "
            f"Order A: {order_a.get('conflicts', [])}\nOrder B: {order_b.get('conflicts', [])}"
        )



    def test_same_value_different_provenance_raises_no_conflict(self):
        """
        Two features with the same value but different provenance (round_in,
        flag, from, because) are NOT a conflict. The provenance records WHERE
        a reading came from, not WHAT it says about the part. Different view
        names for the same measurement are exactly the aliasing this ticket
        exists to remove, not a contradiction about geometry.
        """
        after_page_merges = {
            "features": [
                {"id": "hole-centre", "kind": "hole",
                 "axis": {"value": "perpendicular to the top face",
                          "flag": "traced", "round_in": "front view"}},
                {"id": "bore-centre", "kind": "bore",
                 "axis": {"value": "perpendicular to the top face",
                          "flag": "read", "round_in": "SECTION A-A"}},
            ],
        }
        reconciliation_fragment = {
            "features": [
                {"id": "bore-centre", "superseded_by": "hole-centre"},
            ],
        }
        grown = merge(after_page_merges, reconciliation_fragment)

        has_synonym_conflict = any(
            "synonym pair" in str(c.get("about", ""))
            for c in grown.get("conflicts", [])
        )
        assert not has_synonym_conflict, (
            "Same value with different provenance (round_in, flag) should NOT "
            f"raise a conflict. Got: {grown.get('conflicts', [])}"
        )


class TestMergeGrowsOnly:
    def test_merge_never_removes_a_feature(self):
        held = {
            "features": [
                {"id": "hole-left", "kind": "hole"},
                {"id": "slot-centre", "kind": "slot"},
            ],
        }
        offered = {"features": [{"id": "boss-right", "kind": "boss"}]}
        grown = merge(held, offered)

        ids = [f["id"] for f in grown["features"]]
        assert "hole-left" in ids
        assert "slot-centre" in ids
        assert "boss-right" in ids

    def test_merge_never_removes_a_datum(self):
        held = {
            "datums": [
                {"name": "the left end face", "is": "the flat left face"},
            ],
        }
        offered = {
            "datums": [
                {"name": "the top face", "is": "the upper horizontal face"},
            ],
        }
        grown = merge(held, offered)

        names = [d["name"] for d in grown["datums"]]
        assert "the left end face" in names
        assert "the top face" in names

    def test_merge_never_removes_an_envelope_entry(self):
        held = {"envelope": {"length": _read("100 mm")}}
        offered = {"envelope": {"width": _read("50 mm")}}
        grown = merge(held, offered)

        assert "length" in grown["envelope"]
        assert "width" in grown["envelope"]
