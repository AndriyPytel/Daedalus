"""
Level 1 — the sheet as evidence, before any provider sees it.

No network and no FreeCAD: everything here is measured off
`tests/modelmania/mm25-p1-1.pdf`, the sheet from the recorded run that named
the defect (ADR-0019 — an overall read as 100 where the sheet prints 108, and a
depth off SECTION B-B that was gone two turns later).

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
import time

import numpy
import pytest

from core import sheet_reader
from tests import modelmania_harness

SHEET = os.path.join(os.path.dirname(__file__), "modelmania", "mm25-p1-1.pdf")
RASTER = os.path.join(os.path.dirname(__file__), "modelmania", "mm2025-phase1.png")
SHEET_2023 = os.path.join(os.path.dirname(__file__), "modelmania",
                          "model-mania_-in-person-2023 copy.pdf")

needs_pymupdf = pytest.mark.skipif(not sheet_reader.available(),
                                   reason="PyMuPDF is not installed")


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


def _shown_in(sheet, at):
    """Which crop the callout printed at these coordinates arrives in."""
    return [entry.seen_in for entry in sheet.printed if entry.at == at]


def _costs(path):
    """What a rendered picture costs the provider, by this module's own formula."""
    from PIL import Image

    width, height = Image.open(path).size
    return sheet_reader._patches(sheet_reader.pymupdf.Rect(0, 0, width, height), 72)


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
    printed = [entry.text for entry in sheet.printed]

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
    printed = [entry.text for entry in sheet.printed]

    assert "35" in printed and "\u00d835" not in printed
    assert "\u00d8" in {entry.notation for entry in sheet.printed}
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
    unshown = [entry.text for entry in sheet.printed if not entry.seen_in]

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
    exactly the case ADR-0019 warns about and exactly the one that is hopeless
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
def test_a_description_that_lost_a_dimension_is_caught_by_the_number(sheet):
    """
    The check `printed_on_the_sheet` was written for, finally able to fail.
    Asked of the model, the sweep is the model's own account of what it noticed
    counted against itself, and the callout nobody saw is missing from both
    sides of that count.
    """
    forgot_the_bore_and_the_radius = {
        "envelope": {"length": {"value": "150 mm", "flag": "read"},
                     "depth front to rear": {"value": "100 mm", "flag": "read"}},
        "features": [{"id": "bore", "kind": "35 bore",
                      "position": {"the top face": {"value": "50 mm", "flag": "read"}}},
                     {"id": "pocket", "kind": "pocket",
                      "position": {"the left end face": {"value": "20 mm", "flag": "read"},
                                   "the top face": {"value": "10 mm", "flag": "read"}}}],
    }

    missed = sheet_reader.uncited(sheet, forgot_the_bore_and_the_radius)

    assert "40" in missed, "the Ø40 bore was dropped and nothing noticed"
    assert "(R30)" in missed, "the R30 was dropped and nothing noticed"


@needs_pymupdf
def test_the_title_block_is_not_something_the_part_has_to_account_for(sheet):
    """
    `1:2` is a scale, `01-01-25` is a date and `2025` is the year on a
    competition sheet. All three are printed on it and none of them is a value
    of the part, so a description that ignores them is not incomplete — and a
    check that says otherwise would send Design back to the sheet every run to
    account for the date it was drawn.
    """
    everything = {"note": "150 100 50 40 35 30 20 10 2"}

    assert sheet_reader.uncited(sheet, everything) == []


@needs_pymupdf
def test_a_description_that_used_everything_is_left_alone(sheet):
    """A check that fires on a correct description is a check nobody can keep."""
    used = {"note": " ".join(entry.text for entry in sheet_reader.dimensions(sheet))}

    assert sheet_reader.uncited(sheet, used) == []


@needs_pymupdf
def test_the_sheet_is_shown_whole_before_it_is_shown_close_up(sheet):
    """
    The overview is not decoration: without it the close-ups are a pile of
    unplaced fragments, and `seen_in` has nothing to be measured against. It
    goes first because it is what the rest are read against.

    A tiled view's own whole picture is not among the pictures shown — the
    overview already carries it — so what is shown is one overview plus one
    picture per crop, not per crop and whole.
    """
    told = sheet_reader.shown(sheet)
    pictures = [piece["show"] for piece in told if "show" in piece]

    assert pictures[0] == sheet.overview
    assert len(pictures) == len(sheet.crops) + 1


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

    sheet = sheet_reader.read(RASTER, into=str(tmp_path))

    assert sheet is not None and sheet.crops, "a raster sheet produced no crops"
    assert sheet.printed == []
    assert any("no text layer" in note for note in sheet.notes)


@needs_pymupdf
def test_a_second_page_is_read_rather_than_mentioned(sheet_2023):
    """
    The 2023 file is two pages and the reader used to take the first and say so
    in a note. Page 2 prints `6.6 X 8.0 THRU ALL`, and the recorded run
    (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`) asked the user how
    wide that slot is, was answered by nobody, and assumed 8 mm — the number
    printed on the page nobody read.
    """
    printed = [entry.text for entry in sheet_2023.printed]

    assert "8.0" in printed, "page 2's slot width is still not in the text layer"
    assert not any("only the first" in note for note in sheet_2023.notes)
    assert any(crop.page == 2 for crop in sheet_2023.crops),         "page 2 arrives with no crop of its own"
    assert all(entry.seen_in for entry in sheet_2023.printed),         "a callout on one of the pages is shown in no crop"


@needs_pymupdf
def test_a_view_is_measured_in_the_millimetres_of_the_part(sheet_2023):
    """
    The defect this exists for, in the numbers it was measured in.

    The elevation's ring is centred on a centre line drawn at y=247.9 pt, and
    the `15` that gives the arm its thickness has its extension lines on the
    arm's own faces, at 276.3 and 318.8 pt. In the view's own millimetres the
    centre line is at y=48.01 and the faces at 37.99 and 23.00 — the arm's top
    face 10.0 mm *below* the ring axis and its bottom face 25.0 mm below, flush
    with the ring's lowest tangent. The recorded run asked the user for that
    height, was told the views do not answer, and assumed the arm centred on the
    axis, 7.5 mm above it: the part came out 17.5 mm wrong on a distance the
    file carries to a hundredth of a millimetre.
    """
    elevation = [view for view in sheet_2023.drawn
                 if view.view.startswith("the middle centre")][0]

    def at_height(height):
        return [entity for entity in elevation.entities
                if entity.kind == "line" and entity.source != "traced"
                and abs(entity.start[1] - height) < 0.005
                and abs(entity.end[1] - height) < 0.005]

    assert at_height(48.01), "the ring's own centre line is not in the line work"
    assert at_height(37.99), "the arm's top face is not in the line work"
    assert at_height(23.00), "the arm's bottom face is not in the line work"


@needs_pymupdf
def test_an_arc_is_given_its_radius_and_its_centre(sheet_2023):
    """
    A PDF carries no arcs — every curve is flattened into very short straight
    pieces — so a radius exists only once the pieces are chained and a circle is
    fitted through them. A radius without its centre places nothing, which is
    what the description says about a radius on a sheet, so both are reported.
    """
    said = [entity for view in sheet_2023.drawn for entity in view.entities
            if entity.kind == "arc" and entity.source == "vector"]

    assert said, "not one arc was recovered from the whole drawing"
    assert all(entity.radius and entity.centre for entity in said), said[:3]


