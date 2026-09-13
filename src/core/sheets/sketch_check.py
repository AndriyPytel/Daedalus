"""
Builds a Sketch from the traced outline; the solver says whether the
reading is self-consistent -- geometry can be built, unlike a
person-vs-person diff. Verdict is movement distance, not solver return
code, since a bad solve moves geometry to satisfy it; `A_SHIFT` is the
pass/fail line for how far a traced entity may move onto the sheet's own
numbers; it runs only in the Preflight child, since Document mutation on
the loop thread crashes FreeCAD and Sketcher.Constraint itself can
segfault; and wire fields cross as plain lists, since JSON has no tuple
and the report format uses `%`. Measured (four sheets, 142 outlines):
movement 0.15-0.30mm median, 0.50-0.75mm p90, 1.0-1.5mm worst, a wrong
double-callout snap (42 vs 50) moves 9.88mm, and it is built again where
the solver does not converge, up to `_TRIES` (3) times, since a 1.5%
failure rate compounds to 3 in a million over three tries and a reading
nothing can solve is not made solvable by a fourth.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace

from core.sheets.sheet_reader import A_DISTANCE, spelt

START, END = 1, 2

A_DOCUMENT = "copilot-reading-check"

A_SHIFT = 2.0


@dataclass(frozen=True)
class Trouble:
    """
    One constraint the solver objected to, back at the entity it came
    from. `at` is an index into the walk; `constraint` an index into the
    sketch's own constraints, two fields because they are two coordinate
    systems, since one number meaning both is a resolution thrown away.
    `constraint` is None where there was no constraint, every entity that
    merely moved, and the solver counts constraints from one while
    `constraint` counts from zero, as `Sketch.Constraints` is indexed.
    """

    at: int = None
    constraint: int = None
    entity: object = None
    printed: float = None
    said: str = ""


@dataclass(frozen=True)
class Verdict:
    """
    What one outline came to when it was built: `solved` is `Sketch.solve()`'s
    return, zero meaning solved, and `dof` is what the reading leaves undetermined,
    expected to be large since a profile off one view carries no position.
    `printed` counts sizes written from the sheet's text; `distances` the printed
    distances reaching two points of this outline; `disagrees` is each of those the
    tracing does not agree with before solving. `moved` is how far the furthest
    entity moved to satisfy them, in mm; `shifted` is each entity moved further
    than the fit can account for -- the verdict this exists for.
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
    One traced outline built as a sketch, and the solver's verdict on
    it. Builds in the process that calls it, the Preflight child and
    nothing else -- `checked_by` is the entry point for everybody
    upstream. A reading is under-constrained on purpose, since one view
    carries no position and the walk is free to move as a whole; from a
    start the solver picks itself, the same sketch can come out solved
    or not run to run -- measured on a two-arc bore, three builds in two
    hundred came back `-3`.
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
    One outline built in the Preflight child, and the solver's verdict
    on it: none where there was no verdict to be had -- an outline with
    nothing in it, or a child that died or hung on this reading. Nothing
    checked and nothing passed are the same answer here: the description
    simply gains no conflict. `timeout` is what is left of the sweep's
    own budget, so the last outline asked cannot outlive it.
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
    Every printed distance that reaches two faces of this outline,
    written. Written between two *faces*, not corners. Measured on
    SECTION A-A of `mm25-p1-1.pdf`: corner matching turns 50 mm into
    47.01 mm traced and reports 2.07 mm of spurious movement, and only
    dimensions square to the sheet.
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
    A dimension along the sheet measures one direction only, and the other
    coordinate is not its business — a face rather than a corner is enough to
    write it against. Square is `A_SQUARE_ON`, the cosine between dimension and
    face; plotted square makes this rounding, not a second direction.
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
    The traced line a dimension's foot stands on, or None. `A_FOOT` (2.0 mm)
    bounds how far across the dimension; at 2.6 mm four more dimensions find a
    face and one contradicts another. `A_GAP` (3.0 mm) bounds how far past the
    face's end -- measured 0-2.63 mm known, next at 3.17 mm, and at 2.0x3.0,
    four sheets write 11 distances, worst movement under 1.5 mm.
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
    Each entity against where the pixels put it, after the solve, in
    millimetres. This is the verdict. Two numbers that cannot both be true
    seldom make a sketch unsolvable — the solver satisfies them by moving the
    geometry — so what is asked is not whether it solved but what it cost.
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
    The walk itself, as coincidences: this entity ends where the next begins --
    the whole of what the order was kept for. A circle joins nothing and an
    outline not found closed is not closed here -- forcing one shut would
    invent geometry. Written against the entity each one joins, so a walk that
    cannot close names the place it could not close.
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
    """A size for every round thing, printed where the sheet printed one."""
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
    Each constraint the solver objected to, back at the entity it was written
    for. The solver counts constraints from one; `written` is keyed by what
    `addConstraint` returned, from zero — the index here names the constraint
    before it, so a coincidence reads for the dimension after it. A distance
    between two faces comes back at the first, the one its foot named.
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
