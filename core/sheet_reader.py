"""
The sheet as evidence, before anybody reads it.

Design was asked to read a drawing the way a person reads one, and then handed
a picture no person could read. `mm25-p1-1.pdf` is a 11 x 8.5 in sheet whose
dimension text is set in 9.5 pt Century Gothic. Sent whole, it arrives at the
provider's own cap — 1568 px on the long edge, 143 DPI — and a dimension digit
lands 14 px tall, measured off the render rather than argued from the font
size. 14 px is not illegible and that is exactly what makes it expensive: it
reads correctly most of the time, so the misreadings arrive one at a time,
already flagged `read`, in a model that is designed never to overwrite them.
"108 read as 100" (ADR-0019) is that failure and not a worse one.

So this phase runs before Design and changes what Design is looking at:

- **the sheet is rendered here**, at a DPI chosen from the text on it rather
  than left to the provider. Previously a PDF rode along as an opaque `file`
  block and the provider picked the resolution; it picked it against us.
- **the sheet is cut into crops**, one per region the drawing itself lays out,
  because a sheet is not one picture — it is five or six independent views
  stapled inside a frame, and sending them together buys nothing and costs the
  resolution of every one of them.
- **what is printed on it is extracted, not read.** A vector PDF carries the
  draughtsman's own strings with their coordinates. `printed_on_the_sheet` used
  to be the model's own list of what it noticed, checked against itself — a
  count that cannot catch the callout it never saw. Extracted, it is ground
  truth the loop can hold a finished description against.

**The extracted list is numerals, and the notation is measured back onto it.**
`Ø` is not in the text layer at all — the diameter symbol is drawn as geometry,
so the layer yields `35 THRU` where the sheet shows `Ø35 THRU`. It is drawn at a
known size, in a known place, in a known number of pieces, which makes it
arithmetic rather than eyesight: it is recovered from the line work and carried
beside the string instead of inside it, so the text stays the draughtsman's and
the sign stays this module's reading of what he drew.

**And the sheet measures its own numbers.** A dimension line is drawn between
the two faces it measures and broken where its number sits — which is precisely
the break `_chained` refuses to cross — so joining the two halves back into one
span gives the distance the draughtsman committed to, in points, beside the
number he printed on it. Voting `printed ÷ span` across a page recovers the
scale independently of the title block, and the pairs that agree with that vote
give every one of those numbers a second, unread opinion of itself. Measured on
the 2023 sheet, `108` is printed against a span of 306.1 pt and 108.00 mm of
part: ADR-0019's "108 read as 100" is now an 8 mm contradiction on the record
rather than a misreading nobody can see.

**What is left is the shape, and the shape is pixels.** Every view on these
sheets is a placed raster at exactly 186 DPI, so no render of the page at any
resolution recovers an outline that is not in the file. It can still be
measured. The silhouette is walked round in order and cut at its corners, and
each run is fitted whole — as one line, or as one circle, and as an arc only
where the run actually turns, because a straight edge on a rendered view wanders
a pixel or two and a circle will always fit that wander at a radius of half a
metre. The 2025 top view comes back as two edges 50.3 mm apart and two R25.1
ends whose centres are 99.9 apart: a plate 150 by 50, off a picture, on a sheet
whose vector content holds no outline at all. The holes inside it are done the
same way, and the round ones are reported as diameters.

Traced measurements run about a millimetre under the drawn size — an edge is
measured to the inside of the line that draws it — and a shallow curve is given
as the line between its ends rather than as a radius it may not have. Both
errors point the same way on purpose: an understatement can be checked against
the picture, and an invented radius cannot. So the shape comes from here and the
numbers come from the text layer, and what this is for is contradicting a bore
called 25 and drawn 34. It is reported as `traced`, and traced is not read.

Everything here degrades rather than fails. Without PyMuPDF there is no text
layer and no page rendering, and `read` returns None so the caller sends the
attachment exactly as it did before. Without NumPy the pictures stay pictures
and nothing is traced. A raster attachment has no text layer by definition: it
still gets regions and crops, off its own ink, and an empty printed list saying
so.

Nothing here imports FreeCAD, and nothing here reads a model's mind.
"""
import math
import os
import re
import tempfile

try:  # PyMuPDF is what makes a PDF more than an opaque attachment.
    import pymupdf
except ImportError:  # pragma: no cover - exercised by the machines without it
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

from dataclasses import dataclass, field

# What a callout's text box has to measure in the crop it arrives in. Measured
# rather than chosen: on `mm25-p1-1.pdf` a dimension's box is 12.9 pt, which at
# the provider's own 143 DPI cap renders 26 px tall and puts 14 px of actual ink
# in it — the digits are a little over half their box. 14 px is what misreads.
# 50 px of box is 28 px of ink, and doubling the thing that was already failing
# is the whole reason the sheet is cut up at all.
LEGIBLE_PX = 50

# How much of a text box a digit actually inks, measured on the same render.
# Kept visible because it is the only reason 50 above is not 28.
INK_OF_THE_BOX = 0.55

# The long edge of a crop. Providers downscale above their own cap — 1568 px for
# Anthropic, 768 px tiles for Gemini — so a crop wider than this would have its
# resolution taken back off it at the far end, which is the defect this module
# exists to remove.
MAX_EDGE = 1400

# The patch a provider tokenises an image in, and how many of them it will pay
# for. This, and not the long edge, is the cap that actually bites: Anthropic
# costs an image `ceil(w / 28) * ceil(h / 28)` visual tokens and resizes
# anything over 1568 of them. A 1400 x 1400 crop is inside every edge limit,
# costs 2500, and arrives at 1092 x 1092 — the module tiled for a resolution
# and then had 22% of it taken back. The largest square that survives untouched
# is 1092 px, and a crop of any other shape has to be measured rather than
# guessed at.
A_PATCH = 28
THE_BUDGET = 1568

# How much neighbouring crops share. A dimension sits between its arrows and a
# leader crosses whatever is in its way, so a cut anywhere is a cut through
# something; the overlap is what stops a callout from being halved by both crops
# that touch it.
OVERLAP = 0.15

# The grid the regions are found on, in points. Fine enough to keep two views
# apart, coarse enough that a dashed centreline is still one object.
CELL = 4.0

# How far apart two marks can be and still be the same region, in cells. A
# dimension line stands off its view by about this much and belongs with it.
REACH = 3

# A path whose box covers more of the sheet than this is the frame or the
# border, not a view: it touches everything and would merge the sheet into one.
FRAME = 0.85

# How far from the paper a pixel has to be to be a mark. Not a brightness: the
# paper is measured off the edge of the picture rather than assumed to be white.
# `mm2025-phase1.png` is a screenshot exported on a black background, and a
# threshold that called everything dark "ink" made the whole sheet one region —
# one crop, at the resolution the sheet already had, which is no crop at all.
INK = 40

# A row or column this full is the drawing frame, not a view. The frame touches
# every view on the sheet, so left in it joins all of them into one region.
A_RULE = 0.7

# A strip of untouched paper this many cells wide, inside a region, is a gutter
# between two rows or columns of the drawing rather than a gap inside one view.
# Two cells is eight points, and it cannot be wider and still be inside a region
# at all: the reach above is three cells, so anything the flood fill joined
# across is at most two cells of blank paper. Measured on the 2023 sheet, where
# the elevation's last dimension line is at y=376 and the bottom view's first
# mark at y=388, with the bottom view's own extension lines running out under
# the elevation to hold the two together.
GUTTER = 2

# The least a piece of a cut region can measure and still be a view, in points.
# Measured off the placed pictures on these sheets, which are the view bodies:
# the smallest of them is the 2025 section at 167 x 95 pt. Without a floor the
# cutting runs on into the title block, whose rules stand eight points of blank
# paper apart, and hands back four strips of one line each.
A_VIEW = 90.0

# How much of a region a placed picture has to hold before the region is part
# of the view that picture is. Measured on the 2023 sheet, where the top view's
# body is a picture at (79, 27)-(410, 137) and the flood fill breaks that view
# in two: the picture holds 49% of the right-hand fragment — the rest of the
# fragment is the `HEX 11 6` callout printed beside the view — while the
# isometric's picture reaches over from the far corner and clips 35% of the
# same fragment. Two fifths is the line between holding a view and clipping
# one, and a fragment goes to whichever picture holds most of it in any case.
HELD_BY_A_PICTURE = 0.4


@dataclass(frozen=True)
class Crop:
    """One region of the sheet, rendered large enough to read."""

    name: str
    path: str
    # Where it came from on the page, in the page's own units, so a callout's
    # coordinates can be said to be inside it.
    rect: tuple
    dpi: int = 0
    page: int = 1


@dataclass(frozen=True)
class Drawn:
    """One view's own line work, in millimetres of the part."""

    view: str
    entities: list = field(default_factory=list)
    # How many were dropped for being the shorter ones, so a view whose list
    # was cut says so rather than looking complete.
    left_out: int = 0
    # Whether the body of this view was placed as a picture, in which case the
    # outline is not in the line work and the list is the annotation only.
    from_a_picture: bool = False


@dataclass(frozen=True)
class Printed:
    """One string the sheet carries, with where it is and which crop shows it."""

    text: str
    at: tuple
    seen_in: str = ""
    # What the sheet itself measures where this number is printed, in mm, and
    # how far that is from the number. None where no dimension line measures it
    # — a drawing number, a note, a view label — because silence is the honest
    # answer and an invented millimetre is not.
    measures: float = None
    off_by: float = None
    # The symbol drawn — not typed — immediately in front of this string. The
    # text layer carries `35 THRU` where the sheet shows `Ø35 THRU`, and a bore
    # and a length are not the same thing.
    notation: str = ""


@dataclass(frozen=True)
class Sheet:
    """What one attachment turned out to be."""

    source: str
    overview: str = ""
    crops: list = field(default_factory=list)
    # The views that had to be cut up, each one whole in a single picture. Not
    # in `crops`, because a callout belongs to the piece that shows it legibly
    # and this is the picture that shows how the pieces fit (FCC-113).
    wholes: list = field(default_factory=list)
    printed: list = field(default_factory=list)
    leaders: list = field(default_factory=list)
    # One entry per view, the geometry the draughtsman drew it with.
    drawn: list = field(default_factory=list)
    # The layout picture of every page, the first of which is `overview`.
    overviews: list = field(default_factory=list)
    # What could not be done and why, in the words the user would need. A sheet
    # with no text layer is not an error; it is a sheet with no text layer.
    notes: list = field(default_factory=list)


def available() -> bool:
    """Whether a PDF can be rendered and read here at all."""
    return pymupdf is not None


# One sheet, read once. Design is called several times against the same
# attachment — the first pass, a question put back to it, an answer being
# written in — and each of those would otherwise re-render the same drawing into
# the same pictures. Keyed by what the file is, so a sheet edited between runs
# is read again and a sheet that has not changed is not.
_ALREADY_READ = {}


def read(path, into=None, legible_px=LEGIBLE_PX, max_edge=MAX_EDGE):
    """
    One attachment as the evidence it carries.

    Returns None when there is nothing this can do that the plain attachment
    did not already do — no PyMuPDF for a PDF, no Pillow for a raster, a file
    that is neither. None means "send it the old way" and is not a failure.
    """
    if not path or not os.path.isfile(path):
        return None

    key = (os.path.abspath(path), os.path.getmtime(path), os.path.getsize(path),
           legible_px, max_edge, into)
    if key in _ALREADY_READ:
        held = _ALREADY_READ[key]
        if held is None or _still_there(held):
            return held

    try:
        read_now = _read(path, into, legible_px, max_edge)
    except Exception:
        # A file that says PDF and is not one, a truncated PNG, a page that will
        # not rasterise. None of that is worth ending a run over: the attachment
        # still goes to the provider exactly as it did before this module
        # existed, and the provider gets to say whether it can read it — which
        # it already knows how to say (FCC-89).
        read_now = None
    _ALREADY_READ[key] = read_now
    return read_now


def _still_there(sheet):
    """A cached sheet whose pictures a temp sweep removed is not a cached sheet."""
    return os.path.isfile(sheet.overview) and all(
        os.path.isfile(crop.path) for crop in list(sheet.crops) + list(sheet.wholes))


def _read(path, into, legible_px, max_edge):
    into = into or tempfile.mkdtemp(prefix="fcc-sheet-")
    os.makedirs(into, exist_ok=True)
    extension = os.path.splitext(path)[1].lower()

    if extension == ".pdf":
        return _from_pdf(path, into, legible_px, max_edge) if pymupdf else None
    if extension in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return _from_raster(path, into, max_edge) if Image else None
    return None