@needs_pymupdf
def test_a_view_whose_body_is_a_picture_says_so_rather_than_passing_off_its_dimensions(
        sheet_2023):
    """
    A shaded view on these sheets is exported as a raster: the outline is not in
    the file's vector content at all, and what is left in the view is its
    dimension and centre lines. Handed over as "the line work of this view" that
    would be a drawing of the annotation offered as the part.
    """
    elevation = [view for view in sheet_2023.drawn
                 if view.view.startswith("the middle centre")][0]

    assert elevation.from_a_picture
    assert "not its outline" in sheet_reader._the_drawn_list(sheet_2023)


@needs_pymupdf
def test_the_scale_the_sheet_prints_is_what_the_millimetres_are_in(sheet):
    """
    A page is in points at 72 to the inch, so a drawing plotted 1:1 puts
    0.3528 mm of the part in each of them — and a sheet plotted 1:2 puts twice
    that. The ratio in the title block is the only thing that says which, so it
    is read rather than assumed, and said out loud in the notes.
    """
    assert any("the scale the sheet prints" in note for note in sheet.notes),         sheet.notes


def test_a_notes_item_number_is_not_a_callout_the_part_has_to_account_for():
    """
    `1.` and `2.` enumerate the notes on every one of these sheets, and page 2
    adds `3.` and `4.`. Counted as dimensions they are numbers no description
    can ever use, so every run ends with Design being sent back to the sheet to
    explain that a full stop is a full stop —
    `logs/sessions/6fbd31e98fb540818086611841fa9aff.jsonl` is that run, and it
    died in the explanation.
    """
    assert not sheet_reader._a_dimension("3.")
    assert not sheet_reader._a_dimension("1.")
    assert sheet_reader._a_dimension("30")
    assert sheet_reader._a_dimension("42.5")
    assert sheet_reader._a_dimension("(R30)")


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
    ADR-0019 as arithmetic. The sheet prints 108 and the run recorded 100.

    Nothing here reads the digits — the string comes out of the text layer, and
    the millimetres come out of the span its own dimension line is drawn
    between. They agree to a hundredth, so a description that writes 100 is
    contradicted by 8 mm of evidence the sheet supplied about itself.
    """
    entries = [entry for entry in sheet_2023.printed if entry.text == "108"]
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
    measured = [entry for entry in sheet_2023.printed
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
    unmeasured = [entry for entry in sheet.printed if entry.measures is None]
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
def test_a_centre_line_arrives_as_an_axis_and_not_as_one_more_line(sheet):
    """
    The dash pattern is in the file and was being thrown away. A long-dash-dot
    line is a centre line, which is to say an axis or a plane of symmetry —
    exactly the thing `design.py` asks every feature with an axis to carry, and
    exactly the thing the model was left to guess off a picture.

    Measured on `mm25-p1-1.pdf`: six dashed paths, none of which said anything
    about itself before this.
    """
    said = [entity for view in sheet.drawn for entity in view.entities
            if entity.source == "centre"]
    assert said, "the sheet draws centre lines"
    assert any(entity.kind == "line" for entity in said)


@needs_pymupdf
def test_a_centre_line_drawn_as_a_circle_is_reported_as_the_circle_it_is(
        sheet_2023):
    """
    The 2023 sheet draws one centre line as a full circle 78.4 pt across — a
    bolt circle, whose diameter is a dimension of the part that no callout on
    that view prints. Chained and fitted as ordinary line work it was 165
    anonymous fragments competing for room in the list.
    """
    said = [entity for view in sheet_2023.drawn for entity in view.entities
            if entity.source == "centre" and entity.kind == "arc"]
    assert said, "the bolt circle's centre line is drawn as a circle"


@needs_pymupdf
def test_a_centre_line_is_never_the_entity_that_gets_left_out(sheet_2023):
    """
    The list is capped at the longest `ENOUGH`, and a centre line is short. It
    is also the most informative thing in the list, so it is not what the cap
    takes off.
    """
    for view in sheet_2023.drawn:
        centres = [at for at, entity in enumerate(view.entities)
                   if entity.source == "centre"]
        assert centres == list(range(len(centres))), (
            "centre lines come first, ahead of the cap")


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
    signed = {entry.text for entry in sheet.printed if entry.notation == "\u00d8"}
    assert "35 THRU" in signed or "35" in signed
    assert any(entry.text.startswith("40") for entry in sheet.printed
               if entry.notation == "\u00d8")


@needs_pymupdf
def test_a_radius_is_not_mistaken_for_a_diameter(sheet):
    """
    `(R30)` carries its own letter in the text layer and no sign is drawn
    against it. A rule that marked it a diameter would turn a 30 mm radius into
    a 15 mm one, which is worse than saying nothing.
    """
    assert not [entry for entry in sheet.printed
                if entry.text == "(R30)" and entry.notation]


@needs_pymupdf
def test_the_sign_is_shown_against_the_number_it_was_drawn_against(sheet):
    """What the model is told has the sign in front of the number, as the sheet has."""
    said = sheet_reader._the_printed_list(sheet)
    assert "\u00d835" in said


@needs_pymupdf
def test_the_printed_list_carries_the_callouts_and_not_the_title_block(sheet_2023):
    """
    The split `dimensions` already makes, made where the sheet is shown too. Of
    the 230 strings the 2023 clamp prints, 121 stand inside the title block and
    are a drawing number, a date, a scale and the small print. The 109 outside
    it are the words of 58 callouts: what the draughtsman set on one line the
    extractor hands over one word at a time.
    """
    in_the_title_block = [entry for entry in sheet_2023.printed
                          if sheet_reader._in_the_title_block(entry, sheet_2023)]
    callouts = sheet_reader._the_callouts(sheet_2023)
    fragments = [entry for callout in callouts for entry in callout]

    assert (len(sheet_2023.printed), len(in_the_title_block)) == (230, 121)
    assert (len(fragments), len(callouts)) == (109, 58)
    assert not [entry for entry in fragments
                if sheet_reader._in_the_title_block(entry, sheet_2023)]
    assert len(sheet_reader.dimensions(sheet_2023)) == 34
    assert set(sheet_reader.dimensions(sheet_2023)) <= set(fragments), \
        "a callout a part could be built from was cut with the title block"


@needs_pymupdf
def test_a_callout_is_shown_as_the_draughtsman_set_it_and_not_as_words(sheet_2023):
    """
    `\u00d86.6 THRU ALL` is three strings in the text layer and one callout on the
    sheet, and read as three it is a bore, a note and a word nothing accounts
    for.
    """
    said = sheet_reader._the_printed_list(sheet_2023)
    callouts = {" ".join(entry.text for entry in callout): callout
                for callout in sheet_reader._the_callouts(sheet_2023)}

    assert len(callouts["6.6 THRU ALL"]) == 3
    assert len(callouts["MAKE CHANGES AS INDICATED IN RED REVISION CLOUDS."]) == 8
    assert "\u00d86.6 THRU ALL" in said
    assert "\u00d86.6 X 8.0 THRU ALL" in said
    assert "(C'BORE SLOT FOR M6 SHCS)" in said
    assert "MAKE CHANGES AS INDICATED IN RED REVISION CLOUDS." in said
    assert "PROPRIETARY AND CONFIDENTIAL" not in said
    assert "SCALE" not in said


@needs_pymupdf
def test_one_design_call_carries_the_sheet_cut_to_what_it_can_prove(sheet_2023):
    """
    What one Design turn carries, on a phase that sends it again every turn.

    The 27 pictures are what this module tiles, traces and pays for
    deliberately and the cut leaves them alone — a tiled view's whole picture
    is said rather than sent again, and a picture no region reaches is one
    low-cost crop instead of nothing. The text is the sheet's record reduced
    to the part's evidence. The 351 entities nothing can prove anything about
    would be another 13,917 characters beside the 43,923 that go out, and
    they would be carried again on every turn of the phase.
    """
    told = sheet_reader.shown(sheet_2023)
    pictures = [piece["show"] for piece in told if "show" in piece]
    characters = sum(len(piece["say"]) for piece in told if "say" in piece)
    left_out = [entity for view in sheet_2023.drawn for entity in view.entities
                if entity.role == "unclassified"]

    assert (len(pictures), sum(_costs(path) for path in pictures)) == (27, 21291)
    assert characters == 43923
    assert characters < 45000, "the sheet grew back into the request"
    assert sum(len(sheet_reader._said(entity)) for entity in left_out) == 13917


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

    sheet = sheet_reader.read(RASTER, into=str(tmp_path))
    original = Image.open(RASTER).size
    for crop in sheet.crops:
        width, height = Image.open(crop.path).size
        x0, y0, x1, y1 = crop.rect
        assert width <= int(x1 - x0) + 1 and height <= int(y1 - y0) + 1, crop.name
    assert original


@needs_pymupdf
def test_a_bore_drawn_only_as_pixels_is_measured_in_millimetres(sheet):
    """
    The shape the sheet never wrote down.

    Every view on these sheets is a placed raster at 186 DPI, so the outline
    is not in the vector content at any DPI the page is rendered at — this
    module has always said so and stopped there. It does not have to: the
    picture is at a known scale, so a hole in it is a circle whose diameter is
    arithmetic. `mm25-p1-1.pdf` prints Ø35 and Ø40, and the pixels measure 33.9
    and 38.9 — low by a millimetre, because a hole is measured to the inside of
    the line that draws it, and that is a bias rather than a mistake.

    Traced, never trusted: this is a second opinion about a number the text
    layer already carries exactly, and it exists to contradict a misreading. It
    is well inside the 8 mm that ADR-0019 lost and nowhere near able to supply
    the 35 itself.
    """
    circles = [entity for view in sheet.drawn for entity in view.entities
               if entity.kind == "circle" and entity.source == "traced"]
    assert circles, "not one circle was recovered from the pictures"
    diameters = [entity.diameter for entity in circles]
    assert any(-1.5 <= across - 35.0 <= 0 for across in diameters), diameters
    assert any(-1.5 <= across - 40.0 <= 0 for across in diameters), diameters


@needs_pymupdf
def test_a_callout_that_disagrees_with_the_pixels_it_points_at_is_a_conflict(sheet):
    """
    The whole point of tracing. A leader ends inside a hole; the hole has a
    measured diameter; the callout says what the draughtsman meant it to be.
    Where the two agree the sheet is confirming itself, and where they do not
    the disagreement is put on the record rather than settled here.
    """
    said = " ".join(sheet.notes) + sheet_reader._the_drawn_list(sheet)
    assert "\u00d835" in said or "35" in said
    assert not [entity for view in sheet.drawn for entity in view.entities
                if "disagrees" in sheet_reader._said(entity)], (
        "this sheet agrees with itself; a disagreement here is a defect")


def test_a_bore_called_one_thing_and_drawn_another_is_put_on_the_record():
    """
    The join, exercised where a real sheet cannot exercise it: these four sheets
    agree with themselves, so the disagreement has to be built to be seen.

    A callout of Ø25 whose leader ends inside a hole the pixels measure at 34 is
    9 mm apart, which is the size of the error ADR-0019 lost. It is recorded in
    the words of both, and it is not resolved — that is the Plan Gate's job and
    the user's, not this module's.
    """
    printed = [sheet_reader.Printed(text="25", at=(10.0, 10.0), notation="\u00d8")]
    leaders = [{"callout": "25", "printed_at": (10.0, 10.0),
                "its_leader_reaches": (100.0, 100.0)}]
    hole = sheet_reader.Entity(kind="circle", source="traced", role="contour",
                               size=34.0, diameter=34.0, centre=(0.0, 0.0))
    circles = [((100.0, 100.0), 24.0, 34.0, hole)]
    notes = []

    sheet_reader._the_pixels_against_the_callouts(circles, printed, leaders,
                                                  notes)

    assert notes and "9.0 mm" in notes[0]
    assert "nothing here settles which is meant" in notes[0]


def test_a_callout_whose_leader_lands_nowhere_near_a_hole_is_left_alone():
    """A leader that ends on a face is not evidence about a bore."""
    printed = [sheet_reader.Printed(text="25", at=(10.0, 10.0), notation="\u00d8")]
    leaders = [{"callout": "25", "printed_at": (10.0, 10.0),
                "its_leader_reaches": (400.0, 400.0)}]
    notes = []

    hole = sheet_reader.Entity(kind="circle", source="traced", role="contour",
                               size=34.0, diameter=34.0, centre=(0.0, 0.0))
    sheet_reader._the_pixels_against_the_callouts(
        [((100.0, 100.0), 24.0, 34.0, hole)], printed, leaders, notes)

    assert notes == []


@needs_pymupdf
def test_the_outline_of_a_view_placed_as_a_picture_is_read_as_lines_and_arcs(
        sheet):
    """
    The shape itself, which is what was missing.

    Holes alone are not a part. `mm25-p1-1.pdf`'s top view is a plate 150 long
    and 50 deep with a radiused end at each end, and none of that is anywhere in
    the file: the view is a placed raster and the vector content around it is
    dimension lines. Walked round and fitted whole, the silhouette comes back as
    two straight edges 50.3 mm apart and two R25.1 ends whose centres are 99.9
    apart — 150.1 over the part, against a sheet that prints 150.

    Fitted whole rather than greedily, and an arc only where the run actually
    turns: a straight edge on a rendered view wanders a pixel or two, and a
    circle will always fit that wander at a radius of half a metre.
    """
    view = [entity for shown in sheet.drawn for entity in shown.entities
            if shown.view.startswith("the middle left")]

    ends = [entity for entity in view if entity.kind == "arc"
            and entity.source == "traced" and 25.0 <= entity.radius < 26.0]
    assert len(ends) == 2, view

    centres = sorted(entity.centre[0] for entity in ends)
    assert abs((centres[1] - centres[0]) + 25.1 + 25.1 - 150.0) < 1.0, centres

    edges = sorted(entity.end[1] for entity in view
                   if entity.kind == "line" and entity.source == "traced")
    assert abs((edges[-1] - edges[0]) - 50.0) < 1.0, edges


@needs_pymupdf
def test_a_straight_edge_is_never_reported_as_an_arc_of_half_a_metre(sheet):
    """
    The failure mode of fitting circles to pixels, named. A radius the part does
    not have is worse than no radius at all: it is a number a reader can write
    down.
    """
    radii = [entity.radius for shown in sheet.drawn
             for entity in shown.entities
             if entity.kind == "arc" and entity.source == "traced"]
    assert radii
    assert max(radii) < 200.0, sorted(radii)[-3:]


@needs_pymupdf
def test_the_view_each_round_thing_is_round_in_is_reported(sheet_2023):
    """
    The two M6 holes of the 2023 clamp are round in the plan and nowhere else.
    Their axes are therefore not the bore's, which is round in the elevation —
    the run that named this built all three parallel and the clamp screw ended
    up alongside the split it exists to pinch.

    The plan of the arm is drawn twice on this sheet (once at the top and once
    larger and shaded below) and both draw these holes as circles. What matters
    is the view that does not: the elevation, where the Ø42 bore is the round
    thing and these two are a pair of parallel lines.
    """
    round_here = sheet_reader.round_in(sheet_2023)

    holes = [view for view, wide in round_here.items()
             if any(6.0 <= one <= 6.6 for one in wide)]
    assert holes, "the Ø6.6 holes were traced round in no view at all"
    assert not any("middle centre" in view for view in holes), holes


@needs_pymupdf
def test_what_a_view_is_round_in_is_said_to_the_reader_of_the_sheet(sheet_2023):
    said = "\n".join(piece.get("say", "") for piece in sheet_reader.shown(sheet_2023))

    assert "round in the view its axis points at" in said
    assert "round_in" in said


def test_a_sheet_nobody_could_read_is_round_in_nothing():
    assert sheet_reader.round_in(None) == {}


@needs_pymupdf
def test_a_view_too_big_for_one_crop_is_still_shown_whole_once(sheet_2023):
    """
    A view cut into three is three pictures holding a third of a projection
    each, and relating a circle in the plan to the two lines in the elevation
    that are the same hole was being done across them (FCC-113).
    """
    tiled = {crop.name.split(", part ")[0] for crop in sheet_2023.crops
             if ", part " in crop.name}

    assert tiled, "nothing on this sheet was tiled, so this proves nothing"
    assert {whole.name for whole in sheet_2023.wholes} == tiled
    assert all(whole.rect[2] - whole.rect[0] > 0 for whole in sheet_2023.wholes)


@needs_pymupdf
def test_the_whole_view_is_said_not_shown_before_its_pieces(sheet_2023):
    """
    The overview already carries a tiled view whole, so the sentence tying its
    pieces together is what precedes them now, and the whole's own picture is
    not among what is shown at all.
    """
    told = sheet_reader.shown(sheet_2023)
    whole = sheet_2023.wholes[0]
    shown_paths = {piece["show"] for piece in told if "show" in piece}

    assert whole.path not in shown_paths
    at = next(n for n, piece in enumerate(told)
             if "say" in piece and "how the pieces fit together" in piece["say"])
    first_piece = next(n for n, piece in enumerate(told)
                       if piece.get("show", "").endswith(".png") and n > at)
    assert first_piece > at


@needs_pymupdf
def test_a_view_that_fits_in_one_crop_is_not_sent_twice(sheet_2023):
    whole = {crop.name for crop in sheet_2023.crops if ", part " not in crop.name}

    assert whole & {one.name for one in sheet_2023.wholes} == set()


@needs_pymupdf
def test_view_names_lists_each_view_once_in_reading_order(sheet):
    """
    One name per view, a tile's own tail stripped, in the order the crops
    that carry them were built — the mm25 sheet meets `SECTION B-B` first and
    `the upper right of the sheet` (its isometric) last.
    """
    names = sheet_reader.view_names(sheet)

    assert names[0] == "SECTION B-B"
    assert names[-1] == "the upper right of the sheet"
    assert len(names) == len(set(names)), "a view was named twice"
    assert all(", part " not in name for name in names)


def test_view_names_of_no_sheet_is_nothing():
    assert sheet_reader.view_names(None) == []


@needs_pymupdf
def test_shown_with_no_views_is_unchanged(sheet):
    """The default is the whole sheet: passing `views=None` is not a fourth thing."""
    assert sheet_reader.shown(sheet) == sheet_reader.shown(sheet, views=None)


@needs_pymupdf
def test_shown_for_one_view_sends_only_its_own_pictures(sheet):
    """
    `the middle left of the sheet` fits in one crop: asking for it sends the
    sheet overview and that one crop, and no other view's picture.
    """
    told = sheet_reader.shown(sheet, views=["the middle left of the sheet"])
    pictures = [piece["show"] for piece in told if "show" in piece]
    own = next(crop for crop in sheet.crops
              if crop.name == "the middle left of the sheet")

    assert pictures == [sheet.overview, own.path]


@needs_pymupdf
def test_shown_for_one_view_slices_the_line_work_and_the_between_list(sheet):
    """
    `SECTION A-A`'s own entities and its own printed distances are here; the
    line work `SECTION B-B` was drawn with is not, even though the full sheet
    carries both under one `drawn` heading.
    """
    told = sheet_reader.shown(sheet, views=["SECTION A-A"])
    said = "\n".join(piece["say"] for piece in told if "say" in piece)

    assert "SECTION A-A:" in said
    assert "SECTION B-B:" not in said
    assert "SECTION A-A" in sheet_reader._the_between_list(sheet)
    assert sheet_reader._the_between_list(sheet, {"SECTION A-A"}) is not None
    assert "SECTION B-B" not in sheet_reader._the_between_list(sheet, {"SECTION A-A"})


@needs_pymupdf
def test_shown_for_a_tiled_view_still_ties_its_pieces_together(sheet):
    """
    A tiled view asked for on its own still says how its pieces fit — the
    sheet overview it points back at is shown regardless of `views`, because
    that overview is the sheet's, not any one view's.
    """
    told = sheet_reader.shown(sheet, views=["SECTION B-B"])
    pictures = [piece["show"] for piece in told if "show" in piece]
    said = "\n".join(piece["say"] for piece in told if "say" in piece)
    own = [crop.path for crop in sheet.crops if crop.name.startswith("SECTION B-B,")]

    assert "SECTION B-B is tiled below" in said
    assert pictures == [sheet.overview] + own


@needs_pymupdf
def test_shown_for_a_shape_only_view_still_says_what_it_is(sheet):
    """The isometric asked for on its own is still captioned as not a source for numbers."""
    told = sheet_reader.shown(sheet, views=["the upper right of the sheet"])
    said = "\n".join(piece["say"] for piece in told if "say" in piece)

    assert "not a source for numbers" in said


@needs_pymupdf
def test_the_introduction_names_what_was_asked_for(sheet):
    one = sheet_reader.shown(sheet, views=["SECTION A-A"])[0]["say"]
    two = sheet_reader.shown(sheet, views=["SECTION A-A", "SECTION B-B"])[0]["say"]
    whole = sheet_reader.shown(sheet)[0]["say"]

    assert "The picture after it is SECTION A-A, close up, and is what" in one
    assert "SECTION A-A and SECTION B-B, close up, and are what" in two
    assert "the same sheet close up, one per view" in whole


@needs_pymupdf
def test_every_view_name_round_trips_through_shown(sheet):
    """Every name `view_names` hands out is one `shown` can be asked for."""
    for view in sheet_reader.view_names(sheet):
        told = sheet_reader.shown(sheet, views=[view])
        assert any("show" in piece for piece in told), view


@needs_pymupdf
def test_a_view_not_on_the_sheet_gets_nothing_of_its_own(sheet):
    told = sheet_reader.shown(sheet, views=["VIEW Z-Z"])
    pictures = [piece["show"] for piece in told if "show" in piece]

    assert pictures == [sheet.overview]


@needs_pymupdf
def test_a_bore_the_tracer_could_only_get_part_of_is_still_evidence(sheet_2023):
    """
    The Ø42 clamp bore is drawn in the middle centre view and the tracer never
    closed it — the centre lines crossing it leave arcs. `round_in` reports
    nothing there at all, and the run that read the bore correctly was stopped
    for having no evidence its own reader had thrown away.
    """
    assert "the middle centre of the sheet, on page 1" not in sheet_reader.round_in(sheet_2023)

    evidence = sheet_reader.round_evidence(sheet_2023)["the middle centre of the sheet, on page 1"]

    assert any(abs(wide - 42.0) <= 1.5 for wide in evidence), sorted(evidence)


@needs_pymupdf
def test_the_evidence_carries_the_circles_the_statement_does(sheet_2023):
    """Weaker, never narrower: every full circle is in it too."""
    evidence = sheet_reader.round_evidence(sheet_2023)

    for view, wide in sheet_reader.round_in(sheet_2023).items():
        assert set(wide) <= set(evidence[view])


def test_a_sheet_nobody_could_read_is_evidence_of_nothing():
    assert sheet_reader.round_evidence(None) == {}


@needs_pymupdf
def test_an_entity_carries_its_geometry_rather_than_a_sentence_about_it(sheet):
    """
    What the tracer measured is a circle with a centre and a diameter. Written
    down as `traced circle \u00d86.11 centred (102.77, 19.31)` it is a sentence,
    and the only way back to the number is a regex over this module's own prose
    — which is exactly what `round_evidence` used to be.
    """
    circles = [entity for view in sheet.drawn for entity in view.entities
               if entity.kind == "circle"]

    assert circles, "not one circle was recovered from the pictures"
    assert all(entity.source == "traced" for entity in circles)
    assert all(isinstance(entity.diameter, float)
               and isinstance(entity.centre, tuple) for entity in circles)


@needs_pymupdf
def test_a_line_and_an_arc_carry_their_ends_and_their_centre(sheet_2023):
    """The same for the two kinds the draughtsman's own line work comes in."""
    entities = [entity for view in sheet_2023.drawn for entity in view.entities]

    lines = [entity for entity in entities if entity.kind == "line"]
    arcs = [entity for entity in entities if entity.kind == "arc"]

    assert lines and arcs
    assert all(len(entity.start) == 2 and len(entity.end) == 2
               for entity in lines)
    assert all(isinstance(entity.radius, float) and entity.centre
               for entity in arcs)
    assert {entity.source for entity in entities} <= {"vector", "traced",
                                                      "centre"}


