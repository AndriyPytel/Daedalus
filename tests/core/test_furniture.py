"""
Level 1 -- furniture comes from the whitelist only: the fetch helper refuses any other URL, the library's index is fetched once and searched, and what nothing fits falls back to a parametric kind.
"""
import json

import pytest

from core import furniture
from core.furniture import RAW
from tests.furniture_helpers import FakeGet, library_file

KINDS = ("bed", "sofa", "table", "chair", "wardrobe", "toilet", "basin", "bath", "shower", "fridge",
         "cooker", "sink")


def test_a_url_outside_the_whitelist_is_refused_and_nothing_is_asked_for():
    get = FakeGet()

    for url in ("https://sketchfab.com/models/1.glb", "https://example.com/FreeCAD/FreeCAD-library/x",
                RAW + "../../evil/x.FCStd", "http://raw.githubusercontent.com/FreeCAD/FreeCAD-library/x"):
        with pytest.raises(furniture.Refused):
            furniture.fetch(url, get)

    assert get.urls == []


def test_a_url_under_the_library_is_fetched():
    get = FakeGet(b"bytes")

    assert furniture.fetch(RAW + "Architectural Parts/x.FCStd", get) == b"bytes"


def test_the_index_is_fetched_once_and_read_from_disk_after(tmp_path):
    get = FakeGet()

    first = furniture.index(str(tmp_path), get)
    second = furniture.index(str(tmp_path), get)

    assert first == second and len(get.urls) == 1
    assert "Architectural Parts/Bedroom/FCBL_bed_double.FCStd" in first


def test_the_index_holds_only_the_architectural_parts_that_are_freecad_files(tmp_path):
    entries = furniture.index(str(tmp_path), FakeGet())

    assert all(e.startswith("Architectural Parts/") and e.endswith(".FCStd") for e in entries)


def test_a_query_finds_candidates_with_path_url_and_licence(tmp_path):
    found = furniture.find("double bed", furniture.index(str(tmp_path), FakeGet()))

    assert found[0]["path"] == "Architectural Parts/Bedroom/FCBL_bed_double.FCStd"
    assert found[0]["url"] == RAW + "Architectural Parts/Bedroom/FCBL_bed_double.FCStd"
    assert found[0]["licence"] == "CC-BY 3.0"


def test_a_query_that_matches_nothing_finds_nothing(tmp_path):
    assert furniture.find("dragon", furniture.index(str(tmp_path), FakeGet())) == []


def test_every_candidate_url_is_inside_the_whitelist(tmp_path):
    for found in furniture.find("bed chair toilet", furniture.index(str(tmp_path), FakeGet())):
        assert furniture.permitted(found["url"])


def test_the_table_of_typical_sizes_has_the_twelve_kinds_as_width_depth_height_in_mm():
    assert set(furniture.SIZES) == set(KINDS)
    assert all(len(size) == 3 and all(v > 0 for v in size) for size in furniture.SIZES.values())


def test_find_furniture_answers_the_candidates(tmp_path):
    calls = furniture.calls(str(tmp_path), FakeGet())

    answer = calls["find_furniture"]({"query": "toilet"})

    assert [c["path"] for c in answer["candidates"]] == ["Architectural Parts/Hydro equipment/WallHungToilet.FCStd"]


def test_place_furniture_of_a_candidate_outside_the_whitelist_is_refused_without_a_download(tmp_path):
    get = FakeGet()
    calls = furniture.calls(str(tmp_path), get)
    evil = {"title": "Sofa", "path": "sofa.glb", "url": "https://sketchfab.com/sofa.glb", "licence": "CC-BY 3.0"}

    answer = calls["place_furniture"]({"candidate": evil, "x": 0, "y": 0, "rotation": 0, "level": "Level"})

    assert "whitelist" in answer["error"] and "code" not in answer and not [u for u in get.urls if "sketchfab" in u or u.endswith(".glb")]


def test_place_furniture_downloads_the_file_once_and_answers_the_code_that_places_it(tmp_path):
    get = FakeGet()
    calls = furniture.calls(str(tmp_path), get)
    found = furniture.find("chair", furniture.index(str(tmp_path), get))[0]
    arguments = {"candidate": found, "x": 100, "y": 200, "rotation": 90, "level": "Level"}

    first = calls["place_furniture"](arguments)
    calls["place_furniture"](arguments)

    assert "place_library" in first["code"] and "100" in first["code"] and "'Level'" in first["code"]
    assert len([u for u in get.urls if u.endswith(".FCStd")]) == 1


