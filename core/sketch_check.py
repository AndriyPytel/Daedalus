"""
The reading, assembled, and what the solver makes of it.

Everything before this checks a reading against another reading. The sheet is
measured, the model is asked what it sees, the two are compared, and where they
disagree a person settles it — which is the right rule for a number nobody can
recompute and no rule at all for geometry, because geometry can be built and a
solver has an opinion of its own.

`sheet_reader` now hands over a closed walk: lines, arcs and circles in
millimetres of the part, in the order the silhouette was walked round, and a
hole a callout's leader reaches carries the size the draughtsman printed rather
than the size the pixels measure. That is a `Sketch` — `addGeometry` takes
exactly those three — and once it is one, the solver answers a question nothing
else in the pipeline can put: can what was read be true at once?

The answer is not the solver's return code. Two numbers that cannot both be
true rarely make a sketch *unsolvable* — the solver satisfies them by moving
the geometry, and the geometry is the one thing here that was measured. So the
verdict is how far it had to move: build the walk where the pixels put it,
constrain it with what the sheet prints, solve, and measure the distance
between the two.

What the sheet prints is two things and the second one is most of a drawing. A
diameter arrives where a callout's leader reached a traced hole, which happens
once on four sheets; a **distance** arrives wherever a dimension's two
extension lines reach two faces of the outline, which is 13 of them across 12
outlines. `108` read as `100` is a distance, and until they were written there
was nothing in the sketch for it to be wrong about.

Measured across all four sheets in `tests/modelmania`, 96 outlines, with every
size and every distance the sheets could reach them with: the median moves
0.27 mm, nine in ten move under 0.75 mm and the worst moves 1.07 mm — that is
the slack in a fit through pixels 0.2 mm across, and it is what agreement looks
like. The \u00d840 hole of `mm25-p1-1.pdf`, whose callout the tracing reads a
millimetre under, moves 0.54 mm onto the printed size. A bore traced in two
arcs and snapped to two different callouts — 42 against 50, which is the shape
of the reading this exists to catch — moves 9.88 mm.

Disposable throughout. The document is made here, closed here, and never the
user's; nothing here writes a Step, and nothing here reaches the feature tree.
It is made under a name that says what it is, which is how a stray one is
recognised. What comes back is a verdict about a *reading*.

A traced entity that has to move further than `A_SHIFT` onto the sheet's own
numbers is a fact about the reading rather than the slack in a fit. Two
millimetres, measured over 96 outlines on the four sheets: the median moves
0.27 mm, nine in ten under 0.62 mm and the worst 1.21 mm, where a pixel is
0.2 mm across and an arc's ends are pulled onto its own circle. The reading it
has to catch moves 9.88 mm.

What crosses the pipe is named field by field rather than dumped wholesale, so
what crosses is what is used and a new field on `Entity` does not silently
become part of the contract between the two processes. A point crosses as a
list, because JSON has no tuple and the reporting formats a point with `%` —
a list there is a TypeError, not a rounding difference.

**And it is made in the Preflight child, never in the addon's own process.**
`checked` runs where it is called; every caller upstream goes through
`checked_by`, which sends the outline down the pipe and reads the verdict back.
Two reasons, and either alone is enough (FCC-126): the phases that read a sheet
run on the loop's worker thread, where a Document mutation is how FreeCAD gets
taken down (`core/apply.py`); and `Sketcher.Constraint` is the call that
segfaults (FCC-19), fed here with geometry measured off a drawing. A verdict is
worth a child process. It is not worth the user's FreeCAD.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace

START, END = 1, 2

A_DOCUMENT = "copilot-reading-check"

A_SHIFT = 2.0


@dataclass(frozen=True)
class Trouble:
    """One constraint the solver objected to, back at the entity it came from."""

    at: int
    entity: object = None
    printed: float = None
    said: str = ""


@dataclass(frozen=True)
class Verdict:
    """
    What one outline came to when it was built.

    `solved` is what `Sketch.solve()` returned, where zero is solved and
    anything else is not, and `dof` is what the reading leaves undetermined,
    which is expected to be large: a profile off one view carries no position
    and nothing here invents one. `printed` counts the sizes written from the
    sheet's own text rather than from the pixels, and `distances` the printed
    distances that reached two points of this outline — every other number on a
    sheet is one of these, so until they arrive the check has almost nothing of
    the drawing to hold the tracing against. `disagrees` is each of those the
    tracing does not agree with before any solving, which is the plainest thing
    here: what the sheet prints between two faces against what the pixels put
    between them. `moved` is how far the furthest entity had to move to satisfy
    them, in millimetres, and `shifted` each entity that moved further than the
    fit can account for, which is the verdict this exists for.
    """

    view: str = ""
    entities: int = 0
    geometry: int = 0
    solved: int = 0
    dof: int = 0
    conflicts: list = field(default_factory=list)
    redundant: list = field(default_factory=list)
    printed: int = 0
    distances: int = 0
    disagrees: list = field(default_factory=list)
    moved: float = 0.0
    shifted: list = field(default_factory=list)


def available():
    """Whether there is a FreeCAD here to build in."""
    try:
        import FreeCAD  # noqa: F401
        import Part  # noqa: F401
        import Sketcher  # noqa: F401
    except ImportError:  # pragma: no cover - exercised by the machines without it
        return False
    return True


def checked(outline, view="", between=()):
    """
    One traced outline built as a sketch, and the solver's verdict on it.

    Builds in the process that calls it, which is the Preflight child and
    nothing else. `checked_by` is the entry point for everybody upstream —
    see the note at the top of this module.

    Built again where the solver did not converge, up to `_TRIES` times, which
    is three: the failure is a coin the solver tosses at each build, 1.5 in a
    hundred once is 3 in a million three times over, and a reading nothing can
    solve is not made solvable by a fourth document. A
    reading is under-constrained on purpose — one view carries no position, so
    the walk is free to move as a whole — and from a start the solver picks for
    itself the same sketch comes out solved or not solved run to run: measured
    on the two-arc bore below, three builds in two hundred came back `-3`. A
    second `solve()` on the same sketch is not a second try, because the start
    it failed from is where it now sits; a fresh build is. What that costs is a
    document nobody sees, and what it buys is the one answer this module has no
    other way of telling apart — geometry that stayed where the pixels put it
    because the sheet agrees with itself, from geometry that stayed there
    because the solver gave up. The first is a reading with nothing wrong with
    it; the second measured nothing at all and read as the first.
    """
    if not available() or not outline.entities:
        return None

    verdict = _built(outline, view, between)
    for _ in range(_TRIES - 1):
        if verdict.solved == 0:
            break
        verdict = _built(outline, view, between)
    return verdict


_TRIES = 3


def _built(outline, view, between):
    """One build of the reading, solved once, measured once."""
    import FreeCAD

    document = FreeCAD.newDocument(A_DOCUMENT)
    try:
        sketch = document.addObject("Sketcher::SketchObject", "Reading")
        for entity in outline.entities:
            sketch.addGeometry(_as_geometry(entity), False)
        _joined(sketch, outline)
        written = _sized(sketch, outline)
        across, disagrees = _measured_across(sketch, outline, view, between)
        written.update(across)
        document.recompute()
        solved = sketch.solve()
        sketch.recompute()
        shifted = _how_far_it_moved(sketch, outline)
        return Verdict(
            view=view,
            entities=len(outline.entities),
            geometry=sketch.GeometryCount,
            solved=solved,
            dof=sketch.DoF,
            conflicts=_troubles(sketch.ConflictingConstraints, written, outline),
            redundant=_troubles(sketch.RedundantConstraints, written, outline),
            printed=sum(1 for entity in outline.entities
                        if entity.printed is not None),
            distances=sum(1 for at in written
                          if written[at][0] == "distance"),
            disagrees=disagrees,
            moved=max([away for away, _ in shifted] or [0.0]),
            shifted=[trouble for away, trouble in shifted if away > A_SHIFT])
    finally:
        FreeCAD.closeDocument(document.Name)


_OF_AN_ENTITY = ("kind", "size", "printed", "start", "end", "centre", "radius",
                 "diameter")
_OF_A_DISTANCE = ("printed", "first", "second", "view", "text")
_A_POINT = ("start", "end", "centre", "first", "second")
_OF_A_TROUBLE = ("at", "printed", "said")


def checked_by(worker, outline, view="", between=()):
    """
    One outline built in the Preflight child, and the solver's verdict on it.

    None where there was no verdict to be had — an outline with nothing in it,
    or a child that died or hung on this reading. Nothing checked and nothing
    passed are the same answer here: the description simply gains no conflict.
    """
    if not outline.entities:
        return None
    return from_response(worker.reading(as_request(outline, view, between)))


def as_request(outline, view="", between=()):
    """
    The outline, the view it was traced in, and the sheet's distances, as JSON.

    `do` is what the child dispatches on: a Step, or one of these. The two
    halves are separate processes and this word is the whole of their agreement
    about which is which.
    """
    return {"do": "reading", "view": view,
            "outline": {"closed": bool(outline.closed),
                        "entities": [_written(entity, _OF_AN_ENTITY)
                                     for entity in outline.entities]},
            "between": [_written(entry, _OF_A_DISTANCE) for entry in between]}


def from_request(request):
    """The same three back, as plain objects with the same attribute names."""
    outline = request.get("outline") or {}
    return (SimpleNamespace(closed=bool(outline.get("closed")),
                            entities=[_read(entity)
                                      for entity in outline.get("entities") or ()]),
            request.get("view") or "",
            [_read(entry) for entry in request.get("between") or ()])


def as_response(verdict):
    """The verdict as JSON, or nothing where there was no verdict."""
    if verdict is None:
        return {"verdict": None}
    said = dict(vars(verdict))
    for named in ("conflicts", "redundant", "disagrees", "shifted"):
        said[named] = [_written(trouble, _OF_A_TROUBLE) for trouble in said[named]]
    return {"verdict": said}


def from_response(response):
    """
    The child's reply as a Verdict, or None.

    A Trouble arrives without the `entity` it was raised against: the entity is
    the caller's own object, it does not cross a pipe, and nothing upstream
    reads it — what a Trouble is read for is `said`.
    """
    said = (response or {}).get("verdict")
    if not said:
        return None
    said = dict(said)
    for named in ("conflicts", "redundant", "disagrees", "shifted"):
        said[named] = [Trouble(**trouble) for trouble in said[named]]
    return Verdict(**said)


def _written(what, fields):
    return {named: list(value) if named in _A_POINT and value is not None else value
            for named, value in ((named, getattr(what, named, None))
                                 for named in fields)}


def _read(said):
    return SimpleNamespace(**{
        named: tuple(value) if named in _A_POINT and value is not None else value
        for named, value in said.items()})


A_FOOT = 2.0

A_SQUARE_ON = 0.99

A_DISTANCE = 1.5


def _measured_across(sketch, outline, view, between):
    """
    Every printed distance that reaches two faces of this outline, written.

    This is the drawing arriving in the sketch. Until it does, the only number
    the check holds the tracing against is a diameter a leader happened to
    reach — one of them on four sheets — and every other thing a drawing says
    is a distance.

    Written between two *faces* and never between two corners. A face is drawn
    with a chamfer at each end, so which corner a foot is nearest is a lottery:
    measured on SECTION A-A of `mm25-p1-1.pdf`, corner matching turns a 50 mm
    dimension into 47.01 mm of traced geometry and reports 2.07 mm of movement
    the sheet never asked for, and matching along the dimension alone lets a
    foot reach a face on the far side of the part and reports 34 mm. So the
    foot names the line it stands on, and the constraint holds those two lines
    apart in the one direction the dimension is measured in.

    Which is why only a dimension drawn square to the sheet is written: a
    diagonal one measures between two points and this cannot tell which.

    The value written is the printed one, always. The two faces are the
    tracing's; how far apart they have to be is the sheet's. A constraint the
    sketch already says is dropped rather than written twice.

    `A_DISTANCE` is how far the pixels may be from what the sheet prints
    between two faces, in millimetres, before that is a fact about the reading.
    One and a half, measured on the four sheets: the 13 distances that reach a
    traced outline are 0.07 to 1.08 mm out, and the 1.08 is the bias itself — a
    face is traced to the inside of the line that draws it, so a width reads
    about a millimetre under. The reading this exists to catch is 8 mm out.
    """
    import Sketcher

    written, disagrees = {}, []
    for entry in between:
        if entry.view and view and entry.view != view:
            continue
        squarely = _squarely(entry)
        if squarely is None:
            continue
        named, way = squarely
        first = _the_face_at(outline, entry.first, way)
        second = _the_face_at(outline, entry.second, way)
        if first is None or second is None or first == second:
            continue
        how_far = ((entry.second[0] - entry.first[0]) * way[0]
                   + (entry.second[1] - entry.first[1]) * way[1])
        try:
            at = sketch.addConstraint(Sketcher.Constraint(
                named, first, START, second, START,
                entry.printed if how_far > 0 else -entry.printed))
        except (ValueError, RuntimeError):
            continue
        written[at] = ("distance", entry, entry.printed)
        traced = _across(outline.entities[first].start,
                         outline.entities[second].start, way)
        if abs(traced - entry.printed) > A_DISTANCE:
            disagrees.append(Trouble(
                at=at, printed=entry.printed,
                said="the sheet prints %s between these two faces and the "
                     "pixels put %.2f mm between them"
                     % (entry.text or entry.printed, traced)))
    return written, disagrees


def _across(one, other, way):
    """How far apart two points are in the direction a dimension measures."""
    return abs((other[0] - one[0]) * way[0] + (other[1] - one[1]) * way[1])


def _squarely(entry):
    """
    The constraint one printed distance is, where it is drawn square, or None.

    A dimension drawn along the sheet measures in one direction only, and the
    other coordinate of the two faces it measures is not its business — which
    is what makes a face rather than a corner enough to write it against.

    Square is `A_SQUARE_ON`, the cosine of the angle between the dimension and
    the face it measures to. A drawing is plotted square, so this is rounding in
    the file rather than a second direction.
    """
    import math

    across = (entry.second[0] - entry.first[0], entry.second[1] - entry.first[1])
    long_enough = math.hypot(*across) or 1.0
    way = (across[0] / long_enough, across[1] / long_enough)
    if abs(way[0]) > A_SQUARE_ON:
        return "DistanceX", (1.0, 0.0)
    if abs(way[1]) > A_SQUARE_ON:
        return "DistanceY", (0.0, 1.0)
    return None


def _the_face_at(outline, foot, way):
    """
    The traced line a dimension's foot stands on, or None.

    The face has to be square to the dimension — a line the dimension measures
    *to* is a line it measures *across* — and the foot has to be on it, which
    is a point-to-segment distance and not a distance to either end of it.

    None is the usual answer and the honest one: a foot on a view whose body is
    not traced, or on a face the tracer only got part of, names nothing here.

    `A_FOOT` is how far the foot may be from the face it names, in millimetres,
    measured in the direction the dimension measures. It cannot be wider than
    the movement that counts as a finding, or a match at the edge of the band is
    indistinguishable from the thing this exists to report — and measured, that
    is exactly what happens: at 2.6 mm four more dimensions find a face, all
    four then cost the tracing 3.6 mm, and one of them contradicts another
    dimension outright. At 2.0 the same sheets write 13 distances across 12
    outlines and the worst movement is 1.07 mm.
    """
    best, best_gap = None, A_FOOT
    for at, entity in enumerate(outline.entities):
        if entity.kind != "line":
            continue
        along = (entity.end[0] - entity.start[0], entity.end[1] - entity.start[1])
        long_enough = (along[0] ** 2 + along[1] ** 2) ** 0.5
        if not long_enough:
            continue
        if abs((along[0] * way[0] + along[1] * way[1]) / long_enough) > 1 - A_SQUARE_ON:
            continue
        gap = abs((foot[0] - entity.start[0]) * way[0]
                  + (foot[1] - entity.start[1]) * way[1])
        if gap < best_gap:
            best, best_gap = at, gap
    return best


def _how_far_it_moved(sketch, outline):
    """
    Each entity against where the pixels put it, after the solve, in millimetres.

    This is the verdict. Two numbers that cannot both be true seldom make a
    sketch unsolvable — the solver satisfies them by moving the geometry, and
    the geometry is the one thing here that was measured rather than read. So
    what is asked is not whether it solved but what it cost.
    """
    import math

    found = []
    for at, entity in enumerate(outline.entities):
        built = sketch.Geometry[at]
        if entity.kind == "line":
            away = max(math.dist((built.StartPoint.x, built.StartPoint.y),
                                 entity.start),
                       math.dist((built.EndPoint.x, built.EndPoint.y),
                                 entity.end))
        else:
            was = (entity.diameter / 2.0 if entity.kind == "circle"
                   else entity.radius)
            away = max(math.dist((built.Center.x, built.Center.y),
                                 entity.centre),
                       abs(built.Radius - was))
        found.append((away, Trouble(
            at=at, entity=entity, printed=entity.printed,
            said="the %s traced at %s moved %.2f mm to sit where %s puts it"
                 % (entity.kind, _measured(entity), away,
                    "\u00d8%g printed on the sheet" % entity.printed
                    if entity.printed is not None else "the walk"))))
    return found


def _spelt(point):
    return "(%.2f, %.2f)" % point


def _measured(entity):
    """What the pixels made of one entity, in the words it is reported in."""
    if entity.kind == "line":
        return "%.2f mm long" % entity.size
    if entity.kind == "circle":
        return "\u00d8%.2f" % entity.diameter
    return "R%.2f" % entity.radius


def _as_geometry(entity):
    """One measured entity as the piece of sketch geometry it already is."""
    import math

    import FreeCAD
    import Part

    up = FreeCAD.Vector(0, 0, 1)
    if entity.kind == "line":
        return Part.LineSegment(FreeCAD.Vector(entity.start[0], entity.start[1], 0),
                                FreeCAD.Vector(entity.end[0], entity.end[1], 0))
    centre = FreeCAD.Vector(entity.centre[0], entity.centre[1], 0)
    if entity.kind == "circle":
        return Part.Circle(centre, up, entity.diameter / 2.0)
    return Part.ArcOfCircle(
        Part.Circle(centre, up, entity.radius),
        math.atan2(entity.start[1] - entity.centre[1],
                   entity.start[0] - entity.centre[0]),
        math.atan2(entity.end[1] - entity.centre[1],
                   entity.end[0] - entity.centre[0]))


def _joined(sketch, outline):
    """
    The walk itself, as coincidences: this entity ends where the next begins.

    This is the whole of what the order was kept for. A circle is a walk of its
    own and joins nothing, and an outline that was not found closed is not
    closed here — forcing one shut is inventing geometry, and the solver would
    then object to this module rather than to the sheet.
    """
    import Sketcher

    entities = outline.entities
    for at, entity in enumerate(entities):
        onward = (at + 1) % len(entities)
        if onward == 0 and not outline.closed:
            continue
        if entity.kind == "circle" or entities[onward].kind == "circle":
            continue
        if at == onward:
            continue
        sketch.addConstraint(Sketcher.Constraint("Coincident", at, END,
                                                 onward, START))


def _sized(sketch, outline):
    """
    A size for every round thing, printed where the sheet printed one.

    The one direction the arithmetic runs: the trace found the feature and the
    text layer says how big it is. Where no callout reached it the traced
    measurement is written instead, and the verdict says how many of each.
    """
    import Sketcher

    written = {}
    for at, entity in enumerate(outline.entities):
        if entity.kind == "circle":
            wide = entity.printed if entity.printed is not None else entity.diameter
            named = "Diameter"
        elif entity.kind == "arc":
            wide = (entity.printed / 2.0 if entity.printed is not None
                    else entity.radius)
            named = "Radius"
        else:
            continue
        written[sketch.addConstraint(
            Sketcher.Constraint(named, at, wide))] = ("size", entity, wide)
    return written


def _troubles(indices, written, outline):
    """Each index the solver objected to, back at the entity it was written for."""
    found = []
    for at in indices:
        kind, what, wide = written.get(at, (None, None, None))
        if kind == "distance":
            found.append(Trouble(
                at=at, printed=what.printed,
                said="the %s the sheet prints between %s and %s"
                     % (what.text or what.printed, _spelt(what.first),
                        _spelt(what.second))))
        elif kind == "size":
            found.append(Trouble(
                at=at, entity=what,
                printed=what.printed,
                said="the %s the sheet prints \u00d8%g against"
                     % (what.kind, what.printed) if what.printed is not None
                     else "the traced %s measured %.2f"
                          % (what.kind, wide * (2 if what.kind == "arc" else 1))))
        else:
            found.append(Trouble(
                at=at,
                said="the walk itself: one entity of this outline does not end "
                     "where the next begins"))
    return found