# ── a PDF ─────────────────────────────────────────────────────────────────────


def _from_pdf(path, into, legible_px, max_edge):
    """
    Every page of the drawing, each one cut into its views and measured.

    Every page, because a sheet's second page is the rest of the same drawing
    and reading one of them is reading part of a drawing. Measured on
    `model-mania_-in-person-2023 copy.pdf`, whose page 2 prints
    `6.6 X 8.0 THRU ALL`: the recorded run asked the user how wide that slot is
    and then invented 8 mm, which is the number printed on the page nobody read.
    """
    document = pymupdf.open(path)
    notes = []
    overviews, crops, wholes, printed, leaders, drawn = [], [], [], [], [], []
    for at, page in enumerate(document, start=1):
        _a_page(page, at, len(document), into, legible_px, max_edge,
                overviews, crops, wholes, printed, leaders, drawn, notes)

    notes = list(dict.fromkeys(notes))
    if len(overviews) > 1:
        notes.append(_pages_are_not_one_sheet(len(overviews), printed))
    if printed:
        notes.append("The printed list is the sheet's own text layer, and the "
                     "numbers in it are exact. Symbols drawn as geometry are "
                     "not in that layer — `\u00d8` never is — so where "
                     "one is drawn against a number it has been recovered from "
                     "the line work and put back in front of it. A number with "
                     "no sign in front of it had none drawn against it; what it "
                     "is then is for the views to say.")
    else:
        notes.append("The sheet carries no text layer, so nothing could be "
                     "extracted and every callout has to be read off the views.")

    return Sheet(source=path, overview=overviews[0] if overviews else "",
                 overviews=overviews, crops=crops, wholes=wholes, printed=printed,
                 leaders=leaders, drawn=drawn, notes=notes)


def _a_page(page, at, of_pages, into, legible_px, max_edge,
            overviews, crops, wholes, printed, leaders, drawn, notes):
    """One page of the drawing, added to what the whole document has given."""
    words = [w for w in page.get_text("words") if str(w[4]).strip()]
    dpi = _dpi_for(words, legible_px, notes)

    overviews.append(_render(page, page.rect, _fits(page.rect, max_edge), into,
                             "sheet" if at == 1 else "sheet-%02d" % at))
    grid = _ink_grid(page, words)
    regions = _cut_on_gutters(_regions(grid, page.rect), grid)
    regions = _one_view_one_picture(regions, page.get_image_info())
    regions = _covering(regions, [tuple(w[:4]) for w in words], page.rect)

    # The region is named once and its tiles are named after it. A tile is not
    # a view — naming each one on its own gave two crops both called
    # `SECTION A-A`, and a third called `SECTION` because the label's second
    # word fell the other side of the cut.
    named = [(_on_page(name, at, of_pages), rect)
             for name, rect in zip(_names_for(regions, words, page.rect), regions)]
    for name, rect in named:
        parts = _tiled(rect, dpi, max_edge)
        if len(parts) > 1:
            # The view whole, at the highest resolution one picture of it can
            # be paid for. A view cut into three is three pictures that each
            # hold a third of a projection, and relating a circle in the plan
            # to the pair of lines in the elevation that are the same hole —
            # the one act the reading turns on — was being done across them
            # (FCC-113).
            whole = _affordable(rect)
            wholes.append(Crop(name=name, dpi=whole, page=at,
                               path=_render(page, rect, whole, into,
                                            "view-%02d" % (len(wholes) + 1)),
                               rect=_as_tuple(rect)))
        for part_at, part in enumerate(parts, start=1):
            crops.append(Crop(name=_part_named(name, part_at, len(parts)),
                              path=_render(page, part, dpi, into,
                                           "crop-%02d" % (len(crops) + 1)),
                              rect=_as_tuple(part), dpi=dpi, page=at))

    on_this_page = [crop for crop in crops if crop.page == at]
    # Where this page's own printing and leaders start, because what is compared
    # with the pixels of this page has to be the text and the leaders of this
    # page: both accumulators are the whole document's, both carry page-local
    # coordinates, and a page-2 hole would otherwise be matched to a page-1
    # leader that happens to reach the same point.
    printed_before, leaders_before = len(printed), len(leaders)
    scale, voters, measured = _measured(page, words)
    notation = _notation(page, words)
    printed.extend(Printed(text=str(w[4]).strip(),
                           at=(round(w[0], 1), round(w[1], 1)),
                           seen_in=_showing(on_this_page, w),
                           measures=measured.get(index, (None, None))[0],
                           off_by=measured.get(index, (None, None))[1],
                           notation=notation.get(index, ""))
                   for index, w in enumerate(words))
    leaders.extend(_leaders(page, words))
    printed_scale = _millimetres_per_point(words, notes)
    _the_scale_the_lines_vote_for(scale, voters, printed_scale, notes)
    circles = []
    drawn.extend(_drawn(page, named, printed_scale, page.parent, circles))
    _the_pixels_disagree(circles, printed[printed_before:], leaders[leaders_before:],
                         notes)


# What a later page says when it is a revision of an earlier one rather than
# more of it. Measured on the 2023 file, whose page 2 prints `Phase 2`,
# `MAKE CHANGES AS INDICATED IN RED REVISION CLOUDS.` and `ALL OTHER DIMENSIONS
# REMAIN THE SAME AS IN PHASE 1.`
_A_REVISION = ("REVISION", "REVISIONS", "PHASE", "REV")


def _pages_are_not_one_sheet(pages, printed):
    """
    That the drawing has several sheets, and that they may not be one part.

    Reading every page was worth doing and it brought a hazard with it: these
    competition drawings put a *revision* of the same part on page 2, so a
    description that takes its depth off one page and its height off the other
    is a part neither sheet shows. Measured on the 2023 file, where page 1 has
    the part 25 mm deep and page 2 has it 30 — a run took 30 from page 2, 108
    from page 1, and built a hybrid.

    Every crop and every callout already says which page it is on. This says
    what to do about that, and says it in the file's own words where the file
    supplies them.
    """
    said = ("The drawing has %d pages, and every view and every callout above says which "
            "one it is on. Pages of one drawing are not one sheet: a later page is very "
            "often a *revision* of the same part rather than more of it. Where two pages "
            "give the same value differently, that is a `conflicts` entry naming both "
            "pages, not a value to average or to take whichever came last — and a value "
            "read on one page may not be mixed into a part described from the other "
            "without saying so." % pages)
    revision = [entry.text for entry in printed
                if entry.text.upper().strip(".:") in _A_REVISION]
    if revision:
        said += (" This drawing's own text says as much: %s is printed on it."
                 % ", ".join("`%s`" % word for word in dict.fromkeys(revision)))
    return said


def _on_page(name, at, of_pages):
    """A view's name says which page it is on, where the drawing has more than one."""
    return name if of_pages == 1 else "%s, on page %d" % (name, at)


def _dpi_for(words, legible_px, notes):
    """
    The resolution the callouts need, which is not the resolution the sheet has.

    Set by the text that carries a digit, and deliberately by nothing else. The
    smallest type on `mm25-p1-1.pdf` is the 4.1 pt paragraph in the title block
    declaring the drawing to be the sole property of SolidWorks — 28 of the 89
    words on the sheet. Sizing the render so that sentence is legible puts every
    crop at 492 DPI and cuts the drawing into fourteen tiles to read something
    nobody models from. Sizing it by the callouts gives 279 DPI and six.

    A digit is the test because every dimension has one and no boilerplate
    depends on being read. The percentile then absorbs the odd small date or
    scale note without letting it drag the whole sheet up.
    """
    sizes = sorted(_heights(words, digits_only=True)) or sorted(_heights(words))
    if not sizes:
        return 200
    smallest = sizes[min(len(sizes) - 1, int(len(sizes) * 0.2))]
    dpi = int(math.ceil(legible_px / (smallest / 72.0)))
    if dpi > 600:
        notes.append("The callouts on this sheet are very small; the crops are "
                     "rendered at 600 DPI, which is short of what they want.")
    return max(150, min(600, dpi))


def _heights(words, digits_only=False):
    for word in words:
        if digits_only and not any(c.isdigit() for c in str(word[4])):
            continue
        height = float(word[3]) - float(word[1])
        if height > 0.5:
            yield height


def _render(page, rect, dpi, into, stem):
    rect = pymupdf.Rect(*rect) if not isinstance(rect, pymupdf.Rect) else rect
    pixmap = page.get_pixmap(dpi=int(dpi), clip=rect)
    out = os.path.join(into, "%s.png" % stem)
    pixmap.save(out)
    return out


def _fits(rect, max_edge):
    """The DPI at which a rect lands inside both the long edge and the budget."""
    longest = max(rect.width, rect.height) / 72.0
    if not longest:
        return 150
    return max(72, min(int(max_edge / longest), _affordable(rect)))


def _affordable(rect):
    """
    The highest DPI at which a rect still costs less than the provider will pay.

    Solved rather than searched, then walked down a step at a time, because the
    cost is in whole patches and the closed form is off by one wherever the
    rounding up lands. A rect this pays for is a rect that arrives at the size
    it was rendered.
    """
    if not rect.width or not rect.height:
        return 150
    dpi = int(A_PATCH * 72.0 * math.sqrt(
        THE_BUDGET / (rect.width * rect.height)))
    while dpi > 72 and _patches(rect, dpi) > THE_BUDGET:
        dpi -= 1
    return max(72, dpi)


def _patches(rect, dpi):
    """What a rect rendered at this DPI costs the provider, in visual tokens."""
    scale = dpi / 72.0
    return (math.ceil(rect.width * scale / A_PATCH)
            * math.ceil(rect.height * scale / A_PATCH))


# ── where the views are ───────────────────────────────────────────────────────


def _ink_grid(page, words):
    """
    The sheet as occupied cells: every line segment and every word.

    Segment by segment rather than path by path, because a path is not a view.
    Measured on `mm25-p1-1.pdf`, three of its twenty-five paths each span more
    than half the sheet — one drawn object holding the geometry of several views
    at once — so clustering their boxes puts the whole drawing in one region and
    changes nothing.
    """
    columns = int(page.rect.width / CELL) + 1
    rows = int(page.rect.height / CELL) + 1
    grid = [[False] * columns for _ in range(rows)]

    def mark(x, y):
        column, row = int(x / CELL), int(y / CELL)
        if 0 <= row < rows and 0 <= column < columns:
            grid[row][column] = True

    limit = FRAME * page.rect.width * page.rect.height
    for path in page.get_drawings():
        box = path["rect"]
        if box.width * box.height > limit:
            continue
        for start, end in _segments(path):
            steps = max(2, int(max(abs(end[0] - start[0]),
                                   abs(end[1] - start[1])) / CELL) + 2)
            for step in range(steps + 1):
                fraction = step / float(steps)
                mark(start[0] + (end[0] - start[0]) * fraction,
                     start[1] + (end[1] - start[1]) * fraction)

    for word in words:
        column = int(word[0] / CELL)
        while column <= int(word[2] / CELL):
            row = int(word[1] / CELL)
            while row <= int(word[3] / CELL):
                mark(column * CELL, row * CELL)
                row += 1
            column += 1
    return grid


def _segments(path):
    for item in path["items"]:
        kind = item[0]
        if kind == "l":
            yield (item[1].x, item[1].y), (item[2].x, item[2].y)
        elif kind == "re":
            box = item[1]
            corners = [(box.x0, box.y0), (box.x1, box.y0),
                       (box.x1, box.y1), (box.x0, box.y1)]
            for at in range(4):
                yield corners[at], corners[(at + 1) % 4]
        elif kind == "c":
            points = [(item[at].x, item[at].y) for at in range(1, 5)]
            for start, end in zip(points, points[1:]):
                yield start, end
        elif kind == "qu":
            quad = item[1]
            points = [(quad.ul.x, quad.ul.y), (quad.ur.x, quad.ur.y),
                      (quad.lr.x, quad.lr.y), (quad.ll.x, quad.ll.y)]
            for at in range(4):
                yield points[at], points[(at + 1) % 4]


