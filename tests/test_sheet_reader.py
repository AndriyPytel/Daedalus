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
"""
import os

import pytest

from core import sheet_reader

SHEET = os.path.join(os.path.dirname(__file__), "modelmania", "mm25-p1-1.pdf")
RASTER = os.path.join(os.path.dirname(__file__), "modelmania", "mm2025-phase1.png")

needs_pymupdf = pytest.mark.skipif(not sheet_reader.available(),
                                   reason="PyMuPDF is not installed")


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    if not sheet_reader.available():
        pytest.skip("PyMuPDF is not installed")
    return sheet_reader.read(SHEET, into=str(tmp_path_factory.mktemp("sheet")))


# ── the resolution ────────────────────────────────────────────────────────────


@needs_pymupdf
def test_the_crops_are_rendered_far_above_the_cap_the_sheet_used_to_arrive_at(sheet):
    """
    Sent whole, this sheet reaches the provider at 143 DPI and puts 14 px of ink
    in a dimension digit — legible often enough to be trusted and wrong often
    enough to matter. The crops exist to end that, so the DPI has to clear it by
    a distance rather than by a little.
    """
    assert sheet.crops, "a sheet with views on it produced no crops"
    assert all(crop.dpi >= 250 for crop in sheet.crops), \
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


# ── the text layer ────────────────────────────────────────────────────────────


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
def test_the_diameter_sign_is_missing_and_the_module_says_so(sheet):
    """
    Measured, and load-bearing: `Ø` is drawn as geometry on this sheet and is
    not in the text layer, so the bore printed `Ø35 THRU` extracts as `35` and
    `THRU`. A module that quietly handed that list on as the callouts would be
    teaching Design that the sheet has no diameters on it.
    """
    printed = [entry.text for entry in sheet.printed]

    assert "35" in printed and "\u00d835" not in printed
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


# ── the leaders ───────────────────────────────────────────────────────────────


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


# ── the coverage check ────────────────────────────────────────────────────────


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


# ── what is shown, and what happens without any of it ─────────────────────────


@needs_pymupdf
def test_the_sheet_is_shown_whole_before_it_is_shown_close_up(sheet):
    """
    The overview is not decoration: without it the close-ups are a pile of
    unplaced fragments, and `seen_in` has nothing to be measured against. It
    goes first because it is what the rest are read against.
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
