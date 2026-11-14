"""
Level 1 — the sheet as evidence, before any provider sees it.

No network and no FreeCAD: everything here is measured off
`tests/modelmania/mm25-p1-1.pdf`, the sheet from the recorded run that named
the defect: an overall read as 100 where the sheet prints 108, and a
depth off SECTION B-B that was gone two turns later.

The numbers in these tests are measurements, not preferences. 14 px is what a
dimension digit renders at when the sheet goes out whole; 12.9 pt is what a
callout's box measures on this sheet; 4.1 pt is the paragraph in the title block
declaring the drawing to be the sole property of SolidWorks. Each one is
asserted because each one is a decision this module makes on its own.

SHEET_2023 is the sheet from the same competition and the same draughtsman that
lays its labels out differently: a note that mentions a view, and a section
label printed below the section instead of inside it.
"""
import os
import threading

import pytest

from core.sheets import sheet_reader
from tests import modelmania_harness

SHEET = str(modelmania_harness.MODELMANIA_DIR / "mm25-p1-1.pdf")
RASTER = str(modelmania_harness.MODELMANIA_DIR / "mm2025-phase1.png")
SHEET_2023 = str(modelmania_harness.MODELMANIA_DIR / "model-mania_-in-person-2023 copy.pdf")

needs_pymupdf = pytest.mark.skipif(not sheet_reader.available(),
                                   reason="PyMuPDF is not installed")


def _skip_unless_present(*paths):
    """The `sheet_cache` guard (`tests/conftest.py`), for the tests that read a drawing directly."""
    for path in paths:
        if not os.path.exists(path):
            pytest.skip("sheet not found: %s; set DAE_MODELMANIA_DIR" % path)


@pytest.fixture(scope="module")
def sheet(sheet_cache):
    if not sheet_reader.available():
        pytest.skip("PyMuPDF is not installed")
    return sheet_cache(SHEET)


@pytest.fixture(scope="module")
def sheet_2023(sheet_cache):
    if not sheet_reader.available():
        pytest.skip("PyMuPDF is not installed")
    return sheet_cache(SHEET_2023)


def _printed(sheet):
    """Every string the sheet carries, as the callouts group them."""
    return [entry for line in sheet.callouts for entry in line]


def _shown_in(sheet, at):
    """Which crop the callout printed at these coordinates arrives in."""
    return [entry.seen_in for entry in _printed(sheet) if entry.at == at]


def _overlapping(sheet, box, page=1):
    """The crops of one page that show any part of a patch of it, by view name."""
    return {crop.name.split(", part")[0] for crop in sheet.crops
            if crop.page == page
            and crop.rect[0] < box[2] and box[0] < crop.rect[2]
            and crop.rect[1] < box[3] and box[1] < crop.rect[3]}


@needs_pymupdf
def test_the_crops_are_rendered_far_above_the_cap_the_sheet_used_to_arrive_at(sheet):
    """
    Sent whole, this sheet reaches the provider at 143 DPI and puts 14 px of ink
    in a dimension digit — legible often enough to be trusted and wrong often
    enough to matter. The crops exist to end that, so the DPI has to clear it by
    a distance rather than by a little.
    """
    legible = [crop for crop in sheet.crops if not crop.shape_only]
    assert legible, "a sheet with views on it produced no crops"
    assert all(crop.dpi >= 250 for crop in legible), \
        "a crop was rendered at the resolution that was already misreading"


@needs_pymupdf
def test_the_title_block_s_legal_paragraph_does_not_set_the_resolution(sheet):
    """
    28 of this sheet's 89 words are the 4.1 pt confidentiality notice. Sizing
    the render so *that* is legible puts every crop at 492 DPI and cuts the
    drawing into fourteen tiles, to read a sentence nobody models from. The DPI
    comes off the callouts, which are 12.9 pt, and lands near 279.
    """
    assert all(crop.dpi <= 350 for crop in sheet.crops), \
        "the boilerplate dragged the whole sheet up with it"


@needs_pymupdf
def test_a_crop_is_never_wider_than_the_cap_it_would_be_downscaled_to(sheet):
    """
    Resolution given back at the far end was never gained. A region too big to
    be both whole and legible is sent in pieces, because the resolution is the
    point and the frame is not.
    """
    from PIL import Image

    for crop in sheet.crops:
        assert max(Image.open(crop.path).size) <= sheet_reader.MAX_EDGE, crop.name


@needs_pymupdf
def test_what_is_printed_is_extracted_rather_than_read(sheet):
    """
    The draughtsman's own strings, off the file. This is the whole reason the
    sweep can be checked at all: `150`, `100`, `50` and `20` are on this sheet
    whatever a picture of it looks like.
    """
    printed = [entry.text for entry in _printed(sheet)]

    assert len(printed) == 89
    for callout in ("150", "100", "(R30)", "35", "40", "20", "10", "50"):
        assert callout in printed, callout