def _regions(grid, page_rect):
    """
    The occupied cells as the handful of boxes the sheet lays out.

    Flood fill with a reach, so a dimension standing off its view joins it and
    two views a clear gap apart stay apart. Deliberately not perfect: a region
    that swallows its neighbour costs resolution and a region that splits one
    view in half costs nothing at all, because every crop is sent and the
    coverage check downstream answers for what is in them.
    """
    rows, columns = len(grid), len(grid[0])
    seen = [[False] * columns for _ in range(rows)]
    found = []

    for row in range(rows):
        for column in range(columns):
            if not grid[row][column] or seen[row][column]:
                continue
            stack, cells = [(row, column)], []
            seen[row][column] = True
            while stack:
                at_row, at_column = stack.pop()
                cells.append((at_row, at_column))
                for next_row in range(at_row - REACH, at_row + REACH + 1):
                    for next_column in range(at_column - REACH,
                                             at_column + REACH + 1):
                        if not (0 <= next_row < rows and 0 <= next_column < columns):
                            continue
                        if grid[next_row][next_column] and not seen[next_row][next_column]:
                            seen[next_row][next_column] = True
                            stack.append((next_row, next_column))
            if len(cells) < 12:  # a speck, a stray tick, an artefact
                continue
            found.append(_box(cells, page_rect))
    return _merged(found)


def _box(cells, page_rect):
    rows = [cell[0] for cell in cells]
    columns = [cell[1] for cell in cells]
    pad = CELL * 2
    return pymupdf.Rect(
        max(page_rect.x0, min(columns) * CELL - pad),
        max(page_rect.y0, min(rows) * CELL - pad),
        min(page_rect.x1, (max(columns) + 1) * CELL + pad),
        min(page_rect.y1, (max(rows) + 1) * CELL + pad))


def _merged(boxes):
    """Regions that overlap are one region: a crop inside a crop shows nothing new."""
    boxes = sorted(boxes, key=lambda box: -(box.width * box.height))
    kept = []
    for box in boxes:
        if any(_covers(other, box) for other in kept):
            continue
        kept.append(box)
    return kept


def _covers(outer, inner):
    overlap = pymupdf.Rect(inner) & outer
    if overlap.is_empty:
        return False
    area = inner.width * inner.height
    return area and (overlap.width * overlap.height) / area > 0.8


def _cut_on_gutters(regions, grid):
    """
    A region holding several views, cut apart on the sheet's own blank paper.

    The reach that makes the flood fill work is also what makes this necessary.
    A dimension stands off its view and has to be brought back to it, so the
    fill reaches three cells; but a dimension is also drawn *between* two views,
    and on the 2023 sheet the bottom view's 14 and 7 run their extension lines
    from x=194 out to x=31, straight under the elevation above them. Three cells
    of reach then chains the elevation to the bottom view and the chain never
    breaks. What arrived was one region over the whole main drawing, cut 2x2 on
    nothing but coordinates: a crop of the elevation's 65.5 and 108 lying on the
    left half of the bottom view, and no crop on the sheet that was one view.

    Cutting on the paper instead of on a grid is the difference between a rule
    the drawing supplies and a rule imposed on it. Views are laid out in rows
    and columns — that is what third-angle projection means, and the sheet
    prints the symbol saying so — so between two of them there is a strip the
    draughtsman left blank, and the strip runs the whole way across the region.
    Density is the honest signal and not colour: an extension line crossing a
    strip is a hairline that inks one cell of it, a view's body inks all of
    them, and blank is blank on a black drawing exactly as it is on this one.

    The cut is taken at the widest strip and then taken again on both halves,
    because a sheet is rows of columns and not one or the other.
    """
    cut = []
    for region in regions:
        cut.extend(_cut(region, grid))
    return cut


def _cut(rect, grid):
    first_column = max(0, int(rect.x0 / CELL))
    last_column = min(len(grid[0]), int(rect.x1 / CELL) + 1)
    first_row = max(0, int(rect.y0 / CELL))
    last_row = min(len(grid), int(rect.y1 / CELL) + 1)
    down = [any(grid[row][first_column:last_column])
            for row in range(first_row, last_row)]
    across = [any(grid[row][column] for row in range(first_row, last_row))
              for column in range(first_column, last_column)]

    at = _widest_gutter(down, first_row, rect.y0, rect.y1)
    if at is not None:
        return (_cut(pymupdf.Rect(rect.x0, rect.y0, rect.x1, at), grid)
                + _cut(pymupdf.Rect(rect.x0, at, rect.x1, rect.y1), grid))
    at = _widest_gutter(across, first_column, rect.x0, rect.x1)
    if at is not None:
        return (_cut(pymupdf.Rect(rect.x0, rect.y0, at, rect.y1), grid)
                + _cut(pymupdf.Rect(at, rect.y0, rect.x1, rect.y1), grid))
    return [rect]


def _widest_gutter(marked, first, low, high):
    """
    Where to cut a region, or None when there is nowhere honest to cut it.

    The strip has to be inside the region — a run of blank cells at either end
    is the padding every region carries — and both sides of the cut have to be
    big enough to be a view.
    """
    best, widest = None, GUTTER - 1
    start = None
    for at, inked in enumerate(list(marked) + [True]):
        if not inked:
            if start is None:
                start = at
            continue
        if start is not None and start > 0 and at < len(marked):
            middle = (first + (start + at) / 2.0) * CELL
            if (at - start > widest and middle - low >= A_VIEW
                    and high - middle >= A_VIEW):
                best, widest = middle, at - start
        start = None
    return best


def _one_view_one_picture(regions, pictures):
    """
    The fragments of one view, put back together by the picture the view is.

    A shaded view on these sheets is exported as a placed raster: measured on
    the 2023 sheet, the file carries six pictures and the sheet carries six
    views, and the top view's outline is not in the vector content at all. The
    flood fill therefore had that view's centrelines and one callout to work
    from and returned it as two fragments sixty-eight points square and a
    hundred and fifty points apart — each a crop of part of a view, neither of
    them the view, and a dimension read in one of them belonging to a projection
    that had no name.

    A placed picture is a view's body, which is the same density argument as
    everywhere else here and not a colour one; an ordinary black drawing carries
    its bodies as line work, has no pictures, and comes through this untouched.
    """
    boxes = [pymupdf.Rect(*picture["bbox"]) for picture in pictures]
    held, loose = {}, []
    for region in regions:
        at = _the_picture_holding(boxes, region)
        if at is None:
            loose.append(region)
            continue
        held.setdefault(at, pymupdf.Rect(boxes[at]))
        held[at] |= region
    return loose + list(held.values())


def _the_picture_holding(boxes, region):
    """Which picture a region is part of, which is the one holding most of it."""
    best, most = None, HELD_BY_A_PICTURE
    for at, box in enumerate(boxes):
        share = _share_of(box, region)
        if share > most:
            best, most = at, share
    return best


def _share_of(box, region):
    """How much of a region a box holds, as a fraction of the region."""
    overlap = pymupdf.Rect(region) & box
    if overlap.is_empty:
        return 0.0
    area = region.width * region.height
    return (overlap.width * overlap.height) / area if area else 0.0


def _covering(regions, boxes, page_rect):
    """
    Every printed word inside some region, whatever the flood fill decided.

    The coverage rule is the one the whole module answers to, so it is enforced
    rather than assumed: a callout the regions missed gets a region of its own
    instead of being quietly absent from every crop — which is the failure this
    replaces, arriving in a new place.
    """
    regions = list(regions)
    for box in boxes:
        word = pymupdf.Rect(*box)
        if any(_covers(region, word) for region in regions):
            continue
        regions.append(pymupdf.Rect(
            max(page_rect.x0, word.x0 - 60), max(page_rect.y0, word.y0 - 45),
            min(page_rect.x1, word.x1 + 60), min(page_rect.y1, word.y1 + 45)))
    return _merged(regions)


def _tiled(rect, dpi, max_edge):
    """
    One region as however many crops keep it both whole and legible.

    A region wider than the cap is not sent smaller — that is the trade this
    module refuses. It is sent in overlapping pieces, because the resolution is
    the point and the frame is not.
    """
    scale = dpi / 72.0
    across = max(1, int(math.ceil(rect.width * scale / max_edge)))
    down = max(1, int(math.ceil(rect.height * scale / max_edge)))
    # And again by what the provider will pay for, which is the cap that bites
    # first: a piece inside the edge limit and over the patch budget is a piece
    # resized at the far end, and the whole point of cutting is not to be.
    while _patches(pymupdf.Rect(0, 0, rect.width / across * (1 + 2 * OVERLAP),
                                rect.height / down * (1 + 2 * OVERLAP)),
                   dpi) > THE_BUDGET:
        if rect.width / across >= rect.height / down:
            across += 1
        else:
            down += 1
    if across == 1 and down == 1:
        return [rect]

    width = rect.width / across
    height = rect.height / down
    pieces = []
    for row in range(down):
        for column in range(across):
            pieces.append(pymupdf.Rect(
                max(rect.x0, rect.x0 + column * width - width * OVERLAP),
                max(rect.y0, rect.y0 + row * height - height * OVERLAP),
                min(rect.x1, rect.x0 + (column + 1) * width + width * OVERLAP),
                min(rect.y1, rect.y0 + (row + 1) * height + height * OVERLAP)))
    return pieces


_LABELS = ("SECTION", "DETAIL", "VIEW")

# How far apart two words can be and still be the same printed line, in points.
# Measured on the 2023 sheet: `SECTION` and `A-A` stand 3.1 pt apart, and the
# nearest thing sharing that baseline anywhere else on the sheet is a hundred
# points away. Wide enough for a word space, far short of a second column.
SAME_LINE = 12.0

# How far below its own view a label is printed, in points. Measured on the
# 2023 sheet: `SECTION A-A` begins 0.3 pt under the bottom edge of the region
# holding the section it names. Kept short deliberately — the next thing down
# the sheet from a section is the title block, and a tolerance generous enough
# to be safe would reach it, which is the misnaming this replaces.
LABEL_STANDS_UNDER = 24.0


def _names_for(regions, words, page_rect):
    """
    What to call each region: the sheet's own label where the sheet prints one.

    `SECTION A-A` is what the description's `seen_in` says and what the user
    says back at the Plan Gate, so a crop that carries the label should be
    called by it rather than by a position somebody has to translate. Taking
    the first `_LABELS` word printed anywhere inside a region gets that wrong
    twice over on the 2023 sheet, and both ways are expensive: a note naming a
    view somewhere else names the region it sits in, and a label printed under
    its view names whatever region it lands in instead.

    So the labels are found first, on the sheet, and then handed to the views
    they belong to — rather than each region asking what text happens to fall
    inside it. A region that collects two labels is called by both of them,
    because a region holding two labelled views is a fact worth saying and
    naming it after one of them is a lie about where a dimension was read.
    """
    held = [[] for _ in regions]
    for label, box in _captions(words):
        at = _the_view_labelled_by(regions, box)
        if at is not None:
            held[at].append(label)

    corners = [None if labels else _where(regions[at], page_rect, 0)
               for at, labels in enumerate(held)]
    named = []
    for at, labels in enumerate(held):
        if labels:
            named.append(" and ".join(labels))
            continue
        sharing = sorted((other for other, corner in enumerate(corners)
                          if corner == corners[at]),
                         key=lambda other: (regions[other].y0, regions[other].x0))
        named.append(_where(regions[at], page_rect,
                            0 if len(sharing) == 1 else sharing.index(at) + 1))
    return named


def _captions(words):
    """
    The labels the draughtsman printed, told apart from the notes that name one.

    A label is set on a line of its own — `SECTION A-A`, `VIEW B`, `DETAIL A`,
    the label word and its designator and nothing else beside them. Measured on
    the 2023 sheet, `SEE VIEW B FOR CLARITY OF CUTOUT` is six words on three
    lines, with `SEE` printed in front of `VIEW` on the first of them, and that
    word in front is the whole difference between a label and a note. Read as a
    label it named all four crops of the main drawing after a view 500 pt away
    on the other edge of the sheet.
    """
    lines = {}
    for word in words:
        lines.setdefault(round(float(word[1]), 1), []).append(word)
    for line in lines.values():
        for run in _runs(sorted(line, key=lambda word: float(word[0]))):
            if not 1 <= len(run) <= 2:
                continue
            if str(run[0][4]).strip().upper() not in _LABELS:
                continue
            yield (" ".join(str(word[4]).strip() for word in run),
                   pymupdf.Rect(min(float(word[0]) for word in run),
                                min(float(word[1]) for word in run),
                                max(float(word[2]) for word in run),
                                max(float(word[3]) for word in run)))


def _runs(line):
    """One baseline as the separate pieces of text set on it."""
    run = []
    for word in line:
        if run and float(word[0]) - float(run[-1][2]) > SAME_LINE:
            yield run
            run = []
        run.append(word)
    if run:
        yield run


