"""
Level 1 -- the BIM Assertion refuses Equipment whose Source is not allowed or that sits under no Level.
"""
from core.agent.bim import refusals
from tests.bim_helpers import HOLDS

LIBRARY = "Architectural Parts/Chairs/Office_chair.FCStd"
ENTRIES = lambda: [LIBRARY]
ITEM = {"label": "Sofa", "source": "parametric", "level": True}
FACTS = HOLDS | {"equipment": [ITEM], "ifc": HOLDS["ifc"] | {"IfcFurnishingElement": 1}}


def test_equipment_with_an_allowed_source_on_a_level_is_refused_nothing():
    for source in ("parametric", "generated on request", LIBRARY):
        assert refusals(FACTS | {"equipment": [ITEM | {"source": source}]}, ENTRIES) == []


def test_equipment_from_outside_the_whitelist_is_named():
    for source in ("https://sketchfab.com/sofa.glb", ""):
        found = refusals(FACTS | {"equipment": [ITEM | {"label": "Sofa001", "source": source}]}, ENTRIES)

        assert len(found) == 1 and "Sofa001" in found[0] and "Source" in found[0]


def test_equipment_with_a_library_path_the_index_lacks_is_named():
    found = refusals(FACTS | {"equipment": [ITEM | {"label": "Fake", "source": "Architectural Parts/Fake/Chair.FCStd"}]},
                     ENTRIES)

    assert len(found) == 1 and "Fake" in found[0] and "Source" in found[0]


def test_equipment_under_no_level_is_named():
    found = refusals(FACTS | {"equipment": [ITEM | {"label": "Bed", "level": False}]}, ENTRIES)

    assert len(found) == 1 and "Bed" in found[0] and "Level" in found[0]


def test_equipment_missing_from_the_ifc4_export_is_named():
    found = refusals(FACTS | {"ifc": HOLDS["ifc"] | {"IfcFurnishingElement": 0}}, ENTRIES)

    assert len(found) == 1 and "IfcFurnishingElement" in found[0]


def test_a_flat_with_no_equipment_is_refused_nothing_for_it():
    assert refusals(HOLDS | {"equipment": []}, ENTRIES) == []


def test_the_index_is_read_only_where_the_flat_holds_equipment():
    def index():
        raise AssertionError("the index was read")

    assert refusals(HOLDS, index) == []
