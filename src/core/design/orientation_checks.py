"""Findings about which way a feature points and how wide it reads -- an axis, a datum's fix, a traced diameter -- against what the drawing's views actually show."""


import re
from core.sheets.sheet_reader import A_DISTANCE
from core.design.core import (
    A_GAP,
    UNDER,
    _A_DIRECTION,
    _IDENTITY,
    _conflict,
    _features,
    _filled,
    _named,
    _under,
    entry_at,
)
from core.design.presence_checks import (
    _GOES_THROUGH,
    _HAS_AN_AXIS,
    _HAS_AN_AXIS_PROSE,
    _LIES_IN_A_PLANE,
    _is_one_of,
)


def direction(model) -> list:
    """
    A feature that has a direction and no field carrying it.

    A hole, bore, slot or pocket carries `axis`; a profile, arc or rounded end
    carries `plane`. Where one is missing this is a gap, settled the same way
    every gap is. What a feature *is* is read off `kind` alone, never off `id`
    or `topology`.
    """
    found = []
    for feature in _features(model):
        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        for closed, prose, field, missing in (
                (_HAS_AN_AXIS, _HAS_AN_AXIS_PROSE, "axis", "which way its axis points"),
                (_LIES_IN_A_PLANE, _LIES_IN_A_PLANE, "plane", "which plane it lies in")):
            if _is_one_of(feature, closed, prose) and not _filled(feature.get(field)):
                found.append(_conflict(
                    _under(where, field),
                    "nothing records %s, so it is built along the base "
                    "profile like everything else" % missing,
                    str(feature.get("kind") or feature.get("topology") or ""),
                    kind=A_GAP))
    return found


_ALONG_ITS_OWN_AXIS = ("depth", "thickness")


def _has_an_axis(feature):
    return _is_one_of(feature, _HAS_AN_AXIS, _HAS_AN_AXIS_PROSE)


def _which_way_each_datum_fixes(model):
    """
    Datum name → the one direction of the part it fixes, where it fixes one.

    A datum that fixes two — an axis runs front to rear and so fixes the length
    and the height both — is left out: a magnitude keyed by it names no single
    direction, and neither `placement` nor `pointing` may attribute one.
    """
    fixed = {}
    for datum in (model or {}).get("datums") or ():
        if not isinstance(datum, dict) or not datum.get("name"):
            continue
        fixes = str(datum.get("fixes") or "").lower()
        named = [way for way in _A_DIRECTION if way in fixes]
        if len(named) == 1:
            fixed[str(datum["name"])] = named[0]
    return fixed


def pointing(model) -> list:
    """
    A feature located along the very direction its own axis points.

    Nothing is ever positioned along its own axis: a through hole's axis is
    placed in the two directions across it. So a feature whose `axis` runs in
    the depth and whose `position` names a datum that fixes the depth has one
    of the two wrong — it is a conflict.

    A blind feature is the exception: a pocket starting at the top face *is*
    placed along its own axis, by the face it is sunk from. "Through" is read
    off `topology` alone, never off `id` or `kind`.
    """
    fixed = _which_way_each_datum_fixes(model)

    found = []
    for feature in _features(model):
        axis = _said_of(feature, "axis")
        if not axis:
            continue
        along = {way for way in _A_DIRECTION if way in axis.lower()}
        along |= {fixed[name] for name in fixed if name in axis}
        if len(along) != 1:
            continue
        way = along.pop()

        placed = [datum for datum in feature.get("position") or ()
                  if fixed.get(str(datum)) == way]
        if not placed:
            continue
        if _GOES_THROUGH in str(feature.get("topology") or "").lower():
            why = ("the feature goes through, so it starts at no face and is placed in "
                   "the %s by nothing — and its axis points along the %s" % (way, way))
        elif len(placed) > 1:
            why = ("the feature is placed in the %s %d times over, and its axis points "
                   "along the %s — it can only start at one face" % (way, len(placed), way))
        else:
            continue

        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        for datum in placed:
            found.append({
                "about": _under(_under(where, "position"), datum),
                "stands": why + ", so either the axis or this position was read off the "
                                "wrong view",
                "was_also_said": "%s: %s" % (_under(where, "axis"), axis),
                "machine_written": True,
            })
    return found


