"""
The sheet as evidence, before anybody reads it -- rendered, cropped, and
its ink measured, never handed to the provider whole. Rendered at a DPI
chosen from the sheet's own text, not the provider's default, since a
misread digit ("108" read as "100") is an 8mm contradiction on the
record; cut into per-view crops under MAX_EDGE so no provider downscale
halves a callout across a view; what is printed is extracted from the
vector layer, never read back off a render; regions are found on the
sheet's own ink, not a fixed brightness threshold, since a screenshot on
a black background broke one. It degrades rather than fails -- no
PyMuPDF means no text/render and `read` returns None.
"""
import atexit
import collections
import functools
import logging
import math
import os
import re
import shutil
import tempfile
import threading

try:
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

LEGIBLE_PX = 50

_LOG = logging.getLogger(__name__)

MAX_EDGE = 1400

OVERVIEW_EDGE = 2576

A_PATCH = 28
THE_BUDGET = 1568

OVERLAP = 0.15

CELL = 4.0

REACH = 3

FRAME = 0.85

INK = 40

A_RULE = 0.7

GUTTER = 2

A_VIEW = 90.0

HELD_BY_A_PICTURE = 0.4

IN_THE_REGION = 0.5

SHAPE_LONG_EDGE = 600

INK_PIXELS = 2

RASTER_REACH = 0.015

RASTER_MARGIN = 0.02

RASTER_JOIN = 0.5

CORNER_MARK = 0.05


@dataclass(frozen=True)
class Crop:
    """
    One region of the sheet, rendered large enough to read.

    `rect` is where it came from on the page, in the page's own units, so a
    callout's coordinates can be said to be inside it.

    `shape_only` marks a placed picture no region of ink ever reached — the
    isometric in a sheet's corner, read only for its outline, rendered once at
    the resolution carrying its shape, not the paper's fine print.
    """
    name: str
    path: str
    rect: tuple
    dpi: int = 0
    page: int = 1
    shape_only: bool = False


@dataclass(frozen=True)
class Printed:
    """
    One string the sheet carries, with where it is and which crop shows
    it. `measures` is what the sheet itself measures where this number
    is printed, in mm; `off_by` is its distance from the number, both
    None where no dimension line measures it -- silence is honest,
    invention is not. `notation` is the symbol drawn, not typed, before
    this string: `35 THRU` in text where the sheet shows `Ø35 THRU`.
    """
    text: str
    at: tuple
    seen_in: str = ""
    measures: float = None
    off_by: float = None
    notation: str = ""


@dataclass(frozen=True)
class Sheet:
    """
    What one attachment turned out to be.

    `wholes` is the views that had to be cut up, each one whole in a single
    picture.
    """
    source: str
    overview: str = ""
    crops: list = field(default_factory=list)
    wholes: list = field(default_factory=list)
    callouts: list = field(default_factory=list)
    leaders: list = field(default_factory=list)
    overviews: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def available() -> bool:
    """Whether a PDF can be rendered and read here at all."""
    return pymupdf is not None


_READ_LOCK = threading.Lock()
_READ_CACHE = collections.OrderedDict()
_READ_CACHE_LIMIT = 128


def _read_cached(key, path, into, legible_px, max_edge):
    if key in _READ_CACHE:
        _READ_CACHE.move_to_end(key)
        return _READ_CACHE[key]
    try:
        value = _read(path, into, legible_px, max_edge)
    except Exception:
        _LOG.warning("could not read sheet %s", path, exc_info=True)
        value = None
    _READ_CACHE[key] = value
    if len(_READ_CACHE) > _READ_CACHE_LIMIT:
        _READ_CACHE.popitem(last=False)
    return value


def read(path, into=None, legible_px=LEGIBLE_PX, max_edge=MAX_EDGE):
    """
    One attachment as the evidence it carries.

    Cached and bounded to 128 sheets. `_READ_LOCK` is one lock for every key,
    not one per key: the render is CPU-bound in pure-Python region-finding,
    not I/O PyMuPDF could overlap, so the parallelism this trades away is not
    worth a per-key lock's own bookkeeping.
    """
    if not path or not os.path.isfile(path):
        return None

    key = (os.path.abspath(path), os.path.getmtime(path), os.path.getsize(path),
           legible_px, max_edge, into)
    with _READ_LOCK:
        held = _read_cached(key, path, into, legible_px, max_edge)
        if held is not None and not _still_there(held):
            _READ_CACHE.pop(key, None)
            held = _read_cached(key, path, into, legible_px, max_edge)
        return held