def test_nothing_here_reads_this_module_s_own_prose():
    """
    The rule the representation exists for. A sentence is written in one place,
    for the reader of the sheet, and nothing downstream parses it back.
    """
    assert not hasattr(sheet_reader, "_A_CIRCLE")
    assert not hasattr(sheet_reader, "_AN_ARC")


@needs_pymupdf
def test_the_line_work_reads_as_it_was_recorded_reading(sheet_2023):
    """
    The drawn list is what the reader of the sheet is actually shown, and it is
    long enough that a change anywhere in it is easy to make and hard to see.
    The recording is what it reads as; a diff against it is a decision.
    """
    recorded = os.path.join(os.path.dirname(__file__), "recorded",
                            "drawn_list_2023.txt")
    with open(recorded, encoding="utf-8") as f:
        assert sheet_reader._the_drawn_list(sheet_2023) == f.read()


@needs_pymupdf
def test_every_entity_says_what_the_module_can_prove_it_is(sheet_2023):
    """
    A view arrives as line work, and three quarters of that line work is the
    draughtsman's measuring apparatus. Measured on page 2 of the 2023 sheet:
    2824 solid segments, 201 chains, 170 of them a single segment 3.1 mm long,
    and 125 dimension spans. Listed together under a heading whose last clause
    is "a contour is the part", a 40 mm extension line reads as a 40 mm edge.
    """
    roles = {entity.role for view in sheet_2023.drawn
             for entity in view.entities}

    assert roles <= {"contour", "dimension", "leader", "centre", "unclassified"}
    assert "dimension" in roles, "the sheet is covered in dimension lines"