ROUND_IN = "round_in"


_A_DIRECTION_FIELD = ("axis", "plane")


def _said_of(feature, field):
    """
    What one of a feature's fields says, whatever is recorded beside it.

    `_one_value` gives up on a record carrying anything other than a value and
    a flag, which is right for rendering — the rest of it would be dropped —
    and wrong for a check: an `axis` that also says which view it was seen
    round in is still an axis, and a check that cannot read it is a check that
    stops working the moment the reading gets better.
    """
    said = feature.get(field)
    if isinstance(said, dict):
        said = said.get("value")
    return " ".join(str(said or "").split())


_IS_ROUND = ("hole", "bore", "circle", "circular", "round", "cylind",
             "counterbore", "countersink", "thread", "tapped", "boss", "shaft",
             "spotface")


_A_DIAMETER = ("diameter", "outer_diameter", "outside_diameter", "bore_diameter")


_A_RADIUS = ("radius", "outer_radius")


def _what_it_is_called(feature):
    """The two fields that say what a feature is, without what it is next to."""
    return " ".join(str(feature.get(key) or "") for key in ("id", "kind")).lower()


def _is_round(feature):
    """
    Whether this feature is the kind of thing a view can draw round.

    `round_in` is only ever a reading of a circle, and only a round feature has
    one. The recorded run
    (`logs/sessions/daa17fb149474317840ef98439cfef6f.jsonl`, the 2023 pipe
    clamp) put `round_in` on `ring-split` — a saw cut — and on `web-cutout`, an
    open cutout with a tapering profile, because the prompt asks for the field
    and neither of them is round in anything. Two of the four findings that
    stopped that run were `crosswise` judging a split against a bore, which is
    two things that were never comparable.

    So the check reads the field where it means something and ignores it where
    it cannot: a feature with a diameter or a radius, or one whose own words
    say it is round. A split is a split whatever was written beside it. A
    diameter or a radius says roundness outright; the words are for the feature
    that carries neither, and `topology` is left out of them because a feature is
    described there by what it runs into: the split's topology is "continuing
    from the \u00d842 clamp bore", which names a bore that belongs to something else
    and made a saw cut round.
    """
    if any(_filled(feature.get(key)) for key in _A_DIAMETER + _A_RADIUS):
        return True
    return any(word in _what_it_is_called(feature) for word in _IS_ROUND)


def _seen_round_in(feature):
    """
    The view a feature is round in, where the reading says which one.

    `round_in` is the one thing a drawing states about direction outright, and
    the one an axis can be checked against without knowing what any view is
    called.
    """
    if not _is_round(feature):
        return ""
    for field in ("axis", "plane"):
        said = feature.get(field)
        if isinstance(said, dict) and str(said.get(ROUND_IN) or "").strip():
            return " ".join(str(said[ROUND_IN]).split())
    return ""


def crosswise(model) -> list:
    """
    Two features round in different views, built along the same direction.

    A feature is round in the view its axis points at, and nowhere else: that
    is what a projection is. So two features round in *different* views have
    axes that are not parallel — and the check needs no map of which view is
    which.

    The Ø42 bore is round in the elevation. Both M6 holes are round in the
    plan — `traced circle Ø6.10 centred (19.40, 19.27)` — and SECTION A-A shows
    the right-hand one crossing the split it is there to pinch. All three were
    written along the depth, all three were built parallel, and the clamp
    screw ended up running alongside the split instead of through it.
    """
    seen = []
    for feature in _features(model):
        view, axis = _seen_round_in(feature), _said_of(feature, "axis")
        if view and axis:
            seen.append((view, axis,
                         _under("features", _named(feature, _IDENTITY) or "a feature")))

    found = []
    for axis in {said for _, said, _ in seen}:
        along = [(view, where) for view, said, where in seen if said == axis]
        if len({view for view, _ in along}) < 2:
            continue
        for view, where in along:
            found.append(_conflict(
                _under(where, "axis"),
                "this is round in %s, and %s is round in %s — two views, so "
                "the two axes cross, and these were written the same way"
                % ((view,) + next((other.split(UNDER)[-1], at)
                                  for at, other in along if at != view)),
                "%s: %s" % (_under(where, "axis"), axis)))

    for view in {at for at, _, _ in seen}:
        here = [(said, where) for at, said, where in seen if at == view]
        if len({said for said, _ in here}) < 2:
            continue
        for axis, where in here:
            found.append(_conflict(
                _under(where, "axis"),
                "everything round in %s has the one axis, and the features "
                "round in it here were given %d different ones"
                % (view, len({said for said, _ in here})),
                "%s: %s" % (_under(where, "axis"), axis)))
    return found