def _the_view_labelled_by(regions, box):
    """
    Which region a label is the name of: the view it is printed under.

    A section label is set below its section, and on the 2023 sheet that puts
    `SECTION A-A` 0.3 pt outside the region holding the section and 8 pt inside
    the title block's — so the region enclosing the label is the wrong answer,
    and it is the wrong answer on the drawing that matters. The view above the
    label is preferred. Enclosure is the fallback, for the sheets that set the
    label inside the view's own box, which `mm25-p1-1.pdf` does for both of
    its sections.
    """
    middle = (box.x0 + box.x1) / 2
    above = [at for at, region in enumerate(regions)
             if region.x0 <= middle <= region.x1
             and 0 <= box.y0 - region.y1 <= LABEL_STANDS_UNDER]
    if above:
        return max(above, key=lambda at: regions[at].y1)
    inside = [at for at, region in enumerate(regions)
              if region.intersects(box)]
    if inside:
        return max(inside, key=lambda at: regions[at].width * regions[at].height)
    return None


def _part_named(name, at, of):
    """A tile says which part of its view it is, so `seen_in` still names a view."""
    return name if of == 1 else "%s, part %d of %d" % (name, at, of)


# What to call each of several regions in one corner of the sheet, in the order
# a page is read. Words rather than a number because the name is read aloud at
# the Plan Gate and cited in a sentence.
_IN_ORDER = ("the first one", "the second one", "the third one",
             "the fourth one", "the fifth one", "the sixth one")


def _where(rect, page_rect, ordinal):
    """
    A region with no label of its own: where it is, and which one it is there.

    A crop's name is what `seen_in` cites, so two crops with one name is a
    citation that names two pictures and settles nothing. Measured on the 2023
    sheet: the bottom view lands at (20, 384)-(524, 600) and the notes beside
    it at (396, 540)-(616, 608), and both of them are the lower centre; on
    `mm25-p1-1.pdf` the top view and the fillet note are both the upper left.

    The ordinal is the region's place among the ones that share its corner,
    counted the way a page is read, and nought means it is the only one there
    and wants no number.
    """
    middle_x = (rect.x0 + rect.x1) / 2 - page_rect.x0
    middle_y = (rect.y0 + rect.y1) / 2 - page_rect.y0
    down = ("the upper", "the middle", "the lower")[
        min(2, int(3 * middle_y / max(1.0, page_rect.height)))]
    across = ("left", "centre", "right")[
        min(2, int(3 * middle_x / max(1.0, page_rect.width)))]
    corner = "%s %s" % (down, across) if across != "centre" else "%s centre" % down
    if not ordinal:
        return "%s of the sheet" % corner
    return "%s of the sheet, %s" % (corner, _IN_ORDER[ordinal - 1]
                                    if ordinal <= len(_IN_ORDER)
                                    else "number %d of them" % ordinal)


def _showing(crops, word):
    """Which crop a callout is legible in, named the way the crop is named."""
    box = pymupdf.Rect(*word[:4])
    for crop in crops:
        if _covers(pymupdf.Rect(*crop.rect), box):
            return crop.name
    return ""


# ── the leaders ───────────────────────────────────────────────────────────────

# How close a segment end has to be to a callout to be its leader, in points.
_TOUCHES = 14.0
# How far a leader is followed before it is called a leader that goes nowhere.
_HOPS = 12


def _leaders(page, words):
    """
    Where a callout's leader ends up, as geometry rather than as eyesight.

    "The arrowhead says which feature the callout is of; where the leader starts
    says where its centre is" is the hardest thing the description asks for, and
    it is asked of a 14 px picture of a hairline. In a vector PDF the hairline
    is a list of endpoints, so the far end of the line is arithmetic.

    Reported, never concluded. This says a line runs from this callout to this
    point; whether the point is a bore wall or a pocket floor is the reading,
    and the reading stays with the phase that can see it.
    """
    ends = []
    limit = FRAME * page.rect.width * page.rect.height
    for path in page.get_drawings():
        if path["rect"].width * path["rect"].height > limit:
            continue
        ends.extend(_segments(path))

    traced = []
    for word in words:
        text = str(word[4]).strip()
        if not _a_callout(text):
            continue
        anchor = pymupdf.Rect(*word[:4])
        start = _nearest_end(ends, anchor)
        if start is None:
            continue
        far = _followed(ends, start)
        if far and _distance(far, ((anchor.x0 + anchor.x1) / 2,
                                   (anchor.y0 + anchor.y1) / 2)) > _TOUCHES:
            traced.append({"callout": text,
                           "printed_at": (round(anchor.x0, 1), round(anchor.y0, 1)),
                           "its_leader_reaches": (round(far[0], 1), round(far[1], 1))})
    return traced


def _a_callout(text):
    return any(character.isdigit() for character in text)


def _nearest_end(ends, anchor):
    """
    The segment a leader starts with: the shoulder drawn against the text.

    Measured to the edge of the text box and not to the middle of it. A leader
    is drawn as a short horizontal shoulder touching the string, and then the
    slope. Measured from the centre, `(R30)` on `mm25-p1-1.pdf` picks up a 17 pt
    arrowhead stem belonging to a different dimension 12 pt away and follows it
    nowhere; measured to the box, the shoulder lands exactly on the edge at zero
    and the chain runs out to the pocket wall the arrow is actually touching.
    """
    best, best_at = None, _TOUCHES
    for start, end in ends:
        for point, other in ((start, end), (end, start)):
            gap = _gap_to(anchor, point)
            # A tie on the box edge is broken by reach: the shoulder belongs to
            # the leader, the tick belongs to whatever it was ticking.
            if gap < best_at or (best and abs(gap - best_at) < 0.5
                                 and _distance(point, other) > _distance(*best)):
                best, best_at = (point, other), min(gap, best_at)
    return best


def _gap_to(rect, point):
    """How far a point is from a box, and zero when it is on or inside it."""
    across = max(rect.x0 - point[0], 0.0, point[0] - rect.x1)
    down = max(rect.y0 - point[1], 0.0, point[1] - rect.y1)
    return math.hypot(across, down)


def _followed(ends, start):
    """The far end of the polyline a leader is, hop by hop, without doubling back."""
    here, came_from = start[1], start[0]
    for _ in range(_HOPS):
        forward = None
        for segment_start, segment_end in ends:
            for point, other in ((segment_start, segment_end),
                                 (segment_end, segment_start)):
                if _distance(point, here) < 1.0 and _distance(other, came_from) > 1.0:
                    forward = other
                    break
            if forward:
                break
        if forward is None:
            return here
        came_from, here = here, forward
    return here


def _distance(one, other):
    return math.hypot(one[0] - other[0], one[1] - other[1])


# ── the line work ─────────────────────────────────────────────────────────────

# What a chained point may be off a straight line, or off a circle, and still
# be part of it, in mm. A hairline is drawn 0.18 mm wide, so anything inside
# this is inside the line the draughtsman drew.
A_HAIR = 0.05

# The shortest entity worth reporting, in mm. Below it are arrowheads, the
# ticks on a centre line and the ends of hatch strokes — thousands of them on
# a sheet, and none of them a face, an axis or a contour.
A_MARK = 2.0

# How many entities one view is reported by, longest first. A view is a few
# dozen real edges and a tail of hatching; the tail is what the cap takes off,
# and a view that lost any says how many.
ENOUGH = 120


def _drawn(page, named, millimetres_per_point, document=None, circles=None):
    """
    Each view's own geometry, in millimetres of the part rather than in points.

    The reader has always handed over the sheet's *text* and never the sheet's
    *lines*, and a drawing says most of what it says in lines. The recorded run
    (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`, the 2023 pipe
    clamp) asked the user how high the arm meets the ring, was told the views do
    not say, and assumed it centred on the ring axis — 7.5 mm above it. The file
    has the answer to a hundredth of a millimetre: the ring's centre line is
    drawn at y=247.9 pt and the arm's two faces, which the `15` measures
    between, at 276.3 and 318.8, so the arm's top face is 10.0 mm *below* the
    axis and its bottom face is flush with the ring's lowest tangent. The part
    was built 17.5 mm out on a number nobody had to invent.

    Reported, never concluded, the same way the leaders are. This says a line
    runs from here to there and an arc has this radius about this centre; what
    is a face, what is a centre line and what is an extension line stays with
    the phase that can see the view.

    Where a view's body was placed as a picture — which is how these sheets
    export a shaded view — the outline is not in the file's line work at all,
    and that view says so rather than presenting its dimension lines as the
    part. Even then the annotation is worth having: an extension line is drawn
    from the very face its dimension measures, and a centre line is an axis.
    """
    limit = FRAME * page.rect.width * page.rect.height
    pictures = [pymupdf.Rect(*picture["bbox"]) for picture in page.get_image_info()]

    drawn = []
    for name, rect in named:
        centres = _centres_in(page, rect, limit, millimetres_per_point)
        found = []
        for run in _chained(_segments_in(page, rect, limit, dashed=False)):
            found.extend(_fitted([_in_millimetres(point, rect,
                                                  millimetres_per_point)
                                  for point in run]))
        traced, found_circles = _traced(document, page, rect,
                                        millimetres_per_point) \
            if document is not None else ([], [])
        if circles is not None:
            circles.extend(found_circles)
        found.sort(key=lambda entity: -entity[0])
        centres.sort(key=lambda entity: -entity[0])
        traced.sort(key=lambda entity: -entity[0])
        drawn.append(Drawn(
            view=name,
            entities=[said for _, said in centres]
                     + [said for _, said in traced]
                     + [said for _, said in found[:ENOUGH]],
            left_out=max(0, len(found) - ENOUGH),
            from_a_picture=any(_share_of(box, rect) > HELD_BY_A_PICTURE
                               for box in pictures)))
    return drawn


def _centres_in(page, rect, limit, millimetres_per_point):
    """
    The centre lines of one view, taken path by path rather than chained.

    Chaining is wrong here and quietly loses them. A centre line is drawn with
    a dash pattern, so its pieces do not touch, and `_chained` — which joins a
    polyline end to end — turns one bolt circle into a hundred and sixty-five
    fragments two millimetres long, every one of them below the shortest entity
    worth reporting. The 2023 sheet's bolt circle disappeared exactly that way.

    A dashed path is one object and is read as one: a circle where its points
    lie on one, and otherwise the line between its two ends.
    """
    numpy = _numpy()
    view = _as_tuple(rect)
    said = []
    for path in page.get_drawings():
        box = path["rect"]
        if box.width * box.height > limit or not _is_dashed(path):
            continue
        points = []
        for start, end in _segments(path):
            if _holds(view, start) and _holds(view, end):
                points += [start, end]
        if len(points) < 2:
            continue
        into = [_in_millimetres(point, rect, millimetres_per_point)
                for point in points]

        if numpy is not None and len(into) >= 12 and box.width and box.height:
            circle = _circle_through(numpy.array(into, dtype=float))
            if circle:
                (x, y), radius = circle
                said.append((2 * radius, "centre arc R%.2f centred %s"
                             % (radius, _at((x, y)))))
                continue
        ends = max(((one, other) for at, one in enumerate(into)
                    for other in into[at + 1:]),
                   key=lambda pair: _distance(*pair))
        said.append((_distance(*ends),
                     "centre line %s to %s" % (_at(ends[0]), _at(ends[1]))))
    return said


def _segments_in(page, rect, limit, dashed=None):
    """
    Every line segment of the sheet that lies wholly inside one view.

    `dashed` selects by the pattern the line was drawn with: True for the
    centre lines, False for everything else, None for both. The pattern is in
    the file and is the only thing that says a line is an axis rather than an
    edge, so it is not something to recover by eye later.
    """
    view = _as_tuple(rect)
    inside = []
    for path in page.get_drawings():
        box = path["rect"]
        if box.width * box.height > limit:
            continue
        if dashed is not None and _is_dashed(path) is not dashed:
            continue
        for start, end in _segments(path):
            if _holds(view, start) and _holds(view, end):
                inside.append((start, end))
    return inside


def _is_dashed(path):
    """
    Whether this path was drawn as a centre line.

    A PDF dash pattern is a string like `[ 43.2 3.6 3.6 3.6 ] 16.6` — the array
    is the pattern and the number is the phase. An empty array is a solid line.
    Both patterns this draughtsman uses are long-dash-short-dash, which is the
    ISO chain line, which is a centre line: an axis, or a plane of symmetry.
    """
    pattern = path.get("dashes") or ""
    opened, closed = pattern.find("["), pattern.find("]")
    if opened < 0 or closed < opened:
        return False
    return bool(pattern[opened + 1:closed].strip())