@needs_pymupdf
def test_the_diameter_sign_is_missing_from_the_text_layer_and_supplied_anyway(
        sheet):
    """
    Measured, and load-bearing: `Ø` is drawn as geometry on this sheet and is
    not in the text layer, so the bore printed `Ø35 THRU` extracts as `35` and
    `THRU`. Handing that list on as the callouts teaches Design that the sheet
    has no diameters on it — so the sign stays out of `text`, which is the
    draughtsman's own, and arrives beside it in `notation`, which is this
    module's reading of the line work he drew it as.
    """
    printed = [entry.text for entry in _printed(sheet)]

    assert "35" in printed and "\u00d835" not in printed
    assert "\u00d8" in {entry.notation for entry in _printed(sheet)}
    assert any("\u00d8" in note for note in sheet.notes), \
        "the missing diameter sign is not mentioned anywhere the model would see it"


@needs_pymupdf
def test_every_callout_is_legible_in_some_crop(sheet):
    """
    The invariant the whole module answers to. A callout in no crop is a callout
    at the old resolution, which is the failure this replaces arriving in a new
    place — so coverage is enforced rather than hoped for, and a region the
    flood fill missed gets a crop of its own.
    """
    unshown = [entry.text for entry in _printed(sheet) if not entry.seen_in]

    assert unshown == [], "printed on the sheet and shown nowhere: %s" % unshown


@needs_pymupdf
def test_a_view_is_called_what_the_sheet_calls_it(sheet):
    """
    `seen_in` says `SECTION A-A` and so does the user at the Plan Gate. A crop
    called `the middle right of the sheet` when the sheet prints a name for it
    is a translation somebody has to do twice.
    """
    named = [crop.name for crop in sheet.crops]

    assert any(name.startswith("SECTION A-A") for name in named), named
    assert any(name.startswith("SECTION B-B") for name in named), named


@needs_pymupdf
def test_a_note_that_mentions_a_view_does_not_name_the_region_it_sits_in(sheet_2023):
    """
    `SEE VIEW B FOR CLARITY OF CUTOUT` is printed at (83, 210) on the 2023
    sheet, in the middle of the front elevation, and it points at a view over
    500 pt away on the right-hand edge. Read as a label it named every crop of
    the main drawing `VIEW B`, and a dimension taken off the bottom view was
    then recorded as though it had been read in a view the sheet does not have.

    A label is set on a line of its own — `VIEW B`, two words and nothing else.
    This note is six words on three lines with `SEE` in front of the first of
    them, and the word in front is the whole difference.
    """
    named = _shown_in(sheet_2023, (100.5, 210.5))

    assert named, "the note is printed on the sheet and arrives in no crop"
    assert not any(name.startswith("VIEW B") for name in named), named


@needs_pymupdf
def test_a_label_below_its_view_names_the_view_and_not_the_block_it_lands_in(sheet_2023):
    """
    `SECTION A-A` is printed at (573.8, 520.3) on the 2023 sheet: below the
    section it labels, whose hatching runs to y=523, and inside the title
    block, which starts at y=512. Given to whichever region encloses it, the
    label named the title block — a crop of `MODEL MANIA 2023`, `Phase 1` and
    `SCALE: 1:1` — while the section arrived as `the lower right of the sheet`.

    On an orthographic sheet the name of a view is the only carrier of
    direction, so a crop called `SECTION A-A` that is not the section is worse
    than a crop with no name at all.
    """
    called_the_section = [crop for crop in sheet_2023.crops
                          if crop.name.startswith("SECTION A-A")]

    assert len(called_the_section) == 1, [crop.name for crop in sheet_2023.crops]
    section = called_the_section[0]
    assert section.rect[0] < 600 < section.rect[2], section.rect
    assert section.rect[1] < 460 < section.rect[3], \
        "the crop called SECTION A-A does not show the section's own hatching"
    assert "SECTION A-A" not in _shown_in(sheet_2023, (600.3, 591.2)), \
        "the scale in the title block is being shown as part of the section"


@needs_pymupdf
def test_no_two_crops_arrive_under_the_same_name(sheet_2023):
    """
    A crop's name is what `seen_in` cites and what the user is asked about at
    the Plan Gate, so two crops with one name is a citation that names two
    pictures and settles nothing — the same defect as a crop named after the
    wrong view, arriving by the other door.

    Measured on the 2023 sheet: the bottom view lands at (20, 384)-(524, 600)
    and the notes beside it at (396, 540)-(616, 608), and by thirds of the page
    both of them are the lower centre.
    """
    named = [crop.name for crop in sheet_2023.crops]

    assert len(set(named)) == len(named), sorted(named)


@needs_pymupdf
def test_two_views_a_gutter_of_paper_apart_are_two_crops(sheet_2023):
    """
    The front elevation's last dimension line is at y=376 on the 2023 sheet and
    the bottom view's first mark is at y=388. Twelve points of blank paper, and
    the flood fill's reach is twelve points, so the two views chained into one
    region — helped by the bottom view's 14 and 7, whose extension lines run
    from x=194 out to x=31, straight under the elevation above them — and the
    region was then cut on a bare 2x2 grid. One crop held the lower half of the
    elevation's 65.5 and 108 lying on the left half of the bottom view.

    On an orthographic sheet the view a number is printed in is the only thing
    that says which face it is measured from, so a crop straddling two views is
    a crop whose dimensions have no direction. `108` at (236.8, 363.3) is the
    elevation's overall; `14` at (31.4, 461.7) is the fork slot's width in the
    bottom view. No crop may show both.
    """
    the_elevation = {name.split(", part")[0]
                     for name in _shown_in(sheet_2023, (236.8, 363.3))}
    the_bottom_view = {name.split(", part")[0]
                       for name in _shown_in(sheet_2023, (31.4, 461.7))}

    assert the_elevation and the_bottom_view, "one of the two is shown nowhere"
    assert not the_elevation & the_bottom_view, \
        "one crop shows the elevation's 108 and the bottom view's 14: %s" % (
            the_elevation & the_bottom_view,)