def _still_there(sheet):
    """A cached sheet whose pictures a temp sweep removed is not a cached sheet."""
    return os.path.isfile(sheet.overview) and all(
        os.path.isfile(crop.path) for crop in list(sheet.crops) + list(sheet.wholes))


def _read(path, into, legible_px, max_edge):
    if not into:
        into = tempfile.mkdtemp(prefix="fcc-sheet-")
        atexit.register(functools.partial(shutil.rmtree, into, ignore_errors=True))
    os.makedirs(into, exist_ok=True)
    extension = os.path.splitext(path)[1].lower()

    if extension == ".pdf":
        return _from_pdf(path, into, legible_px, max_edge) if pymupdf else None
    if extension in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return _from_raster(path, into, max_edge) if Image else None
    return None


@dataclass
class _PageAccumulator:
    """
    What every page adds to, so a page's own function cannot mistake one list
    of a type for another. Nine positional `list` args to `_a_page` let two
    adjacent same-type ones (`printed`/`callouts`) swap at the call site; a
    field-name mutation cannot be mistaken for the wrong list.
    """
    overviews: list = field(default_factory=list)
    crops: list = field(default_factory=list)
    wholes: list = field(default_factory=list)
    printed: list = field(default_factory=list)
    callouts: list = field(default_factory=list)
    leaders: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _from_pdf(path, into, legible_px, max_edge):
    """
    Every page of the drawing, each one cut into its views and measured. Every
    page, because a sheet's second page is the rest of the same drawing. On
    `model-mania_-in-person-2023 copy.pdf`, page 2 prints `6.6 X 8.0 THRU ALL`:
    a run that read only page 1 asked the user how wide that slot is and
    invented 8 mm.
    """
    document = pymupdf.open(path)
    page_state = _PageAccumulator()
    for at, page in enumerate(document, start=1):
        _a_page(page, at, len(document), into, legible_px, max_edge, page_state)

    overviews, crops, wholes = page_state.overviews, page_state.crops, page_state.wholes
    printed, callouts, leaders = page_state.printed, page_state.callouts, page_state.leaders
    notes = list(dict.fromkeys(page_state.notes))
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
                 overviews=overviews, crops=crops, wholes=wholes,
                 callouts=callouts, leaders=leaders, notes=notes)


def _a_page(page, at, of_pages, into, legible_px, max_edge, page_state):
    """
    One page of the drawing, added to what the whole document has given.

    A region is named once and its tiles named after it, and rendered whole
    at the highest resolution one picture can be paid for.
    """
    overviews, crops, wholes = page_state.overviews, page_state.crops, page_state.wholes
    printed, callouts, leaders = page_state.printed, page_state.callouts, page_state.leaders
    notes = page_state.notes
    words = [w for w in page.get_text("words") if str(w[4]).strip()]
    dpi = _dpi_for(words, legible_px, notes)

    overviews.append(_render(page, page.rect, _fits(page.rect, OVERVIEW_EDGE), into,
                             "sheet" if at == 1 else "sheet-%02d" % at))
    regions = _the_regions_of(page, words)

    named = [(_on_page(name, at, of_pages), rect)
             for name, rect in zip(_names_for(regions, words, page.rect), regions)]
    for name, rect in named:
        parts = _tiled(rect, dpi, max_edge)
        if len(parts) > 1:
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

    orphans = _orphan_pictures(regions, page.get_image_info(), page.rect)
    orphan_names = _names_for(regions + orphans, words, page.rect)[len(regions):]
    for box, label in zip(orphans, orphan_names):
        shape = _shape_dpi(box)
        crops.append(Crop(name=_on_page(label, at, of_pages),
                          path=_render(page, box, shape, into,
                                       "shape-%02d" % (len(crops) + 1)),
                          rect=_as_tuple(box), dpi=shape, page=at,
                          shape_only=True))

    on_this_page = [crop for crop in crops if crop.page == at]
    printed_before = len(printed)
    spans, gaps = _spans(page)
    scale, voters, measured = _measured(words, spans, gaps)
    notation = _notation(page, words)
    printed.extend(Printed(text=str(w[4]).strip(),
                           at=(round(w[0], 1), round(w[1], 1)),
                           seen_in=_showing(on_this_page, w),
                           measures=measured.get(index, (None, None))[0],
                           off_by=measured.get(index, (None, None))[1],
                           notation=notation.get(index, ""))
                   for index, w in enumerate(words))
    callouts.extend(_as_callouts(words, printed[printed_before:]))
    leaders.extend(_leaders(page, words))
    printed_scale = _millimetres_per_point(words, notes)
    _the_scale_the_lines_vote_for(scale, voters, printed_scale, notes)