def _chained(segments, snap=0.05):
    """
    The segments as the polylines they were drawn as.

    A PDF carries no arcs — every curve arrives flattened into a run of very
    short straight pieces — so an arc can only be recovered from the run, and
    the run only exists once the pieces are joined end to end. A junction where
    three or more lines meet ends the chain: that is a corner of the drawing
    and not a curve continuing.
    """
    key = lambda point: (round(point[0] / snap), round(point[1] / snap))
    edges, meeting = [], {}
    for start, end in segments:
        if key(start) == key(end):
            continue
        meeting.setdefault(key(start), []).append((len(edges), 0))
        meeting.setdefault(key(end), []).append((len(edges), 1))
        edges.append([start, end, False])

    def onward(points):
        while True:
            here, before = points[-1], points[-2]
            joined = meeting.get(key(here), [])
            if len(joined) != 2:
                return points
            forward = None
            for at, which in joined:
                start, end, taken = edges[at]
                other = end if which == 0 else start
                if taken or key(other) == key(before):
                    continue
                edges[at][2] = True
                forward = other
                break
            if forward is None:
                return points
            points.append(forward)

    chains = []
    for at, edge in enumerate(edges):
        if edge[2]:
            continue
        edge[2] = True
        back = onward([edge[1], edge[0]])
        chains.append(list(reversed(back[2:])) + onward([edge[0], edge[1]]))
    return chains


def _fitted(points, tolerance=A_HAIR):
    """
    One polyline as the lines and arcs it is, longest run first.

    Greedy and in order: a straight run is taken as far as it goes, and where
    the next points are not straight a circle is fitted through them and taken
    as far as *it* goes. Nothing is smoothed — a run that is neither is emitted
    as the short lines it was drawn as, because this reports the file rather
    than tidying it.
    """
    found, at = [], 0
    while at < len(points) - 1:
        end = at + 1
        while end + 1 < len(points) and all(
                _off_the_line(points[at], points[end + 1], points[step]) <= tolerance
                for step in range(at + 1, end + 1)):
            end += 1
        if end - at >= 2:
            found.append(_a_line(points[at], points[end]))
            at = end
            continue

        arc = _an_arc(points, at, tolerance)
        if arc:
            found.append(arc[0])
            at = arc[1]
            continue

        found.append(_a_line(points[at], points[at + 1]))
        at += 1
    return [entity for entity in found if entity[0] >= A_MARK]


def _a_line(start, end):
    return (_distance(start, end),
            "line %s to %s" % (_at(start), _at(end)))