@needs_pymupdf
def test_a_contour_is_only_ever_something_that_was_traced(sheet_2023):
    """
    Every view body on these sheets is a placed picture, so the vector layer
    holds no outline at all — the largest open chain on page 2 measures
    15.6 x 2.3 mm. Nothing in it may be called the part.
    """
    contours = [entity for view in sheet_2023.drawn for entity in view.entities
                if entity.role == "contour"]

    assert contours
    assert all(entity.source == "traced" for entity in contours)


@needs_pymupdf
def test_a_line_a_printed_number_was_measured_against_is_a_dimension(sheet):
    """
    Not a guess: `_measured` already pairs a printed number with the span it is
    printed against, and a run lying along one of those spans is that span's
    dimension line. A line lying along no measured span stays `unclassified`,
    which is honest where naming it would not be.
    """
    dimensions = [entity for view in sheet.drawn for entity in view.entities
                  if entity.role == "dimension"]

    assert dimensions
    assert all(entity.source == "vector" for entity in dimensions)


@needs_pymupdf
def test_the_reader_is_told_which_line_is_which(sheet_2023):
    """The list says what each entity is, rather than naming three kinds once."""
    said = sheet_reader._the_drawn_list(sheet_2023)

    assert "dimension line" in said
    assert "a contour is the part" not in said