@needs_pymupdf
def test_a_view_whose_body_is_not_in_the_line_work_still_arrives_as_one_view(sheet_2023):
    """
    The top view's body is a picture placed at (79, 27)-(410, 137): on these
    SolidWorks sheets a shaded view is exported as a raster and its outline is
    not in the file's vector content at all. The flood fill therefore has the
    centrelines and the `HEX 11 6` callout to go on and nothing else, and it
    returned the view as two fragments that do not touch — (100, 48)-(168, 116),
    sixty-eight points square, and (316, 36)-(508, 132) — each of them a crop
    of part of a view and neither of them the view.

    A picture is one view, so the fragments that lie in one belong to one crop.
    The patch asked about here is well inside the picture and clear of the
    elevation's own region, which begins at y=136.
    """
    the_top_view = _overlapping(sheet_2023, (100, 40, 400, 130))

    assert len(the_top_view) == 1, \
        "the top view arrives as %d views: %s" % (len(the_top_view), the_top_view)


@needs_pymupdf
def test_a_leader_is_followed_to_the_feature_its_arrowhead_touches(sheet):
    """
    The hardest thing the description asks for, done by arithmetic.

    `(R30)` is printed at the far left of the middle view, its leader starting
    on the left bore's own axis and its arrowhead landing on the wall of the
    central pocket — two different features at two ends of one line, which is
    exactly the case the defect named above warns about and exactly the one that is hopeless
    to judge on a 14 px hairline. The traced end has to be out at the pocket,
    not back at the text.
    """
    traced = {entry["callout"]: entry["its_leader_reaches"]
              for entry in sheet.leaders}

    assert "(R30)" in traced, "the radius leader was not followed at all"
    reaches_x = traced["(R30)"][0]
    assert reaches_x > 150, \
        "the leader stopped at %s, back among the text it started from" % (
            traced["(R30)"],)


@needs_pymupdf
def test_reading_the_same_sheet_twice_renders_it_once(sheet):
    """
    Design is called several times against one attachment — the first pass, a
    question put back to the sheet, an answer written in. Re-rendering the same
    drawing for each of them is the same pictures at a cost.
    """
    assert sheet_reader.read(SHEET, into=None) is sheet_reader.read(SHEET, into=None)


def test_a_file_that_cannot_be_read_here_asks_to_be_sent_the_old_way(tmp_path):
    """
    None means "send it exactly as before", and it is the difference between a
    dependency this addon wants and a dependency it cannot run without.
    """
    neither = tmp_path / "notes.txt"
    neither.write_text("not a drawing")

    assert sheet_reader.read(str(neither)) is None
    assert sheet_reader.read(str(tmp_path / "nothing-here.pdf")) is None


def test_a_raster_exported_on_a_black_background_is_still_cut_up(tmp_path):
    """
    `mm2025-phase1.png` is a screenshot on black. Read as "dark means ink" the
    background is the drawing, every view is connected to every other one, and
    the sheet comes back as a single crop — which is the sheet as it already
    was, at the resolution that was already failing. The paper is measured off
    the edge of the picture instead of assumed.
    """
    pytest.importorskip("PIL")
    _skip_unless_present(RASTER)

    sheet = sheet_reader.read(RASTER, into=str(tmp_path))

    assert len(sheet.crops) > 3, \
        "the background was taken for line work and the sheet stayed whole"


def test_a_raster_view_keeps_the_dimensions_that_belong_to_it(tmp_path):
    """
    The frame is struck out so the views come apart, and the long dimension
    lines go with it — so a region cut to its own ink arrives without the
    numbers that are the only reason to look at it. There is no text layer on a
    raster to notice that, so the margin stands in for the check, and it has to
    be wide enough to be worth having.

    SECTION A-A on this sheet stands about 6% of the sheet clear of its own
    dimensions on every side.
    """
    pytest.importorskip("PIL")
    _skip_unless_present(RASTER)

    sheet = sheet_reader.read(RASTER, into=str(tmp_path))
    widest = max(sheet.crops, key=lambda crop: crop.rect[2] - crop.rect[0])

    from PIL import Image
    width = Image.open(RASTER).width
    assert (widest.rect[2] - widest.rect[0]) > 0.2 * width, \
        "every crop is tight around its own ink, so the dimensions were cut off"


def test_a_raster_is_still_cut_up_and_still_says_it_has_no_text_layer(tmp_path):
    """
    A photo or a PNG export carries no strings, so nothing can be extracted and
    the sweep stays the model's own. The resolution is still worth buying, so
    the crops are still made, and the missing list is stated rather than left to
    be inferred from an empty one.
    """
    pytest.importorskip("PIL")
    _skip_unless_present(RASTER)

    sheet = sheet_reader.read(RASTER, into=str(tmp_path))

    assert sheet is not None and sheet.crops, "a raster sheet produced no crops"
    assert _printed(sheet) == []
    assert any("no text layer" in note for note in sheet.notes)


CADGENBENCH_SHEETS = os.path.join(os.path.dirname(modelmania_harness.MODELMANIA_DIR), "cadgenbench", "sheets")