_A_REVISION = ("REVISION", "REVISIONS", "PHASE", "REV")


def _pages_are_not_one_sheet(pages, printed):
    """
    That the drawing has several sheets, and that they may not be one part.

    `_A_REVISION` is what a later page says when it revises an earlier one
    rather than extending it.
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
    The resolution the callouts need, which is not the resolution the sheet
    has. Set by the text that carries a digit, deliberately by nothing else. On
    `mm25-p1-1.pdf`, sizing the render so a 4.1 pt title-block paragraph is
    legible puts every crop at 492 DPI and fourteen tiles; sizing by the
    callouts gives 279 DPI and six.
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
    The highest DPI at which a rect still costs less than the provider will
    pay. Solved rather than searched, then walked down a step at a time, since
    the cost is in whole patches and the closed form is off by one wherever the
    rounding up lands.
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


def _the_regions_of(page, words):
    """
    The areas of one page a view could be in, in the order they are decided.

    The flood fill over the ink finds them, the gutters cut a region holding
    several views apart, a picture pulls back together the pieces of the view
    it is the body of, and a callout no region caught gets one of its own.
    """
    grid = _ink_grid(page, words)
    regions = _cut_on_gutters(_regions(grid, page.rect), grid)
    regions = _one_view_one_picture(regions, page.get_image_info())
    return _covering(regions, [tuple(w[:4]) for w in words], page.rect)


def _ink_grid(page, words):
    """
    The sheet as occupied cells: every line segment and every word. Segment by
    segment rather than path by path, because a path is not a view — on
    `mm25-p1-1.pdf`, three of twenty-five paths each span more than half the
    sheet, one drawn object holding several views at once.
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
    The occupied cells as the handful of boxes the sheet lays out. Flood fill
    with a reach, so a dimension standing off its view joins it while two views
    a clear gap apart stay apart. Deliberately not perfect: a region swallowing
    its neighbour costs resolution, splitting one costs nothing since every
    crop is sent, and under a dozen cells is a speck.
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
            if len(cells) < 12:
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

    Density is the signal, not colour: a view's body inks all cells, an
    extension line inks one, and blank is blank on a black drawing as on any
    other. The cut is taken at the widest strip.
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

    A shaded view is exported as a placed raster; a placed picture is a
    view's body, and an ordinary black drawing carries its bodies as line
    work and passes through untouched.
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
    return _shared(box, region, region.width * region.height)


def _the_region_holding_the_picture(box, regions):
    """
    Where a picture belongs, which is the region holding most of it. Regions
    overlap and a picture can lie across two, so the larger share decides, not
    a threshold: 0.55 in one region and 0.45 in another belongs to the 0.55,
    where a threshold both fail would leave it in neither. `IN_THE_REGION` says
    only that a picture is in no region worth speaking of.
    """
    best, most = None, IN_THE_REGION
    for region in regions:
        share = _share_of_the_picture(box, region)
        if share > most:
            best, most = region, share
    return best


def _share_of_the_picture(box, region):
    """How much of a picture lies inside a region, as a fraction of the picture."""
    return _shared(box, region, box.width * box.height)


def _shared(box, region, area):
    """
    Where a box and a region overlap, as a fraction of *area*.

    The two shares differ in nothing but which of the two is the whole, and
    which one that is decides what the number means: the share of a region a
    picture holds, or the share of a picture a region holds.
    """
    overlap = pymupdf.Rect(region) & box
    if overlap.is_empty:
        return 0.0
    return (overlap.width * overlap.height) / area if area else 0.0