def _an_arc(points, at, tolerance):
    """The arc starting at *at*, as far as one circle carries it, or None."""
    if at + 2 >= len(points):
        return None
    fitted = _through(points[at], points[at + 1], points[at + 2])
    if not fitted:
        return None

    centre, radius, end = fitted[0], fitted[1], at + 2
    while end + 1 < len(points):
        again = _through(points[at], points[(at + end + 1) // 2], points[end + 1])
        if not again:
            break
        if any(abs(_distance(points[step], again[0]) - again[1]) > tolerance
               for step in range(at, end + 2)):
            break
        centre, radius, end = again[0], again[1], end + 1

    if end - at < 2:
        return None
    swept = radius * abs(_angle(centre, points[at]) - _angle(centre, points[end]))
    return ((swept, "arc R%.2f centred %s, from %s to %s"
             % (radius, _at(centre), _at(points[at]), _at(points[end]))), end)


def _through(one, two, three):
    """The circle through three points, or None where they are in a line."""
    (ax, ay), (bx, by), (cx, cy) = one, two, three
    twice = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(twice) < 1e-9:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    x = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / twice
    y = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / twice
    return (x, y), math.hypot(ax - x, ay - y)


def _off_the_line(start, end, point):
    across, down = end[0] - start[0], end[1] - start[1]
    span = math.hypot(across, down)
    if span < 1e-9:
        return _distance(point, start)
    return abs(across * (start[1] - point[1]) - down * (start[0] - point[0])) / span


def _angle(centre, point):
    return math.atan2(point[1] - centre[1], point[0] - centre[0])


def _in_millimetres(point, rect, millimetres_per_point):
    """
    A point of the page as a point of the view, in the part's own millimetres.

    Measured from the view's own lower left corner and with y running up, which
    is how every dimension on the sheet is read and the opposite of how a PDF
    counts. A coordinate in page points would have to be converted by whoever
    used it, and the conversion is the arithmetic this exists to remove.
    """
    return ((point[0] - rect.x0) * millimetres_per_point,
            (rect.y1 - point[1]) * millimetres_per_point)


def _at(point):
    return "(%.2f, %.2f)" % point


# What the title block calls the scale, so the ratio beside it can be found.
_SCALE = ("SCALE:", "SCALE")


def _millimetres_per_point(words, notes):
    """
    How much of the part one point of the page is, off the sheet's own scale.

    A PDF page is in points at 72 to the inch, so a drawing plotted 1:1 puts
    0.3528 mm of the part in every point — verified on the 2023 sheet, where the
    arm's 15 mm measures 42.5 pt between the extension lines of its own
    dimension. A sheet plotted 1:2 draws the same part half size, and the ratio
    it prints in its title block is the only thing that says so.
    """
    ratio, said = 1.0, "1:1"
    for at, word in enumerate(words):
        if str(word[4]).strip().upper() not in _SCALE:
            continue
        for other in words[at:at + 3]:
            drawn, _, real = str(other[4]).strip().partition(":")
            try:
                if float(drawn) > 0 and float(real) > 0:
                    ratio, said = float(real) / float(drawn), str(other[4]).strip()
            except ValueError:
                continue
    notes.append("The line work is measured at the scale the sheet prints, %s, "
                 "so one point of the page is %.4f mm of the part."
                 % (said, (25.4 / 72.0) * ratio))
    return (25.4 / 72.0) * ratio


# ── what the sheet measures of itself ─────────────────────────────────────────

# How far apart two segments' angles may be and still be the same drawn line,
# in degrees. A dimension line is plotted as one angle; a degree of slack is
# rounding in the file rather than a second line.
A_TILT = 1.0

# How far apart two segments may sit across that line and still be it, in
# points. Wider and the extension line beside a dimension would join it.
A_SIDESTEP = 1.0

# How wide a gap along the line is still the same line, in points. This is the
# one number that makes a span a span: a dimension line is drawn in two pieces
# with the number sitting in the break between them, and `_chained` deliberately
# refuses to cross a break of any width. Measured on the 2023 sheet, where the
# 108's dimension line is broken by 31 pt of paper for a 22 pt string.
THE_TEXT_GAP = 60.0

# The shortest span worth measuring anything by, in points. Below it are
# arrowheads and the ticks on a centre line.
A_SPAN = 8.0

# How far a number may sit off its own dimension line and still be its number,
# in points. A dimension is set just clear of the line it measures; 14 pt is
# one line of this sheet's 9.5 pt text plus its standoff.
BESIDE = 14.0

# How far in from a span's ends the number has to sit, as a share of the span.
# A number is centred on its dimension line; a string near the end of a long
# span belongs to something else that happens to be collinear with it.
WITHIN = 0.15

# How close two votes on the scale have to be to be the same vote. Two per cent
# of 0.7 mm/pt is 0.014, and the four sheets measured here vote inside 0.0002 of
# each other, so this is loose by two orders and still separates a true pairing
# from a false one.
AGREED = 0.02

# How far a span's measurement may be from the number printed against it and
# still be that number's measurement, in mm. Measured: on all four sheets a true
# pairing lands inside 0.05 mm and a false one lands 7 to 100 mm out. The band
# is not a tuned threshold; it is the empty ground between two populations.
TRUE_TO = 0.6


def _spans(page):
    """
    The sheet's drawn lines as the lines they are, across the breaks in them.

    Sibling of `_chained` and its opposite. `_chained` follows a polyline and
    stops at a junction, because a corner is a corner and not a curve
    continuing; that is the right rule for a contour and the wrong one for a
    dimension. A dimension line is drawn as two collinear pieces with the number
    sitting in the gap, so the thing that has to be measured is precisely the
    thing `_chained` refuses to join.

    So this groups by the line itself — angle and standoff — and then bridges
    gaps up to `THE_TEXT_GAP`. What comes back is not geometry of the part. It
    is the draughtsman's own measuring apparatus, and its length is a number he
    committed to.
    """
    limit = FRAME * page.rect.width * page.rect.height
    lines = {}
    for path in page.get_drawings():
        box = path["rect"]
        if box.width * box.height > limit:
            continue
        for start, end in _segments(path):
            along = _distance(start, end)
            if along < 0.5:
                continue
            angle = math.degrees(math.atan2(end[1] - start[1],
                                            end[0] - start[0])) % 180.0
            across = math.radians(angle)
            forward = (math.cos(across), math.sin(across))
            standoff = -forward[1] * start[0] + forward[0] * start[1]
            at = (forward[0] * start[0] + forward[1] * start[1],
                  forward[0] * end[0] + forward[1] * end[1])
            lines.setdefault((round(angle / A_TILT), round(standoff / A_SIDESTEP)),
                             []).append((min(at), max(at), angle, standoff))

    spans = []
    for pieces in lines.values():
        pieces.sort()
        run = None
        for first, last, angle, standoff in pieces:
            if run is None:
                run = [first, last, angle, standoff]
            elif first - run[1] <= THE_TEXT_GAP:
                run[1] = max(run[1], last)
            else:
                spans.append(_a_span(*run))
                run = [first, last, angle, standoff]
        if run:
            spans.append(_a_span(*run))
    return [span for span in spans if span[0] >= A_SPAN]


def _a_span(first, last, angle, standoff):
    """One span as its length, its direction and where it starts."""
    across = math.radians(angle)
    forward = (math.cos(across), math.sin(across))
    sideways = (-forward[1], forward[0])
    return (last - first, forward,
            (forward[0] * first + sideways[0] * standoff,
             forward[1] * first + sideways[1] * standoff))


def _a_number(text):
    """
    What a printed string is worth as a length, or None if it is not one.

    `Ø` never arrives — it is drawn as geometry — so a bore's callout is the
    bare number, and `(R30)` and `R2 TYP` carry theirs with a letter and
    brackets around it. Only the leading number is taken: `6.6 X 8.0 THRU` is
    measured, if it is measured at all, by the 6.6.
    """
    said = str(text).strip().lstrip("(").lstrip("\u00d8R")
    said = said.split()[0] if said.split() else ""
    said = said.rstrip(")")
    try:
        worth = float(said)
    except ValueError:
        return None
    return worth if worth > 0 else None


def _the_span_measuring(spans, word):
    """
    The span a number is printed against: nearest line that brackets it.

    Nearest, not shortest. A number sits centred on the line that measures it,
    and the standoff is the smallest thing about it — measured on the 2025
    sheets, preferring the shorter of two candidate spans loses the pairing
    the sheet actually drew.
    """
    middle = ((word[0] + word[2]) / 2.0, (word[1] + word[3]) / 2.0)
    best, best_at = None, BESIDE
    for along, forward, start in spans:
        away = (middle[0] - start[0], middle[1] - start[1])
        standoff = abs(-forward[1] * away[0] + forward[0] * away[1])
        down = forward[0] * away[0] + forward[1] * away[1]
        if standoff <= best_at and WITHIN * along <= down <= (1 - WITHIN) * along:
            best, best_at = along, standoff
    return best


def _measured(page, words):
    """
    Every printed number beside the length of the line that measures it.

    This is the whole of the answer to ADR-0019, and it contains no reading.
    The string is the draughtsman's own; the span is the draughtsman's own; the
    scale is voted on by the pairs of them. A run that writes 100 where the
    sheet prints 108 now contradicts 8 mm the sheet said about itself, in a
    number nobody had to see.

    Returns the millimetres one point is worth, the number of pairings that
    voted for it, and each measured word's own millimetres.
    """
    spans = _spans(page)
    votes = []
    for at, word in enumerate(words):
        worth = _a_number(word[4])
        if worth is None:
            continue
        along = _the_span_measuring(spans, word)
        if along:
            votes.append((at, worth, along, worth / along))

    if not votes:
        return None, 0, {}

    agreed = max((([vote for vote in votes
                    if abs(vote[3] - one[3]) <= AGREED * one[3]])
                  for one in votes), key=len)
    scale = sum(vote[3] for vote in agreed) / len(agreed)

    measured = {}
    for at, worth, along, _ in votes:
        millimetres = along * scale
        if abs(millimetres - worth) <= TRUE_TO:
            measured[at] = (round(millimetres, 2), round(millimetres - worth, 2))
    return scale, len(agreed), measured


def _the_scale_the_lines_vote_for(scale, voters, printed_scale, notes):
    """
    What the sheet's own dimension lines make of the scale its title block prints.

    Checked, never substituted. The title block is a statement of intent and the
    lines are what was plotted, and on all four sheets measured they agree to
    four figures — but a sheet re-plotted at another scale with its title block
    left alone is a silent failure mode, and this is the only thing that would
    notice it.
    """
    if scale is None or voters < 3:
        return
    notes.append("The scale is checked against its own dimension lines: %d of "
                 "them, measured against the numbers printed on them, make one "
                 "point %.4f mm of the part, where the title block makes it "
                 "%.4f mm."
                 % (voters, scale, printed_scale)
                 + (" They agree." if abs(scale - printed_scale)
                    <= AGREED * printed_scale else
                    " They disagree, and nothing here settles which is right."))


# What the diameter sign measures against the line of text it stands in, as a
# share of that line's height. Measured on `mm25-p1-1.pdf`, whose 12.9 pt text
# carries a 6.3 x 7.7 pt sign: half as wide as the line is tall, and six tenths
# as high. The band is wide enough for another draughtsman's font and far too
# narrow for a numeral, an arrowhead or a leader.
A_SIGN = (0.35, 0.75, 0.40, 0.80)

# How far in front of the numeral the sign may stand, as a share of the line's
# height. It stands against the number, not merely somewhere to its left:
# measured, the gap is 2.1 pt on a 12.9 pt line, and a sign 26 pt out is the
# sign belonging to the number before it — `Ø6.6 X 8.0` would otherwise make
# the 8.0 a diameter too.
A_SIGN_STANDS_OFF = 0.6

# How many pieces the sign is flattened into. A circle in a PDF is a flattened
# curve — 47 segments here — and this is what tells the diameter sign apart
# from the depth sign, which is four straight lines in a box the same size and
# which the 2023 sheet prints against a `6` that is a depth and not a bore.
A_SIGN_IS_A_CURVE = 12


def _notation(page, words):
    """
    The symbols the sheet draws rather than types, by the word they stand against.

    `Ø` is not in the text layer at all — it is geometry, and the module has
    always said so and stopped there. Saying the sign is missing leaves the
    reading to a picture of a 6 pt glyph; recovering it is arithmetic on a box
    the file gives us, and the difference it makes is the difference between a
    bore and a length.

    Only the diameter sign. `R` survives into the text layer on this
    draughtsman's sheets and needs nothing, and a rule that guessed at more
    symbols than it can measure would be inventing notation rather than
    reading it.
    """
    signs = []
    for path in page.get_drawings():
        box = path["rect"]
        if len(path["items"]) >= A_SIGN_IS_A_CURVE and box.width and box.height:
            signs.append(box)

    against = {}
    for at, word in enumerate(words):
        if _a_number(word[4]) is None:
            continue
        line = word[3] - word[1]
        if line <= 0:
            continue
        low, high, shallow, deep = A_SIGN
        for box in signs:
            if not (low * line <= box.width <= high * line
                    and shallow * line <= box.height <= deep * line):
                continue
            if (word[0] - A_SIGN_STANDS_OFF * line <= box.x1 <= word[0] + 1
                    and box.y0 < word[3] and box.y1 > word[1]):
                against[at] = "\u00d8"
                break
    return against


# ── the picture a view was placed as ──────────────────────────────────────────

# How far from white a pixel has to be to be the part rather than the paper.
# Measured off the picture's own corners rather than assumed, the same way the
# raster path measures its paper, because a view exported on a dark background
# is a view whose paper is dark.
A_SHADE = 40

# The smallest hole worth measuring, in pixels of the placed picture. Below this
# is the anti-aliasing along an edge and the white of a numeral sitting on the
# view, neither of which is a bore. At 186 DPI a 2 mm hole is 15 px across and
# covers 170 px, so this keeps everything a drawing would dimension.
A_HOLE = 120

# How far a hole's boundary may sit from the circle fitted through it, as a
# share of that circle's radius, and still be a circle. Measured: a true bore
# lands inside 0.02 and a slot or a pocket lands 0.3 and worse, so this is the
# empty ground between two populations rather than a tuned threshold.
ROUND = 0.06

# How far a traced diameter may be from the callout printed against it before
# the two are said to disagree, in mm. Measured rather than chosen, and not
# symmetrical about zero for a reason: a hole is measured to the inside of the
# line that draws it, so the tracing reads low — Ø33.8 to Ø34.6 where the sheet
# prints 35, and Ø38.9 where it prints 40. The bias is a millimetre and the
# spread around it is half of one, so two millimetres is outside anything the
# tracing does on its own and inside every misread digit that matters.
TOO_FAR = 2.0


def _traced(document, page, rect, millimetres_per_point):
    """
    What the picture holding a view says about the part, in millimetres.

    These sheets export every view as a placed raster — 186 DPI, measured on all
    four — so the outline exists only as pixels and no render of the page at any
    DPI recovers it. That is the shape half of ADR-0019: the numbers are exact
    in the text layer and the *contour* is not anywhere at all.

    A picture at a known scale is not a picture, though. It is a measurement
    nobody has taken yet. The holes in it are the paper the part does not cover,
    a circle is fitted through each one's boundary as a whole rather than
    greedily along it — the greedy fit `_fitted` uses is right for a
    draughtsman's own polyline and wrong for a pixel edge, where it shatters a
    bore into fragments and invents radii between them — and what comes back is
    a diameter and a centre.

    Reported as `traced`, and traced is not read. One pixel is 0.2 mm here, so
    this can contradict a bore called 100 that is 108, and it can never supply
    the 35 the sheet prints. Where a callout points into a hole the two are said
    together and their difference is left showing.
    """
    if not _numpy():
        return [], []

    said, circles = [], []
    for picture in page.get_image_info(xrefs=True):
        box = pymupdf.Rect(*picture["bbox"])
        if _share_of(box, rect) < HELD_BY_A_PICTURE or not picture.get("xref"):
            continue
        pixels = _pixels_of(document, picture["xref"])
        if pixels is None:
            continue
        across = box.width / pixels.shape[1]
        down = box.height / pixels.shape[0]

        def in_millimetres(point, across=across, down=down):
            return _in_millimetres((box.x0 + point[0] * across,
                                    box.y0 + point[1] * down),
                                   rect, millimetres_per_point)

        said.extend(_shaped(_outline(pixels), in_millimetres))
        for hole, walked in _the_holes_in(pixels):
            circle = _circle_through(hole)
            if circle is None:
                said.extend(_shaped(walked, in_millimetres))
                continue
            (x, y), radius = circle
            on_the_page = (box.x0 + x * across, box.y0 + y * down)
            wide = 2 * radius * across * millimetres_per_point
            said.append((wide, "traced circle \u00d8%.2f centred %s"
                         % (wide, _at(_in_millimetres(on_the_page, rect,
                                                      millimetres_per_point)))))
            circles.append((on_the_page, radius * across, wide))
    return said, circles


def _numpy():
    """NumPy if it is here. Without it a picture stays a picture."""
    try:
        import numpy
    except ImportError:  # pragma: no cover - exercised by the machines without it
        return None
    return numpy


def _pixels_of(document, xref):
    """One placed picture as a grid of True where the part is, or None."""
    numpy = _numpy()
    try:
        pixmap = pymupdf.Pixmap(document, xref)
        if pixmap.alpha:
            pixmap = pymupdf.Pixmap(pixmap, 0)
        if pixmap.n > 1:
            pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pixmap)
        shade = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(
            pixmap.height, pixmap.width)
    except Exception:
        return None
    paper = int(numpy.median(numpy.concatenate(
        [shade[0], shade[-1], shade[:, 0], shade[:, -1]])))
    return numpy.abs(shade.astype(numpy.int16) - paper) > A_SHADE


def _the_holes_in(part):
    """
    Every patch of paper the part encloses, as its boundary and its outline.

    The paper that touches the edge of the picture is the paper around the
    view; what is left is inside the part, which is what a hole is. Two forms of
    the same edge come back: the boundary unordered, which is what a circle is
    fitted through, and the boundary walked round in order, which is what a
    shape that is not a circle has to be read from.
    """
    numpy = _numpy()
    height, width = part.shape
    outside = numpy.zeros(part.shape, dtype=bool)
    edge = ~part
    front = [(0, x) for x in range(width) if edge[0, x]]
    front += [(height - 1, x) for x in range(width) if edge[height - 1, x]]
    front += [(y, 0) for y in range(height) if edge[y, 0]]
    front += [(y, width - 1) for y in range(height) if edge[y, width - 1]]
    _flood(edge, outside, front)

    holes, seen = [], outside.copy()
    for y in range(height):
        for x in range(width):
            if edge[y, x] and not seen[y, x]:
                patch = numpy.zeros(part.shape, dtype=bool)
                _flood(edge, patch, [(y, x)])
                seen |= patch
                if int(patch.sum()) >= A_HOLE:
                    holes.append((_boundary(patch), _outline(patch)))
    return holes


def _flood(where, into, front):
    """One connected patch, marked into `into`, breadth first."""
    height, width = where.shape
    for y, x in front:
        into[y, x] = True
    while front:
        onward = []
        for y, x in front:
            for step_y, step_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (0 <= step_y < height and 0 <= step_x < width
                        and where[step_y, step_x] and not into[step_y, step_x]):
                    into[step_y, step_x] = True
                    onward.append((step_y, step_x))
        front = onward


def _boundary(patch):
    """The pixels of a patch that touch something that is not it."""
    numpy = _numpy()
    inner = patch.copy()
    inner[1:] &= patch[:-1]
    inner[:-1] &= patch[1:]
    inner[:, 1:] &= patch[:, :-1]
    inner[:, :-1] &= patch[:, 1:]
    y, x = numpy.nonzero(patch & ~inner)
    return numpy.stack([x.astype(numpy.float64), y.astype(numpy.float64)], axis=1)


def _circle_through(points, round_to=None):
    """
    The circle fitted through a whole boundary at once, or None if it is not one.

    Algebraic and in one step — the least-squares solution of
    `x² + y² = ax + by + c` — so every point of the boundary has the same say.
    Fitted greedily along the boundary instead, a bore comes back as fifteen to
    thirty fragments with radii it never had, which is why this does not go
    through `_fitted`.
    """
    numpy = _numpy()
    round_to = ROUND if round_to is None else round_to
    if len(points) < 6:
        return None
    x, y = points[:, 0], points[:, 1]
    solved, *_ = numpy.linalg.lstsq(
        numpy.stack([x, y, numpy.ones(len(x))], axis=1), x * x + y * y, rcond=None)
    centre = (solved[0] / 2.0, solved[1] / 2.0)
    under = solved[2] + centre[0] ** 2 + centre[1] ** 2
    if under <= 0:
        return None
    radius = math.sqrt(under)
    off = numpy.abs(numpy.hypot(x - centre[0], y - centre[1]) - radius)
    if radius <= 0 or float(off.max()) / radius > round_to:
        return None
    return centre, radius


# How far a traced run may stray from the line or the circle fitted through it
# and still be it, in pixels of the placed picture. Two pixels is half a
# millimetre at 186 DPI: inside the width of the line the view is drawn with,
# and far outside the staircase a diagonal edge is stored as.
A_STEP = 2.0

# What counts as a corner when the contour is first cut up, in pixels. Smaller
# and a rounded end is cut into a dozen pieces that have to be joined again;
# larger and a real corner is missed and a line and an arc are fitted as one.
A_CORNER = 1.5

# How many entities one traced outline is reported by. A silhouette is a few
# edges and their fillets; anything past this is the picture's own noise.
A_SHAPE = 24

# How far round a circle a run has to go before it is an arc rather than a
# line, in degrees. A straight edge on a rendered view is not perfectly
# straight — a hundred millimetres of it wanders a pixel or two — and a circle
# will always fit that wander, at a radius of a thousand millimetres and a
# sweep of four degrees. No draughtsman drew that. An arc turns.
#
# Thirty degrees is where the two populations separate on these sheets: the
# rounded ends of the 2025 plate sweep 180, the fillets of its pocket sweep 40
# to 47, and everything that came back with a radius the part cannot have —
# R310 across a 98 mm edge, R104 across 39 — sweeps 18 to 27. Below it the run
# is said as the two ends it goes between, which understates a genuinely shallow
# arc and never invents a radius. An understatement is a thing a reader can
# check against the picture; R310 is not.
A_TURN = 30.0


def _outline(mask):
    """
    The boundary of a patch of pixels, walked round in order.

    Ordered, which is the whole point: an unordered set of boundary pixels can
    be fitted with one circle and nothing else, and a part is lines and arcs in
    sequence. Moore-neighbour tracing, starting from the topmost-leftmost pixel
    and keeping the patch on the same hand throughout.
    """
    numpy = _numpy()
    height, width = mask.shape
    rows, columns = numpy.nonzero(mask)
    if not len(rows):
        return None
    start = (int(rows.min()), int(columns[rows == rows.min()].min()))

    around = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))
    walked, here, came_from = [start], start, 7
    while len(walked) < 4 * (height + width) * 4:
        onward = None
        for step in range(8):
            at = (came_from + 1 + step) % 8
            point = (here[0] + around[at][0], here[1] + around[at][1])
            if (0 <= point[0] < height and 0 <= point[1] < width
                    and mask[point]):
                onward, came_from = point, (at + 4) % 8
                break
        if onward is None or (onward == start and len(walked) > 2):
            break
        walked.append(onward)
        here = onward
    return numpy.array([(x, y) for y, x in walked], dtype=float)