@needs_pymupdf
def test_the_drawn_list_carries_what_carries_a_role_and_counts_the_rest(sheet_2023):
    """
    Of the 842 entities the 2023 clamp is drawn with, 89 are the draughtsman's
    own and can be proved to be something — 32 `dimension`, 50 `leader`, 7
    `centre` — and 402 are the contours traced off the pictures the views were
    placed as, which are the part itself. The remaining 351 are an extension
    line, a hatch stroke or an edge with nothing to say which, and read out
    beside the rest they are where a 40 mm extension line becomes a 40 mm edge.

    They stay in `drawn`, which is the record of the sheet; the list Design is
    shown says how many it left out instead of looking complete.
    """
    entities = [entity for view in sheet_2023.drawn for entity in view.entities]
    proved = [entity for entity in entities if entity.role != "unclassified"]
    said = sheet_reader._the_drawn_list(sheet_2023)
    named = [line for line in said.split("\n")
             if line.startswith("    ") and not line.startswith("    (")]

    assert (len(entities), len(proved)) == (842, 491)
    assert len([entity for entity in proved if entity.source != "traced"]) == 89
    assert {role: len([entity for entity in proved if entity.role == role])
            for role in ("dimension", "leader", "centre", "contour")} == {
        "dimension": 32, "leader": 50, "centre": 7, "contour": 402}
    assert len(named) == 491
    assert sum(int(line.split()[1]) for line in said.split("\n")
               if "nothing could prove anything about" in line
               and line.startswith("    (")) == 351


@needs_pymupdf
def test_a_traced_outline_arrives_as_the_walk_it_was_measured_along(sheet):
    """
    A silhouette is walked round in order, and the order is the only thing that
    says these two edges meet. Sorted by length it is a bag of pieces, and a
    closed profile cannot be recovered from a bag.
    """
    outlines = [outline for view in sheet.drawn for outline in view.outlines]

    assert outlines
    walked = max(outlines, key=lambda outline: len(outline.entities))
    ends = [entity.end or entity.centre for entity in walked.entities]
    starts = [entity.start or entity.centre for entity in walked.entities]
    assert all(sheet_reader._distance(end, start) < 1.0
               for end, start in zip(ends, starts[1:])), (
        "each entity of a walk starts where the one before it ended")


@needs_pymupdf
def test_an_outline_that_closes_says_it_closes(sheet):
    """
    The one fact a sketch needs and a list of pieces cannot carry. The top view
    of `mm25-p1-1.pdf` is a plate with a radiused end at each end: one closed
    silhouette, and a hole is another.
    """
    outlines = [outline for view in sheet.drawn for outline in view.outlines]

    assert any(outline.closed for outline in outlines)


@needs_pymupdf
def test_what_the_cap_takes_off_a_traced_outline_is_counted(sheet_2023):
    """
    Measured before this: `_shaped` returned 51 and 41 entities for the two
    largest silhouettes on the 2023 sheet and both were cut to 24, so 44 pieces
    of the two biggest outlines on the sheet went missing and nothing said so.
    The vector list has always counted what it dropped; this did not.
    """
    for view in sheet_2023.drawn:
        kept = sum(len(outline.entities) for outline in view.outlines)
        assert kept == len([entity for entity in view.entities
                            if entity.source == "traced"])
        if view.traced_left_out:
            assert "left out" in sheet_reader._the_drawn_list(sheet_2023)