def _orphan_pictures(regions, pictures, page_rect):
    """
    The placed pictures none of the sheet's regions ever reached.
    `_the_region_holding_the_picture` already answers whether a picture is in a
    region worth speaking of; a no is the same fact, read here not as a source
    of numbers, never a crop. The isometric in every sheet's corner is measured
    at 6%-16% covered, and a bbox off the page or with no area is not a view.
    """
    found = []
    for picture in pictures:
        box = pymupdf.Rect(*picture["bbox"])
        if (box & page_rect).is_empty:
            continue
        if _the_region_holding_the_picture(box, regions) is not None:
            continue
        found.append(box)
    return found


def _shape_dpi(rect):
    """
    The resolution a shape-only picture is rendered at. Nothing on it is read,
    so `_dpi_for`'s legibility budget does not apply — `SHAPE_LONG_EDGE` is
    sized instead for the outline and orientation it is kept for, measured on
    `tests/modelmania` at under 420 visual tokens against `_dpi_for`'s several
    thousand.
    """
    longest = max(rect.width, rect.height) / 72.0
    return max(72, int(SHAPE_LONG_EDGE / longest)) if longest else 72


def _covering(regions, boxes, page_rect):
    """
    Every printed word inside some region, whatever the flood fill decided. The
    coverage rule is the one the whole module answers to, so it is enforced
    rather than assumed: a callout the regions missed gets a region of its own
    instead of being quietly absent from every crop.
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

    A region wider than the cap is sent in overlapping pieces, because
    resolution is the point and the frame is not.
    """
    scale = dpi / 72.0
    across = max(1, int(math.ceil(rect.width * scale / max_edge)))
    down = max(1, int(math.ceil(rect.height * scale / max_edge)))
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

SAME_LINE = 12.0

LABEL_STANDS_UNDER = 24.0


def _names_for(regions, words, page_rect):
    """
    What to call each region: the sheet's own label where the sheet prints one.

    Labels are found first, then handed to the views they belong to. A region
    collecting two labels is called by both.
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
    The labels the draughtsman printed, told apart from the notes that name
    one. A label is set on a line of its own — `SECTION A-A`, `VIEW B`, `DETAIL
    A` — the label word and its designator, nothing else. On the 2023 sheet,
    `SEE VIEW B FOR CLARITY OF CUTOUT` reads as a label because `SEE` leads
    `VIEW`.
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
    """
    One baseline as the separate pieces of text set on it. `SAME_LINE` bounds
    how far apart two words may be and still share a printed line, in points.
    On the 2023 sheet, `SECTION` and `A-A` stand 3.1 pt apart, and the nearest
    thing sharing that baseline elsewhere is a hundred points away — wide
    enough for a word space, short of a column.
    """
    run = []
    for word in line:
        if run and float(word[0]) - float(run[-1][2]) > SAME_LINE:
            yield run
            run = []
        run.append(word)
    if run:
        yield run


def _as_callouts(words, entries):
    """
    One page's strings grouped into the lines the draughtsman set them on.

    A printed line is what shares a baseline and stands within `SAME_LINE`
    of the word before it.
    """
    lines = {}
    for index, word in enumerate(words):
        lines.setdefault(round(float(word[1]), 1), []).append(index)
    for line in lines.values():
        run = []
        for index in sorted(line, key=lambda index: float(words[index][0])):
            if run and float(words[index][0]) - float(words[run[-1]][2]) > SAME_LINE:
                yield [entries[at] for at in run]
                run = []
            run.append(index)
        yield [entries[at] for at in run]


def _the_view_labelled_by(regions, box):
    """
    Which region a label is the name of: the view it is printed under.

    `LABEL_STANDS_UNDER` is how far below its view a label may be printed,
    in points; the view above the label is preferred.
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


_IN_ORDER = ("the first one", "the second one", "the third one",
             "the fourth one", "the fifth one", "the sixth one")