def _corners(points, apart):
    """Where a walked outline changes direction, by recursive splitting."""
    numpy = _numpy()
    cut, waiting = {0, len(points) - 1}, [(0, len(points) - 1)]
    while waiting:
        first, last = waiting.pop()
        if last <= first + 1:
            continue
        between = points[first + 1:last]
        if not len(between):
            continue
        start, end = points[first], points[last]
        along = end - start
        span = math.hypot(along[0], along[1])
        if span < 1e-9:
            off = numpy.hypot(*(between - start).T)
        else:
            off = numpy.abs(along[0] * (between[:, 1] - start[1])
                            - along[1] * (between[:, 0] - start[0])) / span
        if float(off.max()) <= apart:
            continue
        at = int(numpy.argmax(off)) + first + 1
        cut.add(at)
        waiting += [(first, at), (at, last)]
    return sorted(cut)


def _straight(run, within):
    """The run as one line, or None where it bends."""
    numpy = _numpy()
    start, end = run[0], run[-1]
    along = end - start
    span = math.hypot(along[0], along[1])
    if span < 1e-9:
        return None
    off = numpy.abs(along[0] * (run[:, 1] - start[1])
                    - along[1] * (run[:, 0] - start[0])) / span
    return (span, start, end) if float(off.max()) <= within else None


def _curved(run, within):
    """
    The run as one circle fitted through all of it, or None.

    A circle that the run barely goes round is not an arc, however well it
    fits: it is the wander of a straight edge, and reporting it as R1274 gives
    a reader a radius the part does not have.
    """
    if len(run) < 6:
        return None
    fitted = _circle_through(run, within / max(1.0, _spread(run)))
    if fitted is None:
        return None
    centre, radius = fitted
    if radius <= 0:
        return None
    swept = abs(_angle(centre, run[0]) - _angle(centre, run[-1]))
    swept = min(swept, 2 * math.pi - swept) if swept > math.pi else swept
    return fitted if math.degrees(swept) >= A_TURN else None


def _spread(run):
    """How far across a run is, used to turn a pixel band into a share of it."""
    numpy = _numpy()
    return float(max(run[:, 0].max() - run[:, 0].min(),
                     run[:, 1].max() - run[:, 1].min())) / 2.0 or 1.0


def _shaped(outline, into_millimetres):
    """
    One walked outline as the lines and arcs it is, each fitted whole.

    Cut at its corners first and then joined back up as far as one line or one
    circle carries — the opposite order from `_fitted`, which walks forward and
    takes what it can get. That order is right for a draughtsman's own polyline
    and wrong here: a pixel edge is a staircase, and a fit that grows greedily
    along one takes the staircase for curvature and stops, which is how a bore
    comes back as thirty fragments with radii it never had.
    """
    if outline is None or len(outline) < 8:
        return []

    cuts = _corners(outline, A_CORNER)
    runs = [outline[first:last + 1] for first, last in zip(cuts, cuts[1:])]

    joined, at = [], 0
    while at < len(runs):
        run, onward = runs[at], at + 1
        while onward < len(runs):
            numpy = _numpy()
            wider = numpy.concatenate([run, runs[onward][1:]])
            if _straight(wider, A_STEP) or _curved(wider, A_STEP):
                run, onward = wider, onward + 1
            else:
                break
        joined.append(run)
        at = onward

    said = []
    for run in joined:
        line = _straight(run, A_STEP)
        if line:
            span, start, end = line
            start, end = into_millimetres(start), into_millimetres(end)
            said.append((_distance(start, end),
                         "traced line %s to %s" % (_at(start), _at(end))))
            continue
        arc = _curved(run, A_STEP)
        if arc:
            centre, radius = arc
            wide = _distance(into_millimetres(run[0]), into_millimetres(run[-1]))
            said.append((wide, "traced arc R%.2f centred %s, from %s to %s"
                         % (radius * _the_millimetre(into_millimetres),
                            _at(into_millimetres(centre)),
                            _at(into_millimetres(run[0])),
                            _at(into_millimetres(run[-1])))))
            continue
        # Neither, at the precision the picture has: said as the two ends it
        # runs between, which is true, rather than as a radius it does not have.
        said.append((_distance(into_millimetres(run[0]),
                               into_millimetres(run[-1])),
                     "traced line %s to %s"
                     % (_at(into_millimetres(run[0])),
                        _at(into_millimetres(run[-1])))))
    said.sort(key=lambda entity: -entity[0])
    return said[:A_SHAPE]


def _the_millimetre(into_millimetres):
    """How much of the part one pixel is, asked of the conversion itself."""
    origin = into_millimetres((0.0, 0.0))
    return abs(into_millimetres((1.0, 0.0))[0] - origin[0])


def _the_pixels_disagree(circles, printed, leaders, notes):
    """
    Where a bore's callout and the hole its leader points into do not match.

    This is the join the whole exercise is for. The callout is exact and comes
    out of the text layer; the hole is measured off the picture the view was
    placed as; the leader, traced through the file's own line work, says which
    hole the callout is about. Three pieces of evidence, none of them a reading,
    and a disagreement between them is a fact about the sheet.

    Recorded, never settled — the same rule the conflicts in `design.py` follow.
    A traced diameter is measured to the inside of the line that draws the hole
    and so reads about a millimetre under the drawn size, which is why the band
    is set where it is: wide enough that the sheet agreeing with itself is
    silent, narrow enough that a digit read wrong is not.
    """
    for leader in leaders:
        reaches = leader["its_leader_reaches"]
        said = [entry for entry in printed
                if entry.text == leader["callout"]
                and entry.at == leader["printed_at"]]
        if not said or said[0].notation != "\u00d8":
            continue
        across = _a_number(said[0].text)
        if across is None:
            continue
        for centre, radius, wide in circles:
            if _distance(centre, reaches) <= radius and abs(wide - across) > TOO_FAR:
                notes.append(
                    "The callout \u00d8%s points into a hole whose pixels measure "
                    "\u00d8%.1f mm — a difference of %.1f mm. The callout is the "
                    "draughtsman's own text and the measurement is this module's; "
                    "nothing here settles which is meant."
                    % (said[0].text, wide, abs(wide - across)))
                break


# ── a raster ──────────────────────────────────────────────────────────────────


def _from_raster(path, into, max_edge):
    """
    A PNG or a photo: the same cutting up, off its ink, and no text layer.

    Worth doing without one. The regions come out of the picture rather than out
    of a draughtsman's file, so they are rougher, but the resolution they buy is
    the same resolution, and it is the resolution that was failing.
    """
    picture = Image.open(path).convert("RGB")
    width, height = picture.size
    grid, cell = _raster_grid(picture)
    regions = _raster_regions(grid, cell, width, height)

    overview = os.path.join(into, "sheet.png")
    scaled = picture.copy()
    scaled.thumbnail((max_edge, max_edge), Image.LANCZOS)
    scaled.save(overview)

    crops = []
    for at, (x0, y0, x1, y1) in enumerate(regions, start=1):
        piece = picture.crop((int(x0), int(y0), int(x1), int(y1)))
        if max(piece.size) > max_edge:
            piece.thumbnail((max_edge, max_edge), Image.LANCZOS)
        # And never the other way. A crop smaller than the cap used to be
        # enlarged towards it, which adds pixels, adds no ink, and is billed for
        # every one of them — the file holds what the file holds.
        out = os.path.join(into, "crop-%02d.png" % at)
        piece.save(out)
        crops.append(Crop(name="%s of the sheet" % _raster_where(
            (x0, y0, x1, y1), width, height), path=out,
            rect=(x0, y0, x1, y1)))

    return Sheet(source=path, overview=overview, crops=crops, printed=[],
                 leaders=[],
                 notes=["A raster has no text layer, so what is printed on this "
                        "sheet is not extracted and has to be read off the views."])