VIEWS_ON = {
    "101.png": {"isometric": (2432, 486), "SECTION A-A": (1510, 653), "the view under SECTION A-A": (1510, 1050),
                "the left view": (614, 1472), "the view under the left view": (627, 1830)},
    "104.png": {"top left view": (512, 486), "middle view": (1254, 768), "SECTION A-A": (1894, 461),
                "isometric": (2586, 410), "SECTION C-C": (2598, 883), "DETAIL B": (1907, 1101),
                "front view": (666, 1536)},
}


@pytest.mark.parametrize("name", sorted(VIEWS_ON))
def test_every_view_of_a_raster_sheet_is_in_a_crop_of_its_own_part_of_the_sheet(tmp_path, name):
    """
    A view drawn in thin grey lines averages to paper over a grid cell, so the
    104 sheet reached no crop with any of its views; and regions joined by
    touching bounding boxes chain the 101 sheet into one crop of the whole of
    it. The centres are placed by hand on the CADGenBench sheets.
    """
    pytest.importorskip("PIL")
    path = os.path.join(CADGENBENCH_SHEETS, name)
    _skip_unless_present(path)
    from PIL import Image

    width, height = Image.open(path).size
    sheet = sheet_reader.read(path, into=str(tmp_path))

    def holds(crop, point):
        x0, y0, x1, y1 = crop.rect
        return x0 <= point[0] <= x1 and y0 <= point[1] <= y1

    for view, centre in VIEWS_ON[name].items():
        assert any(holds(crop, centre)
                   and (crop.rect[2] - crop.rect[0]) * (crop.rect[3] - crop.rect[1]) < 0.5 * width * height
                   for crop in sheet.crops), view


@pytest.mark.parametrize("name", sorted(VIEWS_ON))
def test_the_registration_marks_in_a_raster_sheets_corners_are_not_crops(tmp_path, name):
    """A frame's corner mark is a few cells of ink the cutting finds on every sheet, and a picture of it says nothing."""
    pytest.importorskip("PIL")
    path = os.path.join(CADGENBENCH_SHEETS, name)
    _skip_unless_present(path)
    from PIL import Image

    width, height = Image.open(path).size
    sheet = sheet_reader.read(path, into=str(tmp_path))

    for crop in sheet.crops:
        x0, y0, x1, y1 = crop.rect
        in_a_corner = (x1 < 0.15 * width or x0 > 0.85 * width) and (y1 < 0.15 * height or y0 > 0.85 * height)
        assert not in_a_corner, crop.name


@needs_pymupdf
def test_a_second_page_is_read_rather_than_mentioned(sheet_2023):
    """
    The 2023 file is two pages and the reader used to take the first and say so
    in a note. Page 2 prints `6.6 X 8.0 THRU ALL`, and the recorded run
    (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`) asked the user how
    wide that slot is, was answered by nobody, and assumed 8 mm — the number
    printed on the page nobody read.
    """
    printed = [entry.text for entry in _printed(sheet_2023)]

    assert "8.0" in printed, "page 2's slot width is still not in the text layer"
    assert not any("only the first" in note for note in sheet_2023.notes)
    assert any(crop.page == 2 for crop in sheet_2023.crops),         "page 2 arrives with no crop of its own"
    assert all(entry.seen_in for entry in _printed(sheet_2023)),         "a callout on one of the pages is shown in no crop"


@needs_pymupdf
def test_the_scale_the_sheet_prints_is_what_the_millimetres_are_in(sheet):
    """
    A page is in points at 72 to the inch, so a drawing plotted 1:1 puts
    0.3528 mm of the part in each of them — and a sheet plotted 1:2 puts twice
    that. The ratio in the title block is the only thing that says which, so it
    is read rather than assumed, and said out loud in the notes.
    """
    assert any("the scale the sheet prints" in note for note in sheet.notes),         sheet.notes


@needs_pymupdf
def test_a_second_page_is_said_to_be_a_second_sheet_and_maybe_a_revision(sheet_2023):
    """
    Reading every page brought a hazard with it. Page 1 of this file has the
    part 25 mm deep and page 2 has it 30: a run took the 30 off page 2 and the
    108 off page 1 and described a part neither page shows
    (`logs/sessions/172369d7389a4a4f9c3c2ae8b5aded91.jsonl`). The pages are
    labelled everywhere; what was missing was what to do about it.
    """
    said = [note for note in sheet_2023.notes if "2 pages" in note]

    assert said, sheet_2023.notes
    assert "conflicts" in said[0], "nothing says what a disagreement between pages is"
    assert "PHASE" in said[0].upper(), \
        "the sheet says it is a revision in its own words and the note does not quote it"


@needs_pymupdf
def test_the_number_that_named_the_defect_is_measured_by_its_own_dimension_line(
        sheet_2023):
    """
    The defect named above, as arithmetic. The sheet prints 108 and the run recorded 100.

    Nothing here reads the digits — the string comes out of the text layer, and
    the millimetres come out of the span its own dimension line is drawn
    between. They agree to a hundredth, so a description that writes 100 is
    contradicted by 8 mm of evidence the sheet supplied about itself.
    """
    entries = [entry for entry in _printed(sheet_2023) if entry.text == "108"]
    assert entries, "the sheet prints 108"
    assert entries[0].measures == pytest.approx(108.0, abs=0.1)
    assert entries[0].off_by == pytest.approx(0.0, abs=0.1)