def _where(rect, page_rect, ordinal):
    """
    A region with no label of its own: where it is, and which one it is there.
    A crop's name is what `seen_in` cites, so two crops sharing a name settle
    nothing — the 2023 sheet's bottom view and its notes are both lower centre.
    The ordinal counts a region's place among ones sharing its corner, page-
    read order; `_IN_ORDER` gives it in words for the Plan Gate.
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


_TOUCHES = 14.0
_HOPS = 12


def _leaders(page, words):
    """
    Where a callout's leader ends up, as geometry rather than as eyesight.

    `_TOUCHES` is how close a segment end must be to a callout to be its
    leader, in points; `_HOPS` is how far a leader is followed.
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
    Measured to the edge of the text box, not its middle — measured from the
    centre, `(R30)` on `mm25-p1-1.pdf` picks up a 17 pt arrowhead stem from a
    dimension 12 pt away; measured to the box, the shoulder lands where it is
    actually touching. A tie is broken by reach.
    """
    best, best_at = None, _TOUCHES
    for start, end in ends:
        for point, other in ((start, end), (end, start)):
            gap = _gap_to(anchor, point)
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


_SCALE = ("SCALE:", "SCALE")


def _millimetres_per_point(words, notes):
    """
    How much of the part one point of the page is, off the sheet's own scale. A
    PDF page is in points at 72/inch, so a 1:1 plot puts 0.3528 mm in every
    point — verified on the 2023 sheet, the arm's 15 mm measures 42.5 pt. A 1:2
    plot halves the part, and the title block's own ratio (`_SCALE`) is the
    only thing that says so.
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


A_TILT = 1.0

A_SIDESTEP = 1.0

THE_TEXT_GAP = 60.0

A_SPAN = 8.0

BESIDE = 14.0

WITHIN = 0.15

AGREED = 0.02

TRUE_TO = 0.6


def _spans(page):
    """
    Dimension lines across the breaks in them (the gap the number sits in).
    `A_TILT` bounds two segments' angle difference for one line, in degrees;
    `A_SIDESTEP` bounds their offset across it; `THE_TEXT_GAP` measures the
    2023 sheet's 108, broken by 31 pt of paper for a 22 pt string. `A_SPAN` is
    the shortest span worth measuring, below which are arrowheads and ticks.
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

    spans, gaps = [], []
    for pieces in lines.values():
        pieces.sort()
        run, broken = None, []
        for first, last, angle, standoff in pieces:
            if run is None:
                run = [first, last, angle, standoff]
            elif first - run[1] <= THE_TEXT_GAP:
                if first > run[1]:
                    broken.append((run[1], first))
                run[1] = max(run[1], last)
            else:
                _a_span_and_its_gaps(run, broken, spans, gaps)
                run, broken = [first, last, angle, standoff], []
        if run:
            _a_span_and_its_gaps(run, broken, spans, gaps)
    return ([span for span in spans if span[0] >= A_SPAN],
            [(span, gap) for span, gap in gaps if span[0] >= A_SPAN])


def _a_span_and_its_gaps(run, broken, spans, gaps):
    """One finished span, and every break the bridging closed over inside it."""
    span = _a_span(*run)
    spans.append(span)
    gaps.extend((span, _a_span(first, last, run[2], run[3]))
                for first, last in broken)


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
    What a printed string is worth as a length, or None if it is not one. `Ø`
    never arrives — it is drawn as geometry — so a bore's callout is the bare
    number, and `(R30)`/`R2 TYP` carry theirs with a letter and brackets. Only
    the leading number is taken: `6.6 X 8.0 THRU` is measured by the 6.6.
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
    Nearest, not shortest: preferring the shorter of two candidates loses the
    pairing the sheet actually drew. `BESIDE` bounds a number's offset from its
    own dimension line, in points (14 pt on this sheet's 9.5 pt text), and
    `WITHIN` bounds where along the span it must sit.
    """
    middle = ((word[0] + word[2]) / 2.0, (word[1] + word[3]) / 2.0)
    best, best_at = None, BESIDE
    for span in spans:
        along, forward, start = span
        away = (middle[0] - start[0], middle[1] - start[1])
        standoff = abs(-forward[1] * away[0] + forward[0] * away[1])
        down = forward[0] * away[0] + forward[1] * away[1]
        if standoff <= best_at and WITHIN * along <= down <= (1 - WITHIN) * along:
            best, best_at = span, standoff
    return best