@needs_pymupdf
def test_the_biggest_silhouette_on_the_sheet_is_not_the_one_that_is_cut(sheet_2023):
    """An outline is kept or dropped whole, and the biggest is never dropped."""
    traced = {view.view: sum(1 for entity in view.entities
                             if entity.source == "traced")
              for view in sheet_2023.drawn}

    assert max(traced.values()) >= 51, traced


def test_a_hole_the_sheet_agrees_with_carries_the_number_the_sheet_prints():
    """
    The join the tracing exists for, made rather than reported: a callout of
    \u00d840 whose leader ends in a hole the pixels measure at 39.95 is one
    feature seen twice, and the exact one of the two is the printed one. The
    shape stays the tracing's — the traced diameter is kept beside the size —
    and `read` is what tells a size that came out of the text layer from one
    measured off the picture.

    Constructed rather than read off a sheet because not one of the four
    produces an agreement this close: measured there, a traced circle reads
    0.49 to 1.17 mm under the number printed for it.
    """
    hole = sheet_reader.Entity(kind="circle", source="traced", role="contour",
                               size=39.95, diameter=39.95, centre=(0.0, 0.0))
    view = sheet_reader.Drawn(view="a view", entities=[hole])
    printed = [sheet_reader.Printed(text="40", at=(10.0, 10.0), notation="\u00d8")]
    leaders = [{"callout": "40", "printed_at": (10.0, 10.0),
                "its_leader_reaches": (100.0, 100.0)}]
    notes = []

    sheet_reader._the_pixels_against_the_callouts(
        [((100.0, 100.0), 20.0, 39.95, (hole,))], printed, leaders, notes, [view])

    assert notes == []
    snapped = view.entities[0]
    assert snapped.printed == 40.0
    assert snapped.size == 40.0
    assert snapped.diameter == 39.95
    assert snapped.source == "read"
    assert "the sheet prints" in sheet_reader._said(snapped)


def test_a_snapped_circle_is_round_at_the_size_the_sheet_prints():
    """
    What a view is round at is the printed number once one has been carried:
    the traced diameter is the evidence that found the feature and never its
    size, so reporting 39.95 where the sheet prints \u00d840 would hand the
    description the one number this module is not allowed to supply.
    """
    snapped = sheet_reader.Entity(kind="circle", source="read", role="contour",
                                  size=40.0, diameter=39.95, printed=40.0,
                                  centre=(0.0, 0.0))
    sheet = sheet_reader.Sheet(source="a sheet", drawn=[
        sheet_reader.Drawn(view="a view", entities=[snapped])])

    assert sheet_reader.round_evidence(sheet) == {"a view": [40.0]}
    assert sheet_reader.round_in(sheet) == {"a view": [40.0]}


def test_the_millimetre_a_traced_hole_reads_under_by_is_not_an_agreement():
    """
    The band is what the tracer's repeats measure, not what its bias measures.
    A callout of \u00d840 into a hole the pixels make 38.93 — the \u00d840 of
    `mm25-p1-1.pdf` — is 1.07 mm apart, which is a hole measured to the inside
    of the line that draws it and not two readings of one number. Nothing is
    carried.

    And nothing is reported either: the sheet agrees with itself, and a delta
    the tracing produces on its own is not a disagreement between the
    draughtsman and this module. That is the gap between the two bands.
    """
    hole = sheet_reader.Entity(kind="circle", source="traced", role="contour",
                               size=38.93, diameter=38.93, centre=(0.0, 0.0))
    view = sheet_reader.Drawn(view="a view", entities=[hole])
    printed = [sheet_reader.Printed(text="40", at=(10.0, 10.0), notation="\u00d8")]
    leaders = [{"callout": "40", "printed_at": (10.0, 10.0),
                "its_leader_reaches": (100.0, 100.0)}]
    notes = []

    sheet_reader._the_pixels_against_the_callouts(
        [((100.0, 100.0), 20.0, 38.93, (hole,))], printed, leaders, notes, [view])

    assert notes == []
    assert view.entities[0].printed is None
    assert view.entities[0].size == 38.93


@needs_pymupdf
def test_no_hole_on_these_sheets_is_traced_close_enough_to_snap(sheet):
    """
    Said of a real sheet, because the band was measured on four of them: the
    tracing reads 0.49 to 1.17 mm under every number printed for it, and the
    snap band is 0.15 mm, so `mm25-p1-1.pdf` carries no printed size onto a
    traced circle and raises no conflict about one either.
    """
    circles = [entity for view in sheet.drawn for entity in view.entities
               if entity.kind == "circle" and entity.source in ("traced", "read")]

    assert circles
    assert [entity for entity in circles if entity.printed is not None] == []
    assert not [note for note in sheet.notes if "difference of" in note]


def test_a_bore_the_pixels_contradict_is_a_conflict_and_never_a_value():
    """
    Outside the band nothing is carried. A callout of \u00d825 whose leader ends
    in a hole the pixels measure at 34 is 9 mm apart, which is the size of the
    error ADR-0019 lost.
    """
    hole = sheet_reader.Entity(kind="circle", source="traced", role="contour",
                               size=34.0, diameter=34.0, centre=(0.0, 0.0))
    view = sheet_reader.Drawn(view="a view", entities=[hole])
    printed = [sheet_reader.Printed(text="25", at=(10.0, 10.0), notation="\u00d8")]
    leaders = [{"callout": "25", "printed_at": (10.0, 10.0),
                "its_leader_reaches": (100.0, 100.0)}]
    notes = []

    sheet_reader._the_pixels_against_the_callouts(
        [((100.0, 100.0), 24.0, 34.0, hole)], printed, leaders, notes, [view])

    assert notes and "9.0 mm" in notes[0]
    assert view.entities[0].printed is None


def test_a_bore_traced_in_arcs_carries_the_size_a_leader_reaches_it_with():
    """
    A hole crossed by a centre line or a section hatch is flooded as two
    patches and comes back as arcs rather than as one circle, and a callout
    reaching into one of them had nothing to name: `printed` was attached to
    traced circles alone. So the arc a sheet can produce carried no printed
    size, and `sketch_check`'s arc branch — the one that catches a bore read as
    two different sizes — ran on nothing any sheet could hand it.

    Every arc of the one patch takes the size, because they are one hole: a
    leader stands inside all of them and sizing the first alone would invent
    the very disagreement the check reports.
    """
    arcs = tuple(sheet_reader.Entity(
        kind="arc", source="traced", role="contour", size=41.9, radius=20.95,
        centre=(0.0, 0.0), start=start, end=end)
        for start, end in (((20.95, 0.0), (0.0, 20.95)),
                           ((0.0, 20.95), (-20.95, 0.0))))
    view = sheet_reader.Drawn(view="a view", entities=list(arcs))
    printed = [sheet_reader.Printed(text="42", at=(10.0, 10.0), notation="\u00d8")]
    leaders = [{"callout": "42", "printed_at": (10.0, 10.0),
                "its_leader_reaches": (100.0, 100.0)}]
    notes = []

    sheet_reader._the_pixels_against_the_callouts(
        [((100.0, 100.0), 20.95, 41.9, arcs)], printed, leaders, notes, [view])

    assert notes == []
    assert [entity.printed for entity in view.entities] == [42.0, 42.0]