@needs_pymupdf
def test_a_dimension_line_broken_by_its_own_text_is_still_one_measurement(
        sheet_2023):
    """
    The span bridges the gap the text sits in — the whole reason `_chained`
    cannot do this job. `_chained` breaks a run at a junction because a corner
    is not a curve continuing; a dimension line is drawn as two pieces either
    side of the number, and the two pieces are one measurement.
    """
    measured = [entry for entry in _printed(sheet_2023)
                if entry.measures is not None]
    assert len(measured) >= 8
    assert all(abs(entry.off_by) < 0.6 for entry in measured)


@needs_pymupdf
def test_a_string_no_dimension_line_measures_says_nothing_rather_than_a_number(
        sheet):
    """
    Silence, not a guess. A drawing number, a note, a view label — nothing on
    the sheet measures them, and an entry that invented a millimetre for them
    would be worse than one that says nothing.
    """
    unmeasured = [entry for entry in _printed(sheet) if entry.measures is None]
    assert unmeasured
    assert all(entry.off_by is None for entry in unmeasured)


@needs_pymupdf
def test_the_scale_is_confirmed_by_the_lines_and_not_taken_on_trust(sheet):
    """
    The title block prints 1:2 and the module used to believe it. The sheet's
    own dimension lines vote on it independently — printed value over span —
    and on this sheet the vote agrees to four figures. Agreement is worth
    saying; the point is that disagreement would now be sayable at all.
    """
    said = " ".join(sheet.notes)
    assert "its own dimension lines" in said
    assert "0.7055" in said or "0.7054" in said


@needs_pymupdf
def test_the_diameter_sign_is_recovered_from_the_line_work_it_is_drawn_as(sheet):
    """
    The module already says the sign is missing from the text layer. Saying so
    is not the same as supplying it: `35 THRU` and `Ø35 THRU` are a bore and a
    length, and the difference was left for the model to see in a picture.

    It is not a picture. On `mm25-p1-1.pdf` the sign is a path of 47 flattened
    segments, 6.3 × 7.7 pt, whose right edge sits 2 pt from the numeral's left
    edge, inside the numeral's own line of text. That is arithmetic, and it is
    the same arithmetic on the 40 and on the 35.
    """
    signed = {entry.text for entry in _printed(sheet) if entry.notation == "\u00d8"}
    assert "35 THRU" in signed or "35" in signed
    assert any(entry.text.startswith("40") for entry in _printed(sheet)
               if entry.notation == "\u00d8")


@needs_pymupdf
def test_a_radius_is_not_mistaken_for_a_diameter(sheet):
    """
    `(R30)` carries its own letter in the text layer and no sign is drawn
    against it. A rule that marked it a diameter would turn a 30 mm radius into
    a 15 mm one, which is worse than saying nothing.
    """
    assert not [entry for entry in _printed(sheet)
                if entry.text == "(R30)" and entry.notation]


DIAMETER_COUNTS = {
    "mm2023-phase1.pdf": 7,
    "mm2023-phase2.pdf": 3,
    "mm2024-phase1.pdf": 5,
    "mm2024-phase2.pdf": 4,
    "mm2025-phase1.pdf": 2,
    "mm2025-phase2.pdf": 2,
}


@needs_pymupdf
def test_every_diameter_on_the_six_modelmania_phase_sheets_is_extracted_as_one():
    """
    `DIAMETER_COUNTS` is a hand count against the rendered page of each sheet,
    not a sample: every `\u00d8` the draughtsman drew, and nothing a radius or
    a reference dimension printed. `R2 TYP`, `(R30)`, `R12.5`, `R8`, `R20` and
    every `°` angle on these six sheets carry no sign at all and must stay that
    way — a false positive here is as wrong as a miss.
    """
    _skip_unless_present(*modelmania_harness.PHASES.values())
    for name, count in DIAMETER_COUNTS.items():
        read = sheet_reader.read(str(modelmania_harness.PHASES[name]))
        diameters = [entry for entry in _printed(read) if entry.notation == "\u00d8"]
        assert len(diameters) == count, (name, [entry.text for entry in diameters])
        assert not [entry for entry in _printed(read)
                    if entry.notation and entry.text.upper().lstrip("(").startswith("R")]


@needs_pymupdf
def test_a_crop_is_never_resized_at_the_far_end_for_costing_too_many_patches(sheet):
    """
    The cap that matters is not the long edge, and getting that wrong gave away
    the resolution this module exists to protect.

    Anthropic tokenises an image in 28 x 28 patches and costs it
    `ceil(w/28) * ceil(h/28)` visual tokens, capped at 1568 on the standard
    tier — so a 1400 x 1400 crop, well inside every edge limit, costs 2500 and
    is silently downscaled to 1092 x 1092. 22% of the linear resolution, taken
    back after the module went to the trouble of tiling for it.
    """
    import math as _math

    from PIL import Image

    def costs(path):
        width, height = Image.open(path).size
        return _math.ceil(width / sheet_reader.A_PATCH) * _math.ceil(
            height / sheet_reader.A_PATCH)

    for crop in sheet.crops:
        assert costs(crop.path) <= sheet_reader.THE_BUDGET, (
            "%s costs %d visual tokens and will be resized"
            % (crop.name, costs(crop.path)))

    for overview in sheet.overviews:
        assert costs(overview) <= sheet_reader.THE_BUDGET, costs(overview)