_A_WIDTH = re.compile(r"^[\u00d8R]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?$")


def _how_wide(feature):
    """
    The diameter this feature was read at, in millimetres, or None.

    A diameter field does not always hold a diameter. The recorded run wrote
    "0.47 of the overall length" in one and an `M6` callout in another, and the
    first number out of either is a millimetre width nobody measured — held
    against the traced arcs within the tracing's own slack, it confirms a
    reading or contradicts one at random. A field that is not a plain
    measurement is not a width, and a width the check does not have is a check
    that says nothing.
    """
    for keys, across in ((_A_DIAMETER, 1.0), (_A_RADIUS, 2.0)):
        for key in keys:
            found = _A_WIDTH.match(_said_of(feature, key))
            if found:
                return float(found.group(1)) * across
    found = re.search(r"\u00d8\s*([0-9]+(?:\.[0-9]+)?)", _what_it_is_called(feature))
    return float(found.group(1)) if found else None


_ONE_WORD_FROM_THE_NEXT = re.compile(r"[^a-z0-9]+")


_SAID_EITHER_WAY = ("the", "on", "of", "sheet")


def _as_a_view(name):
    """
    What is left of a view's name once the difference between two ways of saying
    it is gone.

    The reader names a view after where it sits — "VIEW C-C, on page 2" — and the
    description writes down what it read, "VIEW C-C, page 2", and the two are the
    same view. Compared as they stand they are not, and the check then reads a
    view it was never given as a view with nothing in it.
    """
    return "".join(word for word in _ONE_WORD_FROM_THE_NEXT.split(str(name).lower())
                   if word and word not in _SAID_EITHER_WAY)


def _traced_round_at(round_here, view, wide):
    """
    Whether *view* was traced curved at something the width of *wide*.

    `A_DISTANCE` is how far a traced diameter may sit from the one the
    description read and still be the same circle: the tracing's own slack,
    measured and written down in `sheet_reader`.
    """
    said = _as_a_view(view)
    return any(abs(seen - wide) <= A_DISTANCE
               for at, seen_here in round_here.items() if _as_a_view(at) == said
               for seen in seen_here)


def grounded(model, round_here) -> list:
    """
    A round feature whose circle the tracer found, and found somewhere else.

    The description says which view a feature is drawn round in and that is a
    reading; the tracer says what it saw curved in each view and at what radius,
    and that is a measurement. Where the two disagree the measurement wins, and
    it wins *naming the view the circle is actually in* — which is the whole
    point, because a finding that says only "this is wrong" can be answered
    with another guess and one that says "your Ø42 is in the middle centre
    view, not in VIEW B" can only be answered by looking there.

    Only ever a disagreement, never a silence. The tracer misses circles
    constantly — a raster view hands back the arcs it could follow and drops
    the rest — so a view that shows no evidence of a Ø42 is not a view without
    a Ø42, and a check that read it that way would fire on most of a correct
    reading. Evidence found elsewhere and nowhere here is a different claim: the
    circle exists on the sheet, it is measured, and it is not where the
    description put it.

    A finding is addressed to the field the direction is written in and not to
    the `round_in` beside it: the view is the evidence, the axis is the thing
    that is wrong, and the address is what a correction lands on.
    """
    if not round_here:
        return []

    found = []
    for feature in _features(model):
        view, wide = _seen_round_in(feature), _how_wide(feature)
        if not view or wide is None or _traced_round_at(round_here, view, wide):
            continue
        instead = [at for at in round_here if _traced_round_at(round_here, at, wide)]
        if not instead:
            continue
        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        found.append({
            "about": _under(where, _which_field_is_round(feature)),
            "stands": "nothing \u00d8%g was traced in %s, and something \u00d8%g was "
                      "traced in %s \u2014 so that is the view it is round in, and this "
                      "points at a view its circle is not in"
                      % (wide, view, wide, " and ".join(instead)),
            "was_also_said": "%s: %s" % (_under(where, ROUND_IN), view),
            "instead": instead[0],
            "machine_written": True,
        })
    return found


