"""
Level 1 — the file the part Design read off the sheet is written to.

No provider, no FreeCAD. The model in these tests is the one that came out of
the run that built both M6 holes ninety degrees out
(`logs/sessions/549e6232c9e04b659e5a2d536354bd7a.jsonl`), because the whole
point of the file is that a reading like that can be looked at, diffed and
corrected instead of being reconstructed out of a transcript.
"""
import datetime
import os

import pytest

from core import part_file


THE_CLAMP = {
    "datums": [{"name": "the ring axis", "fixes": "the length and the height",
                "is": "the centreline of the Ø50 / Ø42 clamp body"}],
    "envelope": {"length between the ends": {"value": "108 mm", "flag": "read"}},
    "features": [{"id": "right-mounting-hole", "kind": "Ø6.6 through hole",
                  "axis": {"value": "along the height", "flag": "traced"},
                  "position": {"the rightmost end face": {"value": "12.5 mm",
                                                          "flag": "derived"}}}],
    "conflicts": [],
}


@pytest.fixture
def sheet(tmp_path):
    drawing = tmp_path / "model-mania 2023.pdf"
    drawing.write_bytes(b"%PDF-1.7 not really a pdf, but it has a digest\n")
    return str(drawing)


def test_the_part_is_written_where_the_run_can_be_pointed_at_it(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"))

    assert os.path.isfile(path)
    assert path.endswith(".yaml")


def test_the_name_carries_the_sheet_the_digest_and_when_it_was_read(sheet, tmp_path):
    """
    The digest so two different sheets of the same name are two files, and the
    timestamp so a second reading of one sheet sits beside the first rather
    than over it — a reading is compared with the last one by diffing them.
    """
    name = os.path.basename(part_file.saved(THE_CLAMP, sheet,
                                            into=str(tmp_path / "parts")))

    assert name.startswith("model-mania 2023-")
    stamp = name[len("model-mania 2023-"):-len(".yaml")]
    digest, when = stamp.split("-", 1)
    assert len(digest) == 8 and digest.isalnum()
    assert datetime.datetime.strptime(when, part_file.WHEN)


def test_a_second_reading_of_one_sheet_is_a_second_file(sheet, tmp_path):
    into = str(tmp_path / "parts")

    first = part_file.saved(THE_CLAMP, sheet, into=into)
    second = part_file.saved(dict(THE_CLAMP, conflicts=["the axis or the view"]),
                             sheet, into=into)

    assert first != second
    assert len(os.listdir(into)) == 2


def test_what_was_written_is_what_comes_back(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"))

    assert part_file.read(path) == THE_CLAMP


def test_the_file_says_what_the_reading_was_asked_for(sheet, tmp_path):
    """
    A reading is the requirement one run was held to. What it was read off is
    half of that, and what it was read for is the other half.
    """
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"),
                           asked_for="Open the \u00d842 bore to \u00d845.")

    assert part_file.asked_for(path) == "Open the \u00d842 bore to \u00d845."
    assert part_file.read(path) == THE_CLAMP


def test_what_was_asked_for_is_the_first_thing_the_file_says(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"),
                           asked_for="Open the bore.")

    text = open(path, encoding="utf-8").read()
    assert text.index("what_was_asked_for:") < text.index("read_from:") < text.index("datums:")


def test_a_reading_nobody_said_what_they_wanted_says_nothing(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"))

    assert part_file.asked_for(path) == ""


def test_the_file_says_which_sheet_it_was_read_from(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"),
                           read_from={"scale": "0.3528 mm per point"})

    written = part_file.provenance(path)
    assert written["sheet"] == "model-mania 2023.pdf"
    assert written["scale"] == "0.3528 mm per point"
    assert written["digest"] in os.path.basename(path)


def test_the_schema_is_written_so_an_old_file_can_be_told_apart(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"))

    assert part_file.provenance(path)["schema"] == part_file.SCHEMA


def test_the_sections_are_written_in_the_order_the_gate_reads_them(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"))

    text = open(path, encoding="utf-8").read()
    assert text.index("datums:") < text.index("envelope:") < text.index("features:")


def test_a_diameter_sign_survives_the_round_trip(sheet, tmp_path):
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"))

    assert "Ø50" in open(path, encoding="utf-8").read()
    assert "Ø50" in part_file.read(path)["datums"][0]["is"]


def test_a_hand_edited_file_is_what_comes_back(sheet, tmp_path):
    """
    A user who opens the file and changes the axis has changed the requirement.
    Nothing merges their edit with what the model said, and nothing warns them
    off it: what is in the file is the part.
    """
    path = part_file.saved(THE_CLAMP, sheet, into=str(tmp_path / "parts"))
    text = open(path, encoding="utf-8").read().replace("along the height",
                                                       "along the depth")
    open(path, "w", encoding="utf-8").write(text)

    assert part_file.read(path)["features"][0]["axis"]["value"] == "along the depth"


def test_nothing_is_read_from_a_file_that_is_not_one(tmp_path):
    assert part_file.read(str(tmp_path / "nowhere.yaml")) == {}


def test_an_empty_part_is_not_written_at_all(sheet, tmp_path):
    into = str(tmp_path / "parts")

    assert part_file.saved({}, sheet, into=into) == ""
    assert not os.path.isdir(into)