@needs_pymupdf
def test_a_raster_crop_is_not_enlarged_past_what_the_file_holds(tmp_path):
    """
    An upscale adds pixels and no information, and every one of them is billed.
    The raster path used to enlarge any crop smaller than half the cap on the
    grounds that bigger is more legible; it is not, because the ink it enlarges
    is the ink the file has.
    """
    from PIL import Image

    _skip_unless_present(RASTER)
    sheet = sheet_reader.read(RASTER, into=str(tmp_path))
    original = Image.open(RASTER).size
    for crop in sheet.crops:
        width, height = Image.open(crop.path).size
        x0, y0, x1, y1 = crop.rect
        assert width <= int(x1 - x0) + 1 and height <= int(y1 - y0) + 1, crop.name
    assert original


@needs_pymupdf
def test_a_view_too_big_for_one_crop_is_still_shown_whole_once(sheet_2023):
    """
    A view cut into three is three pictures holding a third of a projection
    each, and relating a circle in the plan to the two lines in the elevation
    that are the same hole was being done across them.
    """
    tiled = {crop.name.split(", part ")[0] for crop in sheet_2023.crops
             if ", part " in crop.name}

    assert tiled, "nothing on this sheet was tiled, so this proves nothing"
    assert {whole.name for whole in sheet_2023.wholes} == tiled
    assert all(whole.rect[2] - whole.rect[0] > 0 for whole in sheet_2023.wholes)


@needs_pymupdf
def test_a_view_that_fits_in_one_crop_is_not_sent_twice(sheet_2023):
    whole = {crop.name for crop in sheet_2023.crops if ", part " not in crop.name}

    assert whole & {one.name for one in sheet_2023.wholes} == set()


@needs_pymupdf
def test_a_picture_straddling_two_regions_is_traced_in_the_one_holding_most():
    """
    The rule is an argmax and not a threshold. A picture with 0.55 of itself in
    one region and 0.45 in another is in the first and in no other; under a
    threshold of 0.9 it was in neither, and the two crops that show it both
    reported the view as having no outline at all.
    """
    import pymupdf

    box = pymupdf.Rect(0, 0, 100, 100)
    holding = pymupdf.Rect(-50, 0, 55, 100)
    clipping = pymupdf.Rect(55, 0, 150, 100)

    assert sheet_reader._share_of_the_picture(box, holding) == pytest.approx(0.55)
    assert sheet_reader._share_of_the_picture(box, clipping) == pytest.approx(0.45)
    assert sheet_reader._the_region_holding_the_picture(
        box, [clipping, holding]) == holding


@needs_pymupdf
def test_a_picture_in_no_region_worth_speaking_of_is_traced_nowhere():
    """
    The threshold survives the argmax, doing the one job it can do: the
    isometric in the corner of every one of these sheets reaches over the title
    block and has at most 0.36 of itself in any region there, and tracing it
    would hand that region an outline of a view drawn somewhere else.
    """
    import pymupdf

    box = pymupdf.Rect(0, 0, 100, 100)
    clipping = pymupdf.Rect(-50, 0, 36, 100)

    assert sheet_reader._the_region_holding_the_picture(box, [clipping]) is None


@needs_pymupdf
def test_what_a_picture_has_inside_a_region_is_measured_off_the_four_sheets():
    """
    The numbers `IN_THE_REGION` stands on, read off `tests/modelmania` rather
    than stated: a view body has at least 0.92 of itself inside its own region,
    the one picture that lies inside two regions at once — the 2024 sheet's
    page 2 detail — is wholly inside one of them and 0.55 inside the other, and
    a picture a region only clips has at most 0.36 of itself in it.

    Four sheets, not five: `model-mania_-in-person-2023.pdf` is the same file
    as the copy beside it and would count everything on it twice.
    """
    import pymupdf

    _skip_unless_present(*modelmania_harness.SHEETS.values())
    inside, alongside, clipped = [], [], []
    for path in modelmania_harness.SHEETS.values():
        document = pymupdf.open(path)
        for page in document:
            words = [w for w in page.get_text("words") if str(w[4]).strip()]
            regions = sheet_reader._the_regions_of(page, words)
            for picture in page.get_image_info():
                box = pymupdf.Rect(*picture["bbox"])
                shares = sorted((sheet_reader._share_of_the_picture(box, region)
                                 for region in regions), reverse=True)
                held = [share for share in shares
                        if share > sheet_reader.IN_THE_REGION]
                if held:
                    inside.append(held[0])
                    alongside.extend(held[1:])
                else:
                    clipped.append(shares[0] if shares else 0.0)

    assert min(inside) == pytest.approx(0.92, abs=0.02), inside
    assert max(clipped) == pytest.approx(0.36, abs=0.04), clipped
    assert len(alongside) == 1, alongside
    assert alongside[0] == pytest.approx(0.55, abs=0.02)


