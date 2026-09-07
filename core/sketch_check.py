"""Builds a Sketch from the traced outline; the solver says whether the reading is self-consistent
— geometry can be built, unlike a person-vs-person diff.

Constraints:
Verdict is movement distance, not solver return code — a bad solve moves geometry to satisfy it.
A_SHIFT is the pass/fail line for how far a traced entity may move onto the sheet's own numbers.
Runs only in the Preflight child (FCC-126) — Document mutation on the loop thread crashes FreeCAD,
and Sketcher.Constraint itself can segfault (FCC-19).
Wire fields cross as plain lists — JSON has no tuple, and the report format uses `%`.

Measured (tests/modelmania, 4 sheets, 142 outlines): movement 0.15-0.30mm median, 0.50-0.75mm p90,
1.0-1.5mm worst; a wrong double-callout snap (42 vs 50) moves 9.88mm.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace

from core.sheet_reader import A_DISTANCE, spelt

START, END = 1, 2

A_DOCUMENT = "copilot-reading-check"

A_SHIFT = 2.0


@dataclass(frozen=True)
class Trouble:
    """
    One constraint the solver objected to, back at the entity it came from.

    `at` is an index into the walk and `constraint` an index into the sketch's
    own constraints, and they are two fields because they are two coordinate
    systems: a rectangle is four entities and six constraints, so one number
    meaning both is a resolution computed and thrown away. `at` is the field
    every Trouble in a verdict is read with; `constraint` is None where there
    was no constraint, which is every entity that merely moved.

    The solver counts its constraints from one — `ConflictingConstraints` on
    that rectangle comes back `[5, 6]` for the fifth and sixth — and
    `constraint` counts from zero, the way `Sketch.Constraints` is indexed.
    """

    at: int = None
    constraint: int = None
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
        written = _joined(sketch, outline)
        written.update(_sized(sketch, outline))
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
_OF_A_TROUBLE = ("at", "constraint", "printed", "said")


def checked_by(worker, outline, view="", between=(), timeout=None):
    """
    One outline built in the Preflight child, and the solver's verdict on it.

    None where there was no verdict to be had — an outline with nothing in it,
    or a child that died or hung on this reading. Nothing checked and nothing
    passed are the same answer here: the description simply gains no conflict.

    `timeout` is what is left of the sweep's own budget, so the last outline
    asked cannot outlive it.
    """
    if not outline.entities:
        return None
    return from_response(
        worker.reading(as_request(outline, view, between), timeout))


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

A_GAP = 3.0

A_SQUARE_ON = 0.99


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

    `A_DISTANCE` is the tracing's own slack, and `sheet_reader` is where it is
    measured and where it is written down: how far the pixels may be from what
    the sheet prints between two faces, before that is a fact about the reading.
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
        written[at] = ("distance", entry, entry.printed, first)
        traced = _across(outline.entities[first].start,
                         outline.entities[second].start, way)
        if abs(traced - entry.printed) > A_DISTANCE:
            disagrees.append(Trouble(
                at=first, constraint=at, entity=outline.entities[first],
                printed=entry.printed,
                said="the sheet prints %s between %s and %s and the "
                     "pixels put %.2f mm between them"
                     % (entry.text or entry.printed, spelt(entry.first),
                        spelt(entry.second), traced)))
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
    is a point-to-segment distance and not a distance to the infinite line the
    segment lies on. Measured to the line, a face's own extent along its own
    direction is never asked about, and a collinear face anywhere else in the
    view — the far side of a C, the other flange of a bracket — stands at the
    same distance as the face the foot is actually on and can win the match.

    So the projection along the face is clamped to the face, and how far the
    clamp had to move it — how far past the face's own ends the foot stands —
    is asked before anything else. The two directions are two bands because
    they are two quantities: across the dimension is the tracing against the
    sheet, and along the face is a draughtsman's gap.

    None is the usual answer and the honest one: a foot on a view whose body is
    not traced, or on a face the tracer only got part of, names nothing here.

    `A_FOOT` is how far the foot may be from the face it names across the
    dimension, in millimetres. It cannot be wider than the movement that counts
    as a finding, or a match at the edge of the band is indistinguishable from
    the thing this exists to report — and measured, that is exactly what
    happens: at 2.6 mm four more dimensions find a face, all four then cost the
    tracing 3.6 mm, and one of them contradicts another dimension outright.

    `A_GAP` is the other direction and a different quantity: how far past the
    end of a face a foot may stand, in millimetres, because an extension line
    is drawn with a gap between it and the part it is drawn off, so a foot
    stands short of the face it names by that gap. Measured on the four sheets,
    the feet that stand on the face they name are 0 to 2.63 mm past its end and
    the next one is 3.17, so 3.0 is the empty ground between them — and the
    answer does not turn on it: anything from 2.7 to 8 mm writes the same 11
    distances, and 12 lets in two feet 11 mm past the end of the face they are
    then measured to.

    At 2.0 across and 3.0 along, the four sheets write 11 distances across 10
    outlines, and the measured worst movement stays under 1.5 mm.
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
        if _past_the_end(foot, entity.start, along, long_enough) > A_GAP:
            continue
        gap = abs((foot[0] - entity.start[0]) * way[0]
                  + (foot[1] - entity.start[1]) * way[1])
        if gap < best_gap:
            best, best_gap = at, gap
    return best


def _past_the_end(foot, start, along, long_enough):
    """
    How far beyond a segment's own two ends a point stands, in millimetres.

    The projection of the point onto the segment's direction, clamped to the
    segment, and how far the clamp had to move it. Zero anywhere on the face.
    """
    reach = ((foot[0] - start[0]) * along[0]
             + (foot[1] - start[1]) * along[1]) / long_enough
    return max(0.0, -reach, reach - long_enough)


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

    Written down against the entity each one joins, the same as a size and a
    distance are, so that a walk the solver cannot close names the place in the
    walk it could not close.
    """
    import Sketcher

    written = {}
    entities = outline.entities
    for at, entity in enumerate(entities):
        onward = (at + 1) % len(entities)
        if onward == 0 and not outline.closed:
            continue
        if entity.kind == "circle" or entities[onward].kind == "circle":
            continue
        if at == onward:
            continue
        written[sketch.addConstraint(
            Sketcher.Constraint("Coincident", at, END,
                                onward, START))] = ("walk", entity, None, at)
    return written


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
            Sketcher.Constraint(named, at, wide))] = ("size", entity, wide, at)
    return written