def _which_field_is_round(feature):
    """Which of `axis` and `plane` is the one carrying `round_in`."""
    for field in _A_DIRECTION_FIELD:
        said = feature.get(field)
        if isinstance(said, dict) and str(said.get(ROUND_IN) or "").strip():
            return field
    return _A_DIRECTION_FIELD[0]


def where_the_direction_is(model, address):
    """
    The address a re-read's answer belongs at, given the finding it answers.

    A check addresses its finding where the wrong reading is, and `pointing`
    cannot: it holds an axis and a position that cannot both be right and no
    way to tell which, so it addresses the position — `features → X → position
    → <datum>`. The re-read is then asked for a direction, "say which way the
    axis points given that view", and a direction written at a position lands
    on a field nobody asked about while the axis it was meant to overrule
    stands. So the answer goes where directions go, on the feature the finding
    named.
    """
    steps = str(address).split(UNDER)
    if steps[-1] in _A_DIRECTION_FIELD or len(steps) < 2 or steps[0] != "features":
        return address
    where = _under(steps[0], steps[1])
    feature = entry_at(model, where)
    return _under(where, _which_field_is_round(feature)) \
        if isinstance(feature, dict) else address


def what_the_trace_says(model, address, round_here):
    """
    The measurement that earns an overrule at *address*, or nothing.

    `corrected` is given a `because`, and what belongs there is the evidence
    off the drawing: the width the feature was read at, and the view the tracer
    found a circle that wide in. A finding's `stands` is the reading it stands
    against — the thing being overruled, which cannot also be the reason for
    overruling it.
    """
    steps = str(address).split(UNDER)
    feature = entry_at(model, _under(steps[0], steps[1])) if len(steps) > 1 else None
    wide = _how_wide(feature) if isinstance(feature, dict) else None
    if wide is None:
        return ""
    seen = [at for at in round_here or () if _traced_round_at(round_here, at, wide)]
    return "something \u00d8%g was traced in %s" % (wide, " and ".join(seen)) if seen else ""


def read_off_the_wrong_view(model, round_here=None) -> list:
    """
    Every finding that says a direction on this part cannot be right.

    Three now, and they are one question asked from three sides: `pointing`
    catches a feature placed along the very direction it points, `crosswise`
    catches two features round in two views pointing the same way, `grounded`
    catches a circle the tracer found in a view other than the one claimed.
    All three mean a view was read for something it does not show.

    The first two read the model against itself and the third reads it against
    the sheet, which is why the third also settles the first two. `crosswise`
    knows that two of these features disagree and cannot know which one is
    wrong; where the drawing confirms one of them — the circle is there, at that
    width, in that view — the wrong one is the other, and holding the confirmed
    one to account is how the run that read the bore correctly came to be
    stopped for it. So a crosswise finding about a feature the tracer confirms
    is dropped, unless the tracer confirms them all, in which case the drawing
    itself is what disagrees and every one of them stands.
    """
    off = grounded(model, round_here)
    crossed = crosswise(model)
    confirmed = _confirmed(model, round_here)
    if confirmed and not all(_about(entry) in confirmed for entry in crossed):
        crossed = [entry for entry in crossed if _about(entry) not in confirmed]
    return pointing(model) + crossed + off


def _about(finding):
    """The feature a finding is about, without the field it ends in."""
    return str(finding.get("about") or "").rsplit(UNDER, 1)[0]


def _confirmed(model, round_here):
    """The features whose claimed view the tracer found their circle in."""
    if not round_here:
        return set()
    return {_under("features", _named(feature, _IDENTITY) or "a feature")
            for feature in _features(model)
            for view, wide in [(_seen_round_in(feature), _how_wide(feature))]
            if view and wide is not None and _traced_round_at(round_here, view, wide)}