@needs_pymupdf
def test_a_picture_no_region_reaches_becomes_a_shape_only_crop():
    """
    The isometric in the corner of the 2024 sheet is a placed picture at most
    0.21 covered by the regions the ink pipeline finds — the same "no region
    worth speaking of" picture `_the_region_holding_the_picture` already
    refuses to trace. `_orphan_pictures` is what turns that refusal into a
    crop instead of into nothing: measured on page 1, two placed pictures are
    orphaned this way, at (521.68, 0.0) and (523.23, 154.55).
    """
    import pymupdf

    path = str(modelmania_harness.MODELMANIA_DIR / "model-mania-2024.pdf")
    _skip_unless_present(path)
    page = pymupdf.open(path)[0]
    words = [w for w in page.get_text("words") if str(w[4]).strip()]
    regions = sheet_reader._the_regions_of(page, words)

    orphans = sheet_reader._orphan_pictures(regions, page.get_image_info(),
                                            page.rect)

    corners = {(round(box.x0, 1), round(box.y0, 1)) for box in orphans}
    assert (521.7, 0.0) in corners
    assert (523.2, 154.5) in corners


@needs_pymupdf
def test_an_off_page_bbox_is_never_orphaned_into_a_crop():
    """
    Page 2 of the 2023 sheet places a picture at x from -34.8 to 0 — a sliver
    `page_rect` does not contain at all, not a view with a corner nobody drew.
    `_orphan_pictures` has to reject it on its own terms rather than lean on
    the region pipeline happening not to reach it either.
    """
    import pymupdf

    page_rect = pymupdf.Rect(0, 0, 792, 612)
    off_page = {"bbox": (-34.8, 486.1, 0.0, 612.0)}
    empty = {"bbox": (100.0, 100.0, 100.0, 250.0)}

    assert sheet_reader._orphan_pictures([], [off_page], page_rect) == []
    assert sheet_reader._orphan_pictures([], [empty], page_rect) == []


@needs_pymupdf
def test_a_shape_only_crop_is_one_render_at_a_fraction_of_the_usual_cost(
        sheet_2023):
    """
    Nothing on this picture is read, so it is not rendered at `_dpi_for`'s
    legibility budget: measured on the 2023 sheet, its two shape-only crops
    land at 112-126 DPI against the sheet's own 150-600 DPI range, each under
    500 visual tokens where an ordinary crop is budgeted up to `THE_BUDGET`
    (1568).
    """
    from PIL import Image

    shape_crops = [crop for crop in sheet_2023.crops if crop.shape_only]
    assert shape_crops

    for crop in shape_crops:
        assert crop.dpi < 150
        with Image.open(crop.path) as picture:
            width, height = picture.size
        visual_tokens = width * height / 750
        assert visual_tokens < 500, (crop.name, visual_tokens)


@needs_pymupdf
def test_a_dimension_drawn_with_its_arrows_outside_is_measured_too(sheet_2023):
    """
    A dimension too small to seat its arrows between its extension lines is
    drawn inside out — arrows outside, the line as two stubs pointing away, the
    number set off one of them — and the span the two stubs bridge into is the
    whole apparatus rather than the distance. The 2023 clamp's cutout carries
    one: `7`, from the depth centre line out to the near cheek of a 14 wide
    cutout, printed at (48.3, 424.3) and measuring nothing at all until the gap
    between the stubs was measured instead of the span across them. Design read
    the datum it was left to guess at as the front face, described a 16 mm
    recess the sheet does not draw, and the run ended blocked on the fillet that
    would not fit the land it left
    (`logs/sessions/069d21dbac10416392533fe7c06b62d4.jsonl`).
    """
    seven = [entry for entry in _printed(sheet_2023)
             if entry.text == "7" and entry.at == (48.3, 424.3)]

    assert len(seven) == 1, "the 7 of the cutout is not on the sheet"
    assert (seven[0].measures, seven[0].off_by) == (7.02, 0.02)


@needs_pymupdf
def test_the_numbers_the_sheet_measures_for_itself(sheet_2023):
    """
    Fourteen of the 2023 clamp's callouts are printed against a line the module
    can prove measures them, and every one of the fourteen lands within 0.03 mm
    of what is printed. The band is the evidence: a false pairing on these
    sheets lands 7 to 100 mm out.
    """
    measured = [entry for entry in _printed(sheet_2023) if entry.measures is not None]

    assert len(measured) == 14
    assert max(abs(entry.off_by) for entry in measured) <= 0.03


@needs_pymupdf
def test_read_registers_its_temp_dir_for_atexit_cleanup(monkeypatch, tmp_path):
    """
    `read()` called with no `into=` makes its own `mkdtemp` directory, and
    nothing else in the repo ever removes an `fcc-sheet-*` directory
    (`grep -rn rmtree core ui`). The cleanup has to be registered
    at creation time, so this patches `atexit.register` to capture the
    callback instead of letting it run at interpreter exit. Read a copy of
    the sheet at a path no other test has used, so `_ALREADY_READ` cannot
    hand back a sheet another test already cached without the patch active.
    """
    import shutil as shutil_module

    _skip_unless_present(SHEET)
    copy = tmp_path / "fcc-263.pdf"
    shutil_module.copyfile(SHEET, copy)

    registered = []
    monkeypatch.setattr(sheet_reader.atexit, "register",
                         lambda callback: registered.append(callback))

    sheet = sheet_reader.read(str(copy))

    assert registered, "_read() did not register a cleanup callback"
    directory = os.path.dirname(sheet.overview)
    assert os.path.isdir(directory)

    registered[0]()

    assert not os.path.isdir(directory)