def _troubles(indices, written, outline):
    """
    Each constraint the solver objected to, back at the entity it was written for.

    The solver counts its constraints from one and `written` is keyed by what
    `addConstraint` returned, which counts from zero — so the index arriving
    here names the constraint before it, and reading it as it comes reports the
    coincidence for the dimension and the dimension for nothing at all.

    A distance is written between two faces and comes back at the first of
    them, which is the one its own foot named.
    """
    found = []
    for index in indices:
        at = index - 1
        kind, what, wide, entity_at = written.get(at, (None, None, None, None))
        entity = (outline.entities[entity_at] if entity_at is not None
                  else None)
        if kind == "distance":
            found.append(Trouble(
                at=entity_at, constraint=at, entity=entity, printed=what.printed,
                said="the %s the sheet prints between %s and %s"
                     % (what.text or what.printed, spelt(what.first),
                        spelt(what.second))))
        elif kind == "size":
            found.append(Trouble(
                at=entity_at, constraint=at, entity=entity,
                printed=what.printed,
                said="the %s the sheet prints \u00d8%g against"
                     % (what.kind, what.printed) if what.printed is not None
                     else "the traced %s measured %.2f"
                          % (what.kind, wide * (2 if what.kind == "arc" else 1))))
        else:
            found.append(Trouble(
                at=entity_at, constraint=at, entity=entity,
                said="the walk itself: one entity of this outline does not end "
                     "where the next begins"))
    return found
