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

Measured across all four sheets in `tests/modelmania`, 96 outlines: the median
moves 0.27 mm, nine in ten move under 0.62 mm and the worst moves 1.21 mm —
that is the slack in a fit through pixels 0.2 mm across, and it is what
agreement looks like. The \u00d840 hole of `mm25-p1-1.pdf`, whose callout the
tracing reads a millimetre under, moves 0.54 mm onto the printed size. A bore
traced in two arcs and snapped to two different callouts — 42 against 50, which
is the shape of the reading this exists to catch — moves 9.88 mm.

Disposable throughout. The document is made here, closed here, and never the
user's; nothing here writes a Step, and nothing here reaches the feature tree.
What comes back is a verdict about a *reading*.
"""
from dataclasses import dataclass, field

# The two ends of a piece of sketch geometry, as the Sketcher numbers them.
START, END = 1, 2

# The name the throwaway document is made under. It never reaches the user, but
# a name that says what it is is what a stray one is recognised by.
A_DOCUMENT = "copilot-reading-check"

# How far the solver may move a traced entity onto the sheet's own numbers
# before that is a fact about the reading rather than the slack in a fit, in mm.
# Measured over 96 outlines on the four sheets: median 0.27, nine in ten under
# 0.62, worst 1.21 — a pixel is 0.2 mm and an arc's ends are pulled onto its own
# circle. The reading it has to catch moves 9.88.
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
    """What one outline came to when it was built."""

    view: str = ""
    entities: int = 0
    geometry: int = 0
    # What `Sketch.solve()` returned. Zero is solved; anything else is not.
    solved: int = 0
    # What the reading leaves undetermined. It is expected to be large: a
    # profile off one view carries no position, and nothing here invents one.
    dof: int = 0
    conflicts: list = field(default_factory=list)
    redundant: list = field(default_factory=list)
    # The sizes written from the sheet's own text rather than from the pixels.
    printed: int = 0
    # How far the furthest entity had to move to satisfy them, in mm.
    moved: float = 0.0
    # Each entity that moved further than the fit can account for, which is the
    # verdict this exists for.
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


def checked(outline, view=""):
    """One traced outline built as a sketch, and the solver's verdict on it."""
    if not available() or not outline.entities:
        return None

    import FreeCAD

    document = FreeCAD.newDocument(A_DOCUMENT)
    try:
        sketch = document.addObject("Sketcher::SketchObject", "Reading")
        for entity in outline.entities:
            sketch.addGeometry(_as_geometry(entity), False)
        _joined(sketch, outline)
        written = _sized(sketch, outline)
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
            moved=max([away for away, _ in shifted] or [0.0]),
            shifted=[trouble for away, trouble in shifted if away > A_SHIFT])
    finally:
        FreeCAD.closeDocument(document.Name)


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
            Sketcher.Constraint(named, at, wide))] = (at, entity, wide)
    return written


def _troubles(indices, written, outline):
    """Each index the solver objected to, back at the entity it was written for."""
    found = []
    for at in indices:
        if at in written:
            index, entity, wide = written[at]
            found.append(Trouble(
                at=at, entity=entity,
                printed=entity.printed,
                said="the %s the sheet prints \u00d8%g against"
                     % (entity.kind, entity.printed) if entity.printed is not None
                     else "the traced %s measured %.2f"
                          % (entity.kind, wide * (2 if entity.kind == "arc" else 1))))
        else:
            found.append(Trouble(
                at=at,
                said="the walk itself: one entity of this outline does not end "
                     "where the next begins"))
    return found