def test_place_parametric_of_a_known_kind_answers_the_code_and_of_an_unknown_one_an_error(tmp_path):
    calls = furniture.calls(str(tmp_path), FakeGet())

    placed = calls["place_parametric"]({"kind": "sofa", "x": 0, "y": 0, "rotation": 0, "level": "Level"})
    refused = calls["place_parametric"]({"kind": "dragon", "x": 0, "y": 0, "rotation": 0, "level": "Level"})

    assert "place_parametric" in placed["code"] and "dragon" in refused["error"]


def test_the_attribution_names_title_author_licence_and_url_of_each_library_item_once():
    item = {"title": "Office chair", "author": "Ada Lovelace", "licence": "CC-BY 3.0",
            "licence_url": "https://creativecommons.org/licenses/by/3.0/",
            "url": RAW + "Architectural Parts/Chairs/Office_chair.FCStd"}

    text = furniture.attribution([item, item])

    assert text.count("Office chair") == 1
    assert all(item[k] in text for k in ("author", "licence", "licence_url", "url"))


def test_a_source_is_parametric_generated_on_request_or_a_file_of_the_index():
    entries = ["Architectural Parts/Chairs/Office_chair.FCStd"]

    assert furniture.sourced("parametric", entries) and furniture.sourced("generated on request", entries)
    assert furniture.sourced("Architectural Parts/Chairs/Office_chair.FCStd", entries)
    assert not furniture.sourced("Architectural Parts/Fake/Chair.FCStd", entries)
    assert not furniture.sourced("https://sketchfab.com/x", entries) and not furniture.sourced("", entries)


def test_a_source_is_shortened_by_the_raw_prefix_alone():
    assert furniture.short(RAW + "Architectural Parts/x.FCStd") == "Architectural Parts/x.FCStd"
    assert furniture.short("https://sketchfab.com/x") == "https://sketchfab.com/x"


def _placed(tmp_path, get, path="Architectural Parts/Chairs/Office_chair.FCStd"):
    calls = furniture.calls(str(tmp_path), get)
    found = next(c for c in furniture.find("chair", furniture.index(str(tmp_path), get)) if c["path"] == path)
    return calls["place_furniture"]({"candidate": found, "x": 0, "y": 0, "rotation": 0, "level": "Level"})


def test_the_author_is_the_one_the_file_names(tmp_path):
    get = FakeGet(library_file("Ada Lovelace"), history=["Newest", "First"])

    code = _placed(tmp_path, get)["code"]

    assert "Ada Lovelace" in code and not [u for u in get.urls if "/commits?" in u]


def test_the_author_is_the_first_committer_where_the_file_names_none_and_is_asked_once(tmp_path):
    get = FakeGet(library_file(""), history=["Newest", "Middle", "First Author"])

    code = _placed(tmp_path, get)["code"]
    _placed(tmp_path, get)

    assert "First Author" in code and len([u for u in get.urls if "/commits?" in u]) == 1


def test_the_author_is_unrecorded_with_the_history_url_where_file_and_history_name_none(tmp_path):
    code = _placed(tmp_path, FakeGet(library_file(""), history=[]))["code"]

    assert "author not recorded" in code
    assert "https://github.com/FreeCAD/FreeCAD-library/commits/master/Architectural%20Parts/Chairs/Office_chair.FCStd" in code


def test_the_licence_is_the_one_the_file_declares_else_cc_by_3_with_its_uri(tmp_path):
    declared = _placed(tmp_path, FakeGet(library_file("A", "CC-BY-SA 4.0", "https://example.org/sa")))["code"]
    default = _placed(tmp_path / "other", FakeGet())["code"]

    assert "CC-BY-SA 4.0" in declared and "https://example.org/sa" in declared
    assert "CC-BY 3.0" in default and "https://creativecommons.org/licenses/by/3.0/" in default


def test_place_furniture_of_a_path_the_index_lacks_is_refused_without_a_download(tmp_path):
    get = FakeGet()
    fake = {"title": "Chair", "path": "Architectural Parts/Fake/Chair.FCStd",
            "url": RAW + "Architectural Parts/Fake/Chair.FCStd", "licence": "CC-BY 3.0"}

    answer = furniture.calls(str(tmp_path), get)["place_furniture"](
        {"candidate": fake, "x": 0, "y": 0, "rotation": 0, "level": "Level"})

    assert "whitelist" in answer["error"] and not [u for u in get.urls if u.endswith(".FCStd")]


def test_a_default_licence_of_the_file_is_no_declaration(tmp_path):
    code = _placed(tmp_path, FakeGet(library_file("A", "All rights reserved", "https://en.wikipedia.org/wiki/All_rights_reserved")))["code"]

    assert "CC-BY 3.0" in code and "All rights reserved" not in code
