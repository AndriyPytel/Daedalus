"""Findings about which way a feature points and how wide it reads -- an
axis, a datum's fix, a traced diameter -- against what the drawing's
views actually show.

Recorded run (`logs/sessions/daa17fb149474317840ef98439cfef6f.jsonl`,
the 2023 pipe clamp): `round_in` landed on `ring-split` (a saw cut) and
on `web-cutout` (a tapering cutout) because the field exists on both and
neither is round in anything; two of the four findings that run needed
were `crosswise` judging a split against a bore, which were never
comparable. `_is_round` and `crosswise` guard against exactly this.
"""


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
    A feature that has a direction and no field carrying it. A hole, bore,
    slot or pocket carries `axis`; a profile, arc or rounded end carries
    `plane`, and where one is missing this is a gap, settled the same way
    every gap is. What a feature *is* is read off `kind` alone, never off
    `id` or `topology`.
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


def unproven_datum(model) -> list:
    """
    A datum that fixes a direction and carries no `proves` -- an
    independent measured distance to a neighbouring known surface, matched
    against a known dimension. The 2024 Model Mania run named "the
    underside face" for a plate's whole vertical chain and it was the top:
    an envelope came out right either reading, because a shift in the base
    moves the top and the bottom by the same amount, so nothing but a
    proof distinct from the envelope can catch it.
    """
    found = []
    for datum in (model or {}).get("datums") or ():
        if not isinstance(datum, dict) or not datum.get("name"):
            continue
        fixes = str(datum.get("fixes") or "").lower()
        if not any(way in fixes for way in _A_DIRECTION) or _filled(datum.get("proves")):
            continue
        found.append(_conflict(
            _under(_under("datums", str(datum["name"])), "proves"),
            "no `proves` measures this datum against a neighbouring known "
            "surface, so which face it is was guessed rather than checked",
            str(datum.get("fixes") or ""),
            kind=A_GAP))
    return found


def pointing(model) -> list:
    """
    A feature located along the very direction its own axis points.

    Nothing is normally positioned along its own axis, so a feature whose
    `axis` and `position` both fix the depth is a conflict -- except a
    blind feature, which *is* placed along its axis by the face it starts
    from. "Through" is read off `topology` alone, never `id` or `kind`.
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

    `_one_value` drops anything beyond a value and a flag, which suits
    rendering but breaks a check: an axis that also names its view is
    still an axis, so this reads through the extra data instead.
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

    A split is round whatever is written beside it; otherwise only a
    diameter, a radius, or the feature's own words say so -- never
    `topology`, which names what a feature runs into, not its shape.
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

    A feature is round only in the view its axis points at, so two features
    round in *different* views cannot share an axis direction.

    Recorded case: the Ø42 bore round in elevation, both M6 holes round
    in plan ("traced circle Ø6.10 centred (19.40, 19.27)"), and SECTION
    A-A showing one running alongside the split it should pinch, not through it.
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

    A diameter field can hold prose instead of a number -- "0.47 of the
    overall length", an "M6" callout -- so only a plain measurement,
    confirmed against the traced arcs within the tracing's own slack, counts.
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
    What is left of a view's name once the difference between two ways of
    saying it is gone.

    The reader names a view by where it sits ("VIEW C-C, on page 2"); the
    description writes what it read ("VIEW C-C, page 2") -- same view, unequal strings.
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

    The description names a view; the tracer measures what it saw curved,
    and where the two disagree the measurement wins, naming the view the
    circle is actually in so the finding can be checked by looking there.

    Never a silence, only ever a disagreement: the tracer misses circles
    constantly, and the finding addresses `round_in`, not `axis`.
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

    A check addresses its finding where the wrong reading is, but `pointing`
    holds an axis and a position that cannot both be right with no way to
    tell which, so it addresses the position. The re-read then answers with
    a direction, which must land back on the axis it was meant to overrule,
    not on the position where it was asked.
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

    `pointing`, `crosswise`, and `grounded` ask one question three ways: a
    feature placed along its own axis, two features pointing the same way,
    a circle found in the wrong view. The third checks against the sheet
    and so can settle the first two -- confirming one side of a
    `crosswise` disagreement drops the other, unless the tracer confirms
    them all, in which case the drawing itself disagrees.
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