def _raster_grid(picture, cells_across=200):
    """
    The picture as marks, against whatever its own paper turns out to be.

    The paper is read off the border of the picture. A drawing exported from a
    viewer comes out on whatever that viewer's background was — black, as often
    as white — and "dark means ink" then makes the background the drawing.
    """
    grey = picture.convert("L")
    cell = max(1, int(picture.width / cells_across))
    small = grey.resize((max(1, picture.width // cell),
                         max(1, picture.height // cell)), Image.BILINEAR)
    pixels = small.load()
    paper = _paper_of(pixels, small.width, small.height)

    grid = [[abs(pixels[x, y] - paper) > INK for x in range(small.width)]
            for y in range(small.height)]
    return _without_the_frame(grid), cell


def _paper_of(pixels, width, height):
    edge = ([pixels[x, 0] for x in range(width)]
            + [pixels[x, height - 1] for x in range(width)]
            + [pixels[0, y] for y in range(height)]
            + [pixels[width - 1, y] for y in range(height)])
    edge.sort()
    return edge[len(edge) // 2]


def _without_the_frame(grid):
    """The border and the title-block rules struck out, so the views come apart."""
    rows, columns = len(grid), len(grid[0])
    for row in range(rows):
        if sum(grid[row]) > A_RULE * columns:
            grid[row] = [False] * columns
    for column in range(columns):
        if sum(grid[row][column] for row in range(rows)) > A_RULE * rows:
            for row in range(rows):
                grid[row][column] = False
    return grid


def _raster_regions(grid, cell, width, height):
    rows, columns = len(grid), len(grid[0])
    seen = [[False] * columns for _ in range(rows)]
    found = []
    for row in range(rows):
        for column in range(columns):
            if not grid[row][column] or seen[row][column]:
                continue
            stack, cells = [(row, column)], []
            seen[row][column] = True
            while stack:
                at_row, at_column = stack.pop()
                cells.append((at_row, at_column))
                for next_row in range(at_row - REACH, at_row + REACH + 1):
                    for next_column in range(at_column - REACH, at_column + REACH + 1):
                        if not (0 <= next_row < rows and 0 <= next_column < columns):
                            continue
                        if grid[next_row][next_column] and not seen[next_row][next_column]:
                            seen[next_row][next_column] = True
                            stack.append((next_row, next_column))
            if len(cells) < 6:
                continue
            xs = [c[1] for c in cells]
            ys = [c[0] for c in cells]
            found.append((min(xs) * cell, min(ys) * cell,
                          (max(xs) + 1) * cell, (max(ys) + 1) * cell))

    # Merged on their own ink and padded afterwards, in that order. Padding
    # first chains: a margin wide enough to bring a dimension along is wide
    # enough to touch the next view, whose margin touches the one after it,
    # and the whole sheet comes back as a single crop — which is the thing this
    # module exists to stop. Padded after, the crops simply overlap, which is
    # what they do at every other cut anyway.
    pad = max(cell * 4, int(0.06 * max(width, height)))
    return [(max(0, box[0] - pad), max(0, box[1] - pad),
             min(width, box[2] + pad), min(height, box[3] + pad))
            for box in _merged_boxes(found)]


def _merged_boxes(boxes):
    """
    Overlapping regions joined into one, until none of them overlap.

    Dropping a box that sits inside another is not enough: two views whose ink
    genuinely overlaps would be sent as two crops that are each mostly the
    other one. Joined, they are one crop of both, which costs resolution — and
    the tiling gives it back.
    """
    boxes = list(boxes)
    joined = True
    while joined:
        joined = False
        for one in range(len(boxes)):
            for other in range(one + 1, len(boxes)):
                if not _overlap(boxes[one], boxes[other]):
                    continue
                boxes[one] = (min(boxes[one][0], boxes[other][0]),
                              min(boxes[one][1], boxes[other][1]),
                              max(boxes[one][2], boxes[other][2]),
                              max(boxes[one][3], boxes[other][3]))
                boxes.pop(other)
                joined = True
                break
            if joined:
                break
    return sorted(boxes, key=lambda box: (box[1], box[0]))


def _overlap(one, other):
    return not (one[2] <= other[0] or other[2] <= one[0]
                or one[3] <= other[1] or other[3] <= one[1])


def _box_covers(outer, inner):
    x0 = max(outer[0], inner[0])
    y0 = max(outer[1], inner[1])
    x1 = min(outer[2], inner[2])
    y1 = min(outer[3], inner[3])
    if x1 <= x0 or y1 <= y0:
        return False
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return bool(area) and ((x1 - x0) * (y1 - y0)) / area > 0.8


def _raster_where(box, width, height):
    middle_x = (box[0] + box[2]) / 2
    middle_y = (box[1] + box[3]) / 2
    down = ("the upper", "the middle", "the lower")[min(2, int(3 * middle_y / height))]
    across = ("left", "centre", "right")[min(2, int(3 * middle_x / width))]
    return "%s %s" % (down, across) if across != "centre" else "%s centre" % down


def _as_tuple(rect):
    return (round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1))


# ── what the sheet is shown as ────────────────────────────────────────────────

# What marks the title block rather than a view. Its numbers are a drawing
# number, a date and a scale — nothing the part is built from — so they are
# recorded like every other callout and left out of the coverage check.
_THE_TITLE_BLOCK = ("SCALE:", "SCALE", "DRAWN", "PROJECTION", "CONFIDENTIAL")

# A callout the part could actually be built from: a bare number, with or
# without parentheses, a radius or diameter sign, or a decimal.
_A_DIMENSION = ("\u00d8", "\u2300", "R", "(", ")", "\u00b0", "\u00b1", "X", "\u00d7")


def shown(sheet):
    """
    The sheet as the sequence of things to say and pictures to show, in order.

    Says rather than sends: the envelope a provider wants around an image is
    the caller's business and already written once, in the loop. This decides
    what is worth showing and what has to be said beside it, which is the part
    that belongs to the sheet.
    """
    if sheet is None:
        return []

    told = [{"say": "The drawing `%s`, rendered here rather than sent whole, so "
                    "the callouts are legible. The first picture is the sheet as "
                    "laid out — use it to see where the views are and what refers "
                    "to what. The pictures after it are the same sheet close up, "
                    "one per view, and they are what the numbers should be read "
                    "from." % os.path.basename(sheet.source)},
            {"show": sheet.overview}]

    for page, overview in enumerate(sheet.overviews or [sheet.overview], start=1):
        if page > 1:
            told.append({"say": "Page %d of the drawing, as laid out:" % page})
            told.append({"show": overview})
        shown_whole = set()
        for crop in sheet.crops:
            if crop.page != page:
                continue
            view = crop.name.split(", part ")[0]
            whole = next((one for one in sheet.wholes
                          if one.page == page and one.name == view), None)
            if whole is not None and view not in shown_whole:
                shown_whole.add(view)
                told.append({"say": "%s, whole. The pictures after it are this "
                                    "same view close up, in pieces, and they are "
                                    "what the numbers are read from — this one is "
                                    "how the pieces fit together and how this view "
                                    "lines up with the others." % view})
                told.append({"show": whole.path})
            told.append({"say": "%s:" % crop.name})
            told.append({"show": crop.path})

    if sheet.printed:
        told.append({"say": _the_printed_list(sheet)})
    if sheet.leaders:
        told.append({"say": _the_leader_list(sheet)})
    if sheet.drawn:
        told.append({"say": _the_drawn_list(sheet)})
    for note in sheet.notes:
        told.append({"say": note})
    return told


def _the_printed_list(sheet):
    lines = ["Every string the sheet's own text layer carries, extracted from "
             "the file rather than read off the picture, with the view it is in. "
             "This is exact — it is the draughtsman's own text — and it is the "
             "list `printed_on_the_sheet` has to account for."]
    for entry in sheet.printed:
        where = " — %s" % entry.seen_in if entry.seen_in else ""
        measured = ("; the sheet measures %.1f mm between the faces this one's "
                    "dimension line is drawn across" % entry.measures
                    if entry.measures is not None else "")
        lines.append("  %s%s%s%s" % (entry.notation, entry.text, where, measured))
    return "\n".join(lines)


def _the_leader_list(sheet):
    lines = ["Where each callout's leader runs, traced through the file's own "
             "line work rather than judged by eye. The point given is where the "
             "line ends — the end the arrowhead is on, so it is the feature the "
             "callout is *of*. What that feature is is still yours to read."]
    for entry in sheet.leaders:
        lines.append("  %s printed at %s — its leader reaches %s"
                     % (entry["callout"], entry["printed_at"],
                        entry["its_leader_reaches"]))
    return "\n".join(lines)


def _the_drawn_list(sheet):
    lines = ["The line work each view is drawn with, taken out of the file and "
             "converted to millimetres of the part. Coordinates are millimetres "
             "inside the view itself: x to the right of the view's left edge, y "
             "*up* from its bottom edge. This is the draughtsman's own geometry "
             "and not a reading of a picture — a centre line marks an axis, a "
             "dimension's extension lines are drawn from the very faces that "
             "dimension measures, and a contour is the part. So a distance "
             "nobody printed is measured here rather than assumed, and a value "
             "measured off it is `derived`, never `not_defined`. An entity "
            "marked `traced` is not the draughtsman's: it is measured off the "
            "picture the view was placed as, to the inside of the line that "
            "draws it, so it reads about a millimetre under the drawn size. "
            "Use it to check a number, never to supply one."]
    round_here = round_in(sheet)
    if round_here:
        lines.append(
            "What each view is round in, which is the one thing the drawing "
            "says outright about direction: a feature is round in the view its "
            "axis points at and in no other, so two of them round in two views "
            "cannot have been drilled the same way. Say which view each feature "
            "is round in, in `round_in`.")
        for view, diameters in round_here.items():
            lines.append("  %s: %s" % (view, ", ".join(
                "\u00d8%.2f" % wide for wide in diameters)))
    for view in sheet.drawn:
        if not view.entities:
            continue
        lines.append("  %s%s:" % (
            view.view,
            " — its body was placed as a picture, so what follows is its "
            "dimension and centre lines and not its outline"
            if view.from_a_picture else ""))
        lines.extend("    %s" % said for said in view.entities)
        if view.left_out:
            lines.append("    (and %d shorter entities, left out)" % view.left_out)
    return "\n".join(lines)


# What a round entity looks like once it has been written down, so the one
# statement a drawing makes about direction can be read back off the list.
_A_CIRCLE = re.compile(r"circle \u00d8([0-9]+(?:\.[0-9]+)?)")


def round_in(sheet):
    """
    {view: [diameter, …]} — what each view was found round in.

    The only thing a projection states outright about direction: a feature is
    round in the view its axis points at and in no other. Two features round in
    two views therefore have crossing axes, whatever a reading of the picture
    says about either, and that holds without knowing which view is the plan
    and which the elevation — which is as well, because the reader names a view
    after where it sits on the sheet and the description names it after what it
    shows (FCC-109).
    """
    if sheet is None:
        return {}
    found = {}
    for view in sheet.drawn:
        round_here = [float(match.group(1)) for said in view.entities
                      for match in [_A_CIRCLE.search(said)] if match]
        if round_here:
            found[view.view] = sorted(round_here)
    return found


def printed_on_the_sheet(sheet):
    """
    The extracted sweep, in the shape the description records it in.

    Written by this module rather than by the phase reading the sheet, and put
    into the model over whatever the phase wrote there. It is the text layer:
    it is already exact, it already carries what the sheet's own dimension
    lines measure at each number, and a paraphrase of it is a loss. In the run
    that named this (`logs/sessions/549e6232c9e04b659e5a2d536354bd7a.jsonl`)
    230 entries came back as 22 sentences — "Page 1: (6), (6), 30, (25) —
    SECTION A-A" — and `off_by`, which is the whole of the arithmetic that
    catches 108 read as 100, went with them (FCC-112).
    """
    if sheet is None:
        return []
    return [_as_swept(entry) for entry in sheet.printed]


def _as_swept(entry):
    swept = {"callout": entry.text, "view": entry.seen_in or "the sheet"}
    if entry.notation:
        swept["drawn_in_front_of_it"] = entry.notation
    if entry.measures is not None:
        swept["the_sheet_measures"] = round(entry.measures, 2)
        swept["off_by"] = round(entry.off_by, 2)
    return swept


def dimensions(sheet):
    """
    The callouts a part could be built from: everything outside the title block.

    A drawing number, a revision date and `1:2` are printed on the sheet and are
    not values of the part. They stay in `printed` because the sweep is a record
    of the sheet, and they are kept out of here because the coverage check is a
    record of the part.
    """
    if sheet is None:
        return []
    blocked = [crop.rect for crop in sheet.crops
               if any(word in crop.name.upper() for word in _THE_TITLE_BLOCK)]
    blocked += [tuple(entry.at) for entry in sheet.printed
                if entry.text.upper().rstrip(":") in _THE_TITLE_BLOCK]
    return [entry for entry in sheet.printed
            if _a_dimension(entry.text) and not _in_the_title_block(entry, sheet)]


def _a_dimension(text):
    """
    Whether a string off the sheet is a number the part could be built from.

    An item number is not one. `1.` and `2.` enumerate the notes on every one of
    these sheets and `3.` and `4.` enumerate page 2's, and read as callouts they
    are two dimensions the description can never account for — which sends
    Design back to the sheet to explain, every run, that a full stop is a full
    stop. A dimension is printed `30`, `42.5`, `(R30)`; a trailing full stop
    with no digit after it is a list marker and is nothing else on a drawing.
    """
    stripped = text.strip().strip("()")
    for sign in _A_DIMENSION:
        stripped = stripped.replace(sign, "")
    if stripped.endswith("."):
        return False
    return bool(stripped) and stripped.replace(".", "", 1).isdigit()


def _in_the_title_block(entry, sheet):
    for crop in sheet.crops:
        if not _holds(crop.rect, entry.at):
            continue
        near = [other.text.upper() for other in sheet.printed
                if _holds(crop.rect, other.at)]
        if any(word in near for word in _THE_TITLE_BLOCK):
            return True
    return False


def _holds(rect, point):
    return rect[0] <= point[0] <= rect[2] and rect[1] <= point[1] <= rect[3]


def uncited(sheet, model):
    """
    The dimensions the sheet prints that the finished description never used.

    The check `printed_on_the_sheet` was written for, finally able to run. Asked
    of the model itself, that list is the model's own account of what it
    noticed, counted against itself — it cannot name the callout it never saw,
    which is the one failure worth catching. Held against the text layer it can.

    A number is "used" if it appears anywhere in the description at all,
    including in `not_defined` and in `conflicts`: those are accounted for by
    being written down, which is the rule the prompt already states. Deliberately
    loose in the other direction — this reports what to look at again, and a
    false alarm costs one more look at the sheet while a miss costs the part.
    """
    if sheet is None or not model:
        return []
    # Not the sweep itself: that is this module's own list now (FCC-112), and
    # counting it would mean every callout is accounted for by having been
    # copied in — which is the failure this check exists to catch.
    said = _numbers_in({key: value for key, value in model.items()
                        if key != "printed_on_the_sheet"})
    return [entry.text for entry in dimensions(sheet)
            if _number_of(entry.text) not in said]


def _numbers_in(node):
    found = set()
    if isinstance(node, dict):
        for value in node.values():
            found |= _numbers_in(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            found |= _numbers_in(value)
    elif node is not None:
        text = str(node)
        digits, at = "", 0
        while at < len(text):
            if text[at].isdigit() or (text[at] == "." and digits):
                digits += text[at]
            else:
                if digits:
                    found.add(_number_of(digits))
                digits = ""
            at += 1
        if digits:
            found.add(_number_of(digits))
    return found


def _number_of(text):
    stripped = "".join(character for character in str(text)
                       if character.isdigit() or character == ".").strip(".")
    try:
        return float(stripped)
    except ValueError:
        return stripped
