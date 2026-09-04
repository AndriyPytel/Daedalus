"""
Measured evidence from the four distinct Model Mania drawings and the recorded
number lists that describe them.
"""
import statistics

import pytest

from core import design, sheet_reader
from tests import modelmania_harness


def test_recorded_descriptions_keep_their_own_sheet_and_fail_on_the_others(
        modelmania_sheets):
    """
    The 32 recorded descriptions invent 4 to 8 numbers against their own
    drawings and 150 to 180 against the other three drawings. The bands leave
    room for a tracing adjustment while making a changed reading visible.
    """
    own, other = [], []
    for name, descriptions in modelmania_harness.described_numbers().items():
        for description in descriptions:
            stated = {"stated": description["stated"]}
            own.append(len(sheet_reader.invented(modelmania_sheets[name], stated)))
            other.extend(
                len(sheet_reader.invented(sheet, stated))
                for other_name, sheet in modelmania_sheets.items()
                if other_name != name)

    assert len(own) == 32
    assert 4 <= sum(own) <= 8, own
    assert 150 <= sum(other) <= 180, other


@pytest.mark.freecad
def test_the_four_sheets_keep_their_measured_reading_verdicts(modelmania_sheets):
    """
    The four sheets produce 142 outlines, all of which build and solve. Eleven
    face-to-face distances reach outlines, and every sheet has no disagreement.
    Movement remains within the measured bands: 0.15 to 0.30 mm at the median,
    0.50 to 0.80 mm at nine in ten, and 1.0 to 1.5 mm at the worst.

    The bands are wider than one reading because the solver starts from a point
    it picks itself, so the same 142 outlines move differently run to run. A
    hundred repeats in one process gave 0.202 to 0.260 mm at the median, 0.568
    to 0.720 mm at nine in ten, and 1.197 to 1.375 mm at the worst; a run of the
    whole suite, where the outlines are built inside an xdist worker, produced
    0.766 mm at nine in ten, above anything the hundred in-process repeats
    reached. The upper end of the middle band is that number rounded up, not a
    figure chosen to make the test pass.
    """
    verdicts = modelmania_harness.verdicts(modelmania_sheets)
    by_sheet = {
        name: [verdict for verdict_name, verdict in verdicts if verdict_name == name]
        for name in modelmania_harness.SHEETS
    }

    assert {name: len(found) for name, found in by_sheet.items()} == {
        "mm25-p1-1.pdf": 25,
        "mm25-p2.pdf": 21,
        "model-mania-2024.pdf": 52,
        "model-mania_-in-person-2023 copy.pdf": 44,
    }
    assert all(verdict is not None for _, verdict in verdicts)
    assert all(verdict.geometry == verdict.entities for _, verdict in verdicts)
    assert all(verdict.solved == 0 for _, verdict in verdicts)
    assert all(not verdict.conflicts and not verdict.redundant
               for _, verdict in verdicts)
    assert {name: sum(verdict.distances for verdict in found)
            for name, found in by_sheet.items()} == {
        "mm25-p1-1.pdf": 4,
        "mm25-p2.pdf": 2,
        "model-mania-2024.pdf": 3,
        "model-mania_-in-person-2023 copy.pdf": 2,
    }
    assert sum(verdict.distances > 0 for _, verdict in verdicts) == 10
    assert all(not verdict.disagrees for _, verdict in verdicts)

    moved = [verdict.moved for _, verdict in verdicts]
    assert 0.15 <= statistics.median(moved) <= 0.30
    assert 0.50 <= statistics.quantiles(moved, n=10, method="inclusive")[8] <= 0.80
    assert 1.0 <= max(moved) <= 1.5


def test_the_document_carries_nothing_off_the_title_block_and_no_dead_feature(
        modelmania_sheets):
    """
    What the code removes at distillation, measured on the sheet the recorded
    run read. Of the 230 strings the 2023 clamp prints, 121 stand inside the
    title block — a drawing number, a date, a scale and the small print — and
    the sweep carrying them is a record of the sheet rather than of the part.
    Neither are the two features page 2 deletes, which the reading keeps for
    the user checking it against the drawing and the document does not carry
    at all.
    """
    sheet = modelmania_sheets["model-mania_-in-person-2023 copy.pdf"]
    swept = sheet_reader.printed_on_the_sheet(sheet)
    in_the_title_block = [entry for entry, printed in zip(swept, sheet.printed)
                          if sheet_reader._in_the_title_block(printed, sheet)]
    reading = modelmania_harness.the_2023_clamp_as_read()
    document = design.distilled(reading)

    assert (len(swept), len(in_the_title_block)) == (230, 121)
    assert reading["printed_on_the_sheet"] == swept, "the run read another sheet"
    assert "printed_on_the_sheet" not in document
    assert [feature["id"] for feature in document["features"]] == \
        [feature["id"] for feature in reading["features"]
         if not feature.get("superseded_by")]
    assert "superseded" in design.render(reading), "the reading kept no dead feature"
    assert "superseded" not in design.render(document)


def test_nothing_the_document_needs_is_cut_out_of_what_design_is_shown(
        modelmania_sheets):
    """
    The four sheets print 115 callouts a part could be built from and are drawn
    with 961 contours traced off the pictures their views were placed as. Every
    one of both reaches Design after the cut. What the cut leaves out is the
    1,289 entities nothing on the sheet can prove anything about, and the title
    block, which the sweep goes on carrying.
    """
    dimensions, contours, left_out = {}, {}, {}
    for name, sheet in modelmania_sheets.items():
        entities = [entity for view in sheet.drawn for entity in view.entities]
        printed = {entry for callout in sheet_reader._the_callouts(sheet)
                   for entry in callout}
        said = sheet_reader._the_drawn_list(sheet)
        named = [line[4:] for line in said.split("\n")
                 if line.startswith("    ") and not line.startswith("    (")]

        assert set(sheet_reader.dimensions(sheet)) <= printed, name
        assert named == [sheet_reader._said(entity) for entity in entities
                         if entity.role != "unclassified"], name
        dimensions[name] = len(sheet_reader.dimensions(sheet))
        contours[name] = len([entity for entity in entities
                              if entity.role == "contour"])
        left_out[name] = len([entity for entity in entities
                              if entity.role == "unclassified"])

    assert (sum(dimensions.values()), sum(contours.values()),
            sum(left_out.values())) == (115, 961, 1289)


def test_the_recorded_run_ends_in_the_document_it_ended_in_before_the_cut():
    """
    The other half of the criterion: the cut is right only if what comes out of
    Design is no poorer for it. The recorded reading of the 2023 clamp distils
    to 11 features and 46 Acceptance entries, and the run read every one of them
    off the sheet this cut is measured on.
    """
    document = design.distilled(modelmania_harness.the_2023_clamp_as_read())

    assert [feature["id"] for feature in document["features"]] == [
        "outer-profile", "ring-bore", "ring-outer-cylinder", "right-lug",
        "right-lug-hole", "right-lug-hex-recess", "arm-cutout", "ring-split",
        "edge-fillets", "angled-left-arm", "left-arm-slot"]
    assert len([entry["says"] for entry in design.entries(document)]) == 46
    assert "envelope \u2192 depth front to rear between the exterior faces: 30 mm " \
           "(read)" in [entry["says"] for entry in design.entries(document)]
