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

**The extracted list is numerals, not notation.** Measured on the same sheet:
`Ø` is not in the text layer at all — the diameter symbol is drawn as geometry,
so the layer yields `35 THRU` where the sheet shows `Ø35 THRU`. `R` survives,
`(R30)` survives whole. That asymmetry is why this module supplies evidence and
does not supply the reading: the numbers come from the text layer because it
cannot misread them, and what they mean stays with the phase that can see the
arrowhead.

Everything here degrades rather than fails. Without PyMuPDF there is no text
layer and no page rendering, and `read` returns None so the caller sends the
attachment exactly as it did before. A raster attachment has no text layer by
definition: it still gets regions and crops, off its own ink, and an empty
printed list saying so.

Nothing here imports FreeCAD, and nothing here reads a model's mind.
"""
import math
import os
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


@dataclass(frozen=True)
class Printed:
    """One string the sheet carries, with where it is and which crop shows it."""

    text: str
    at: tuple
    seen_in: str = ""


@dataclass(frozen=True)
class Sheet:
    """What one attachment turned out to be."""

    source: str
    overview: str = ""
    crops: list = field(default_factory=list)
    printed: list = field(default_factory=list)
    leaders: list = field(default_factory=list)
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
        os.path.isfile(crop.path) for crop in sheet.crops)


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
    document = pymupdf.open(path)
    page = document[0]
    notes = []
    if len(document) > 1:
        notes.append("The sheet has %d pages and only the first was read."
                     % len(document))

    words = [w for w in page.get_text("words") if str(w[4]).strip()]
    dpi = _dpi_for(words, legible_px, notes)

    overview = _render(page, page.rect, _fits(page.rect, max_edge), into, "sheet")
    grid = _ink_grid(page, words)
    regions = _cut_on_gutters(_regions(grid, page.rect), grid)
    regions = _one_view_one_picture(regions, page.get_image_info())
    regions = _covering(regions, [tuple(w[:4]) for w in words], page.rect)

    crops = []
    # The region is named once and its tiles are named after it. A tile is not
    # a view — naming each one on its own gave two crops both called
    # `SECTION A-A`, and a third called `SECTION` because the label's second
    # word fell the other side of the cut.
    for name, rect in zip(_names_for(regions, words, page.rect), regions):
        parts = _tiled(rect, dpi, max_edge)
        for part_at, part in enumerate(parts, start=1):
            crops.append(Crop(name=_part_named(name, part_at, len(parts)),
                              path=_render(page, part, dpi, into,
                                           "crop-%02d" % (len(crops) + 1)),
                              rect=_as_tuple(part), dpi=dpi))

    printed = [Printed(text=str(w[4]).strip(), at=(round(w[0], 1), round(w[1], 1)),
                       seen_in=_showing(crops, w))
               for w in words]
    if printed:
        notes.append("The printed list is the sheet's own text layer. Symbols "
                     "drawn as geometry are not in it: measured on this sheet "
                     "the diameter sign is absent, so a bore reads as `35 THRU` "
                     "where the sheet shows `\u00d835 THRU`. The numbers are "
                     "exact; the notation is for the views to supply.")
    else:
        notes.append("The sheet carries no text layer, so nothing could be "
                     "extracted and every callout has to be read off the views.")

    return Sheet(source=path, overview=overview, crops=crops, printed=printed,
                 leaders=_leaders(page, words), notes=notes)


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
    """The DPI at which a rect's long edge lands on `max_edge`."""
    longest = max(rect.width, rect.height) / 72.0
    return max(72, int(max_edge / longest)) if longest else 150


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
        elif max(piece.size) < max_edge // 2:
            factor = min(2.0, (max_edge / 2.0) / max(1, max(piece.size)))
            piece = piece.resize((int(piece.width * factor),
                                  int(piece.height * factor)), Image.LANCZOS)
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

    for crop in sheet.crops:
        told.append({"say": "%s:" % crop.name})
        told.append({"show": crop.path})

    if sheet.printed:
        told.append({"say": _the_printed_list(sheet)})
    if sheet.leaders:
        told.append({"say": _the_leader_list(sheet)})
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
        lines.append("  %s%s" % (entry.text, where))
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


def printed_on_the_sheet(sheet):
    """The extracted sweep, in the shape the description records it in."""
    if sheet is None:
        return []
    return [{"callout": entry.text, "view": entry.seen_in or "the sheet"}
            for entry in sheet.printed]


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
    stripped = text.strip().strip("()")
    for sign in _A_DIMENSION:
        stripped = stripped.replace(sign, "")
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
    said = _numbers_in(model)
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