def test_a_number_is_never_snapped_onto_a_hole_by_being_the_nearest_number():
    """
    A circle no leader points into does not snap, which is the question
    settled here rather than left to the code. The 2023 sheet prints
    \u00d86.6 for its M6 clearance holes, in a view of its own, and prints a
    bare `6` in four other places. The traced circles measure 6.10, so the
    nearest printed number is the `6` — the wrong number, from the wrong view,
    by the depth of the counterbore, and 0.09 mm away where its own callout is
    0.5 mm away.

    The leader is traced through the file's own line work and is the only
    evidence there is of which hole a callout is about. Without one there is
    none, and a size invented from proximity is worse than no size.
    """
    hole = sheet_reader.Entity(kind="circle", source="traced", role="contour",
                               size=6.10, diameter=6.10, centre=(0.0, 0.0))
    view = sheet_reader.Drawn(view="a view", entities=[hole])
    printed = [sheet_reader.Printed(text="6", at=(10.0, 10.0), notation="")]
    notes = []

    sheet_reader._the_pixels_against_the_callouts(
        [((100.0, 100.0), 3.05, 6.10, hole)], printed, [], notes, [view])

    assert notes == []
    assert view.entities[0].printed is None


@needs_pymupdf
def test_a_printed_distance_reaches_the_two_points_it_measures(sheet):
    """
    The span says how far apart two faces are; the extension lines say where
    they are, and an extension line is drawn from the very face its dimension
    measures. Measured on `mm25-p1-1.pdf`: the 150 comes back as two points
    150.00 mm apart in the view's own millimetres.
    """
    assert sheet.between

    hundred_and_fifty = [entry for entry in sheet.between
                         if entry.printed == 150.0]
    assert hundred_and_fifty
    apart = sheet_reader._distance(hundred_and_fifty[0].first,
                                   hundred_and_fifty[0].second)
    assert abs(apart - 150.0) < sheet_reader.THE_FEET_AGREE, apart


@needs_pymupdf
def test_a_pair_of_feet_that_does_not_measure_what_is_printed_is_not_reported(
        sheet_2023):
    """
    The check on the follow. Of the 42 printed numbers a span measures and two
    feet are found for across the four sheets, 35 pairs come back measuring what
    the sheet prints and 7 are 1.05 to 18.4 mm out — those are the wrong lines,
    and the module says nothing rather than reporting a point of the part it
    cannot stand behind. Eleven of the 35 are the 2023 clamp's.
    """
    assert len(sheet_2023.between) == 11
    for entry in sheet_2023.between:
        apart = sheet_reader._distance(entry.first, entry.second)
        assert abs(apart - entry.printed) <= sheet_reader.THE_FEET_AGREE, entry


@needs_pymupdf
def test_the_distances_are_said_the_way_the_leaders_are(sheet):
    """Reported, never concluded: where the points are, not what they are."""
    said = sheet_reader._the_between_list(sheet)

    assert "measures" in said
    assert "still yours to read" in said


@needs_pymupdf
def test_a_view_dimensioned_on_four_sides_is_still_traced(sheet_2023):
    """
    A section 40 mm across, dimensioned all round, is one fifth of its own
    region and it is still a section. Measured before this: `VIEW C-C` on page
    2 of the 2023 sheet holds a picture wholly inside it covering 0.33 of the
    region, and the tracer refused to look at it, so the three printed
    distances that reach that view reached nothing.
    """
    view = [one for one in sheet_2023.drawn
            if one.view.startswith("VIEW C-C")][0]

    assert view.outlines, "the view holds a picture and it is traced"


@needs_pymupdf
def test_what_a_picture_holds_and_what_it_is_traced_from_are_two_questions(
        sheet_2023):
    """
    `from_a_picture` warns that a view's *body* is a raster, so the vector list
    beside it is annotation and not the outline. That is a question about the
    region. Whether there is a picture here to measure is a question about the
    picture, and sharing one threshold cost ten of the twenty-six distances.
    """
    traced = [one for one in sheet_2023.drawn if one.outlines]
    bodied = [one for one in sheet_2023.drawn if one.from_a_picture]

    assert {one.view for one in bodied} <= {one.view for one in traced}
    assert len(traced) > len(bodied), (
        "a view can be traced without its body being called a picture")


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

    path = os.path.join(os.path.dirname(__file__), "modelmania",
                        "model-mania-2024.pdf")
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
def test_a_shape_only_crop_says_it_carries_no_callouts(sheet_2023):
    """
    A crop the model has never been shown before needs to be told what it is
    looking at, or a shape with no numbers on it reads as a crop that lost
    its numbers rather than as one that never had any.
    """
    told = sheet_reader.shown(sheet_2023)
    shape_names = {crop.name for crop in sheet_2023.crops if crop.shape_only}
    assert shape_names

    said = [piece["say"] for piece in told if "say" in piece]
    for name in shape_names:
        matches = [text for text in said
                  if text.startswith(name) and "not a source for numbers" in text]
        assert matches, "%s: no sentence says it carries no callouts" % name


@needs_pymupdf
def test_which_views_are_bodied_by_a_picture_is_the_set_it_was(sheet_2023):
    """
    `from_a_picture` asks how much of a *region* a picture holds, and choosing
    where a picture is traced does not touch it. Pinned as the set and not as a
    count, so a view swapped for another cannot pass.
    """
    assert {one.view for one in sheet_2023.drawn if one.from_a_picture} == {
        "SECTION A-A, on page 1",
        "VIEW B, on page 1",
        "the middle centre of the sheet, on page 1",
        "the upper centre of the sheet, on page 1",
        "the upper left of the sheet, on page 2"}


@needs_pymupdf
def test_a_number_the_sheet_never_printed_and_never_drew_is_reported(sheet):
    """
    The other direction, and the shape of ADR-0019. A run read a depth of 100
    off SECTION B-B and planned 150 x 50 x 50 two turns later: the 50 was on
    nothing, and every check in the pipeline reads one reading against another.
    """
    invented = sheet_reader.invented(sheet, {"envelope": {
        "the depth front to rear": {"value": "1234 mm", "flag": "read"}}})

    assert invented == [1234.0], invented


@needs_pymupdf
def test_a_number_the_sheet_prints_is_cited(sheet):
    """`mm25-p1-1.pdf` prints 150 across its top view."""
    assert sheet_reader.invented(sheet, {"envelope": {
        "the length": {"value": "150 mm", "flag": "read"}}}) == []


@needs_pymupdf
def test_a_number_the_tracing_measured_is_cited(sheet):
    """
    A distance nobody printed is `derived` and not invented, which is what the
    drawn list already tells the reader to do. The tracing is the citation.
    """
    traced = [entity.size for view in sheet.drawn for entity in view.entities
              if entity.source == "traced" and entity.kind == "line"
              and entity.size > 10.0]

    assert traced
    assert sheet_reader.invented(sheet, {"features": [
        {"id": "a face", "kind": "%.0f mm long" % traced[0]}]}) == []


@needs_pymupdf
def test_the_sweep_is_not_what_makes_a_number_cited(sheet):
    """
    `printed_on_the_sheet` is this module's own list written back over the
    model's paraphrase (FCC-112), so counting it would cite every number by
    having copied it in — the same failure `uncited` was built to avoid.
    """
    invented = sheet_reader.invented(sheet, {
        "printed_on_the_sheet": [{"text": "1234"}],
        "envelope": {"the length": {"value": "1234 mm"}}})

    assert invented == [1234.0]


def _a_sheet_printing(*callouts):
    """A sheet that prints these callouts and draws nothing, for the citations."""
    return sheet_reader.Sheet(
        source="a.pdf",
        printed=[sheet_reader.Printed(text=text, at=(at * 10.0, 0.0))
                 for at, text in enumerate(callouts)])