@needs_pymupdf
def test_a_read_failure_is_logged_once_and_then_cached_quiet(monkeypatch, caplog, tmp_path):
    """
    A failing `_read` is caught so one bad sheet does not stop the run (the
    docstring above), but until now nothing said the failure happened
    (`grep -n "^import\\|^from" core/sheet_reader.py` had no `logging`).
    The second call with the same key must hit `_ALREADY_READ` and
    not log again. Reads a copy at a path no other test has used, so the
    cache another test filled cannot hand back a non-exception result.
    """
    import shutil as shutil_module

    _skip_unless_present(SHEET)
    copy = tmp_path / "fcc-265.pdf"
    shutil_module.copyfile(SHEET, copy)

    def boom(path, into, legible_px, max_edge):
        raise ValueError("staged failure")

    monkeypatch.setattr(sheet_reader, "_read", boom)

    with caplog.at_level("WARNING", logger="core.sheets.sheet_reader"):
        first = sheet_reader.read(str(copy), into=None)
        second = sheet_reader.read(str(copy), into=None)

    assert first is None
    assert second is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert str(copy) in warnings[0].message


@needs_pymupdf
def test_two_sessions_reading_the_same_sheet_at_once_render_it_once(monkeypatch, tmp_path):
    """
    Two `QThread` sessions can call `read()` on the same
    path at the same moment; `_ALREADY_READ` was a plain dict with no lock, so
    both could pass the check before either had written the cache and both
    would render. `_read` is patched to block until both threads are running,
    proving the second thread waits on `_READ_LOCK` for the first's result
    instead of rendering its own.
    """
    import shutil as shutil_module

    _skip_unless_present(SHEET)
    copy = tmp_path / "fcc-267.pdf"
    shutil_module.copyfile(SHEET, copy)

    calls = []
    started = threading.Event()
    release = threading.Event()

    def slow_read(path, into, legible_px, max_edge):
        calls.append(path)
        started.set()
        release.wait(timeout=2)
        return None

    monkeypatch.setattr(sheet_reader, "_read", slow_read)

    results = []

    def call():
        results.append(sheet_reader.read(str(copy)))

    first = threading.Thread(target=call)
    first.start()
    assert started.wait(timeout=2), "the first thread never reached _read"

    second = threading.Thread(target=call)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(calls) == 1
    assert results == [None, None]


def test_evicting_one_stale_key_does_not_wipe_the_other_cached_sheet(monkeypatch, tmp_path):
    """
    `read()` used to call `_read_cached.cache_clear()` when one
    cached sheet's temp images had been swept, wiping the whole 128-entry
    cache instead of the one stale key. Two sheets are cached, one goes
    stale, and re-reading it must not force the other, still-good one to
    render again.
    """
    one = tmp_path / "one.pdf"
    two = tmp_path / "two.pdf"
    one.write_bytes(b"one")
    two.write_bytes(b"two")

    calls = []

    def fake_read(path, into, legible_px, max_edge):
        calls.append(path)
        return sheet_reader.Sheet(source=path, overview=path)

    monkeypatch.setattr(sheet_reader, "_read", fake_read)

    good = sheet_reader.read(str(one))
    sheet_reader.read(str(two))
    assert calls == [str(one), str(two)]

    monkeypatch.setattr(sheet_reader, "_still_there",
                        lambda sheet: sheet.overview != str(two))

    sheet_reader.read(str(two))
    assert calls == [str(one), str(two), str(two)]

    assert sheet_reader.read(str(one)) == good
    assert calls == [str(one), str(two), str(two)]


def test_the_cache_never_grows_past_128_entries():
    """
    `_ALREADY_READ` had no upper bound and lived for the process's
    whole life. `_read_cached` bounds `_READ_CACHE` to 128 entries itself
    (`popitem(last=False)` on overflow), so pushing 200 unique keys through
    it must still leave at most 128 held.
    """
    sheet_reader._READ_CACHE.clear()
    for i in range(200):
        path = "/tmp/fcc-267-%d.pdf" % i
        key = (path, float(i), i, sheet_reader.LEGIBLE_PX, sheet_reader.MAX_EDGE, None)
        sheet_reader._read_cached(key, path, None, sheet_reader.LEGIBLE_PX,
                                  sheet_reader.MAX_EDGE)

    assert len(sheet_reader._READ_CACHE) <= 128


@needs_pymupdf
def test_a_page_fills_the_accumulator_by_field_name_not_position(tmp_path):
    """
    `_a_page` used to take nine same-typed `list` accumulators as
    positional arguments — two adjacent ones swapped at the call site compiles
    and passes static analysis without a warning. Calling it directly against
    a fresh `_PageAccumulator` and reading each field back by name is the test
    that a positional swap could not have passed.
    """
    _skip_unless_present(SHEET)
    document = sheet_reader.pymupdf.open(SHEET)
    page_state = sheet_reader._PageAccumulator()

    sheet_reader._a_page(document[0], 1, len(document), str(tmp_path),
                        sheet_reader.LEGIBLE_PX, sheet_reader.MAX_EDGE, page_state)

    assert page_state.overviews and os.path.isfile(page_state.overviews[0])
    assert page_state.crops
    assert page_state.printed
    assert page_state.callouts
    assert page_state.leaders