def _the_gap_measuring(gaps, word):
    """
    The distance a number stands beside, where the arrows are drawn outside it.

    Where the number sits tells the two cases apart: a broken dimension line
    seats its number *in* the break, one drawn inside out seats it against a
    stub, outside the break.
    """
    middle = ((word[0] + word[2]) / 2.0, (word[1] + word[3]) / 2.0)
    corners = ((word[0], word[1]), (word[2], word[1]),
               (word[0], word[3]), (word[2], word[3]))
    best, best_at = None, BESIDE
    for span, gap in gaps:
        if gap[0] < A_SPAN:
            continue
        along, forward, start = span
        away = (middle[0] - start[0], middle[1] - start[1])
        standoff = abs(-forward[1] * away[0] + forward[0] * away[1])
        down = forward[0] * away[0] + forward[1] * away[1]
        opens = (forward[0] * (gap[2][0] - start[0])
                 + forward[1] * (gap[2][1] - start[1]))
        reach = sorted(forward[0] * (corner[0] - start[0])
                       + forward[1] * (corner[1] - start[1]) for corner in corners)
        if standoff > best_at or reach[-1] < 0.0 or reach[0] > along:
            continue
        if opens <= down <= opens + gap[0]:
            continue
        best, best_at = gap, standoff
    return best


def _measured(words, spans, gaps):
    """
    Every printed number beside the length of the line that measures it: the
    string, the span, and the scale voted on by them are all the draughtsman's
    own. `AGREED` is how close two scale votes count as the same -- 0.014 on
    0.7 mm/pt. `TRUE_TO` is how far a span may sit from its number, in mm --
    true pairings land inside 0.05 mm, false ones 7-100 mm out.
    """
    votes = []
    for at, word in enumerate(words):
        worth = _a_number(word[4])
        if worth is None:
            continue
        span = _the_span_measuring(spans, word) or _the_gap_measuring(gaps, word)
        if span:
            votes.append((at, worth, span[0], worth / span[0]))

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
    What the sheet's own dimension lines make of the scale its title block
    prints. Checked, never substituted. The title block is a statement of
    intent, the lines are what was plotted, and on all four sheets measured
    they agree to four figures — but a sheet re-plotted with its title block
    left alone is a silent failure this is the only thing that would notice.
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


A_SIGN = (0.35, 0.75, 0.40, 0.80)

A_SIGN_STANDS_OFF = 0.6

A_SIGN_IS_A_CURVE = 12