def test_a_number_the_sheet_measures_between_two_of_its_own_is_derived():
    """
    Subtraction is what a drawing dimensioned from one end asks the reader to
    do: `95.5 mm to the bore axis` is the 2023 sheet's 108 less its 12.5.
    """
    assert sheet_reader.invented(_a_sheet_printing("108", "12.5"), {
        "envelope": {"to the bore axis": {"value": "95.5 mm"}}}) == []


def test_a_number_that_is_two_of_the_sheets_added_up_is_not_derived():
    """
    Stating a sum is the prompt's job and checking one is `wrong_sums`. Admitted
    here it exempts every total a description could have added up to.
    """
    assert sheet_reader.invented(_a_sheet_printing("108", "12.5"), {
        "envelope": {"the length": {"value": "120.5 mm"}}}) == [120.5]


def test_a_number_is_not_derived_from_itself():
    """
    A number is its own difference and its own double, so a pair drawn from one
    printed callout twice exempts every doubling of it and nought besides.
    """
    assert sheet_reader.invented(_a_sheet_printing("108"), {
        "envelope": {"the length": {"value": "216 mm"}}}) == [216.0]


def test_a_small_number_on_no_sheet_is_reported():
    """
    No floor. `A_MARK` is the shortest traced entity worth reporting, a
    tracing-noise floor, and reused here it silences a 1.5 mm chamfer.
    """
    assert sheet_reader.invented(_a_sheet_printing("108"), {
        "features": [{"id": "a chamfer", "size": {"value": "1.5 mm"}}]}) == [1.5]


@needs_pymupdf
def test_the_two_sheets_agree_on_far_fewer_numbers_than_either_alone(
        modelmania_sheets):
    """
    The intersection `_off_the_sheet` takes keeps the few values neither sheet
    can cite and drops values either drawing prints.
    """
    descriptions = modelmania_harness.described_numbers()
    first = modelmania_sheets["mm25-p1-1.pdf"]
    second = modelmania_sheets["model-mania_-in-person-2023 copy.pdf"]
    agreed = [
        len(set(sheet_reader.invented(first, {"stated": stated}))
            & set(sheet_reader.invented(second, {"stated": stated})))
        for name in ("mm25-p1-1.pdf", "model-mania_-in-person-2023 copy.pdf")
        for stated in (entry["stated"] for entry in descriptions[name])
    ]

    assert sum(agreed) <= 8, sum(agreed)


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
    seven = [entry for entry in sheet_2023.printed
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
    measured = [entry for entry in sheet_2023.printed if entry.measures is not None]

    assert len(measured) == 14
    assert max(abs(entry.off_by) for entry in measured) <= 0.03


@needs_pymupdf
def test_a_distance_taken_off_a_centre_line_says_so(sheet_2023):
    """
    The 7 of the 2023 clamp's cutout is measured from the depth centre line out
    to the near cheek, and the 14 across both cheeks is the same feature said
    twice. Read as an offset from the front face instead, the cutout comes out
    16 wide with a 1 mm land beside a 14 mm slot, which is the part the recorded
    run built and the fillet it then could not apply
    (`logs/sessions/069d21dbac10416392533fe7c06b62d4.jsonl`).

    Nothing here reads the view: the foot of the 7's second extension line
    stands on a chain line, which is the sheet's own notation for an axis, and
    the point it stands at is the middle of the 14's two feet to two hundredths
    of a millimetre.
    """
    lower = "the lower centre of the sheet, the first one, on page 1"
    seven = [entry for entry in sheet_2023.between
             if entry.printed == 7.0 and entry.view == lower]
    fourteen = [entry for entry in sheet_2023.between
                if entry.printed == 14.0 and entry.view == lower]

    assert len(seven) == 1 and len(fourteen) == 1
    assert seven[0].on_a_centre_line == ("second",)
    assert fourteen[0].on_a_centre_line == ()
    assert seven[0].first == fourteen[0].first, "the two do not start at one cheek"
    assert abs(seven[0].second[1]
               - (fourteen[0].first[1] + fourteen[0].second[1]) / 2) < 0.02


@needs_pymupdf
def test_the_centre_line_a_distance_is_taken_off_is_said_in_the_list(sheet_2023):
    """Design is told which point is an axis, in the line the distance is on."""
    said = sheet_reader._the_between_list(sheet_2023)

    assert "the second of them on a centre line" in said
    assert "measured from the middle of the part and not from a face" in said


@needs_pymupdf
def test_read_registers_its_temp_dir_for_atexit_cleanup(monkeypatch, tmp_path):
    """
    `read()` called with no `into=` makes its own `mkdtemp` directory, and
    nothing else in the repo ever removes an `fcc-sheet-*` directory
    (`grep -rn rmtree core ui` — FCC-263). The cleanup has to be registered
    at creation time, so this patches `atexit.register` to capture the
    callback instead of letting it run at interpreter exit. Read a copy of
    the sheet at a path no other test has used, so `_ALREADY_READ` cannot
    hand back a sheet another test already cached without the patch active.
    """
    import shutil as shutil_module

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
    (`grep -n "^import\\|^from" core/sheet_reader.py` had no `logging` —
    FCC-265). The second call with the same key must hit `_ALREADY_READ` and
    not log again. Reads a copy at a path no other test has used, so the
    cache another test filled cannot hand back a non-exception result.
    """
    import shutil as shutil_module

    copy = tmp_path / "fcc-265.pdf"
    shutil_module.copyfile(SHEET, copy)

    def boom(path, into, legible_px, max_edge):
        raise ValueError("staged failure")

    monkeypatch.setattr(sheet_reader, "_read", boom)

    with caplog.at_level("WARNING", logger="core.sheet_reader"):
        first = sheet_reader.read(str(copy), into=None)
        second = sheet_reader.read(str(copy), into=None)

    assert first is None
    assert second is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert str(copy) in warnings[0].message


def _rectangle_hole(shape, top, left, height, width):
    """A boolean mask, True over one rectangle, for a known hole's patch."""
    patch = numpy.zeros(shape, dtype=bool)
    patch[top:top + height, left:left + width] = True
    return patch


def test_the_holes_in_finds_the_same_holes_the_full_grid_scan_would():
    """
    A part with one hole big enough to count (`A_HOLE` is 120 px) and one too
    small to (16 px) — the small one must stay out of the result, same as
    before. The expected boundary and outline are built from `_boundary` and
    `_outline` directly on the known hole rectangle, not from a second copy
    of `_the_holes_in`'s own loop, so this does not just check the function
    against itself.
    """
    shape = (60, 60)
    part = numpy.ones(shape, dtype=bool)
    part[10:22, 10:22] = False  # 12 x 12 = 144 px, over A_HOLE
    part[40:44, 40:44] = False  # 4 x 4 = 16 px, under A_HOLE

    big_hole = _rectangle_hole(shape, 10, 10, 12, 12)
    expected_boundary = sheet_reader._boundary(big_hole)
    expected_outline = sheet_reader._outline(big_hole)

    holes = sheet_reader._the_holes_in(part)

    assert len(holes) == 1
    numpy.testing.assert_array_equal(holes[0][0], expected_boundary)
    assert len(holes[0][1]) == len(expected_outline)
    assert all(numpy.array_equal(got, want)
               for got, want in zip(holes[0][1], expected_outline))


def test_the_holes_in_does_not_visit_every_pixel_of_a_sparse_canvas():
    """
    A 2000 x 2000 canvas with one small hole: the pure-Python double loop
    over every pixel measured 0.177s on this machine (three runs, no
    outlier); the `numpy.nonzero`-filtered version measured 0.012s on the
    same canvas. 0.08s sits between the two with margin either way.
    """
    part = numpy.ones((2000, 2000), dtype=bool)
    part[500:520, 500:530] = False

    start = time.perf_counter()
    sheet_reader._the_holes_in(part)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.08