def _notation(page, words):
    """
    The symbols the sheet draws rather than types, by the word they stand
    against. `Ø` is geometry, not text; `A_SIGN` bounds the sign's size as a
    share of line height (6.3x7.7 pt on 12.9 pt text). `A_SIGN_STANDS_OFF`
    bounds its gap from the numeral (2.1 pt), and `A_SIGN_IS_A_CURVE` is its
    piece count (47), telling diameter from depth.
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


def _from_raster(path, into, max_edge):
    """
    A PNG or a photo: the same cutting up, off its ink, and no text layer.
    Worth doing without one: regions come from the picture rather than a
    draughtsman's file, so they are rougher but buy the same resolution that
    was failing. A crop over the cap is scaled down, never enlarged: enlarging
    adds pixels, adds no ink, and is billed for every one.
    """
    picture = Image.open(path).convert("RGB")
    width, height = picture.size
    grid, cell = _raster_grid(picture)
    regions = _raster_regions(grid, cell, width, height)

    overview = os.path.join(into, "sheet.png")
    scaled = picture.copy()
    scaled.thumbnail((OVERVIEW_EDGE, OVERVIEW_EDGE), Image.LANCZOS)
    scaled.save(overview)

    crops = []
    for at, (x0, y0, x1, y1) in enumerate(regions, start=1):
        piece = picture.crop((int(x0), int(y0), int(x1), int(y1)))
        if max(piece.size) > max_edge:
            piece.thumbnail((max_edge, max_edge), Image.LANCZOS)
        out = os.path.join(into, "crop-%02d.png" % at)
        piece.save(out)
        crops.append(Crop(name="%s of the sheet" % _raster_where(
            (x0, y0, x1, y1), width, height), path=out,
            rect=(x0, y0, x1, y1)))

    return Sheet(source=path, overview=overview, crops=crops,
                 leaders=[],
                 notes=["A raster has no text layer, so what is printed on this "
                        "sheet is not extracted and has to be read off the views."])


def _raster_grid(picture, cells_across=200):
    """
    The picture as marks against its own paper, read off its border, since a
    viewer's export comes out on black as often as on white and "dark means
    ink" then makes the background the drawing. A cell is ink by the count of
    its marked pixels, not their average, since a one-pixel grey line averages
    to paper over a cell and a view drawn in them is never found.
    """
    grey = picture.convert("L")
    cell = max(1, int(picture.width / cells_across))
    paper = _paper_of(grey.load(), grey.width, grey.height)
    marked = grey.point(lambda value: 255 if abs(value - paper) > INK else 0)
    across, down = max(1, picture.width // cell), max(1, picture.height // cell)
    share = marked.crop((0, 0, across * cell, down * cell)).resize((across, down), Image.BOX)
    pixels = share.load()
    enough = 255 * (INK_PIXELS - 0.5) / (cell * cell)

    grid = [[pixels[x, y] > enough for x in range(share.width)]
            for y in range(share.height)]
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
    """
    The marked cells of a picture as the boxes its views are in. Merged on
    their own ink and padded afterwards, in that order: padding first chains,
    since a margin wide enough to bring a dimension along is wide enough to
    touch the next view's margin, and the whole sheet comes back as one crop.
    Padded after, crops simply overlap, as they do at every other cut.
    """
    rows, columns = len(grid), len(grid[0])
    across = max(1, round(RASTER_REACH * width / cell))
    down = max(1, round(RASTER_REACH * height / cell))
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
                for next_row in range(at_row - down, at_row + down + 1):
                    for next_column in range(at_column - across, at_column + across + 1):
                        if not (0 <= next_row < rows and 0 <= next_column < columns):
                            continue
                        if grid[next_row][next_column] and not seen[next_row][next_column]:
                            seen[next_row][next_column] = True
                            stack.append((next_row, next_column))
            if len(cells) < 6:
                continue
            xs = [c[1] for c in cells]
            ys = [c[0] for c in cells]
            box = (min(xs) * cell, min(ys) * cell,
                   (max(xs) + 1) * cell, (max(ys) + 1) * cell)
            if not _a_corner_mark(box, width, height):
                found.append(box)

    pad = max(cell * 4, int(RASTER_MARGIN * max(width, height)))
    return [(max(0, box[0] - pad), max(0, box[1] - pad),
             min(width, box[2] + pad), min(height, box[3] + pad))
            for box in _merged_boxes(found)]


def _merged_boxes(boxes):
    """
    Regions joined when one overlaps more than half of the other, until none
    do: two views whose ink genuinely overlaps would otherwise be sent as two
    crops each mostly showing the other. Boxes that merely touch stay apart,
    since touching boxes chain view to view through the title block into one
    crop of the whole sheet.
    """
    boxes = list(boxes)
    joined = True
    while joined:
        joined = False
        for one in range(len(boxes)):
            for other in range(one + 1, len(boxes)):
                if not _mostly_overlap(boxes[one], boxes[other]):
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


def _mostly_overlap(one, other):
    shared = (max(0, min(one[2], other[2]) - max(one[0], other[0]))
              * max(0, min(one[3], other[3]) - max(one[1], other[1])))
    smaller = min((one[2] - one[0]) * (one[3] - one[1]),
                  (other[2] - other[0]) * (other[3] - other[1]))
    return shared > RASTER_JOIN * smaller


def _a_corner_mark(box, width, height):
    """A registration mark: a speck of ink in a corner of the frame, small both ways."""
    small = (box[2] - box[0]) < CORNER_MARK * width and (box[3] - box[1]) < CORNER_MARK * height
    at_a_side = box[0] < CORNER_MARK * width or box[2] > (1 - CORNER_MARK) * width
    at_an_end = box[1] < CORNER_MARK * height or box[3] > (1 - CORNER_MARK) * height
    return small and at_a_side and at_an_end


def _raster_where(box, width, height):
    middle_x = (box[0] + box[2]) / 2
    middle_y = (box[1] + box[3]) / 2
    down = ("the upper", "the middle", "the lower")[min(2, int(3 * middle_y / height))]
    across = ("left", "centre", "right")[min(2, int(3 * middle_x / width))]
    return "%s %s" % (down, across) if across != "centre" else "%s centre" % down


def _as_tuple(rect):
    return (round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1))
