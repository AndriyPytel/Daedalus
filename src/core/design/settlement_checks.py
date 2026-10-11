"""
Findings about whether a placement or a conflict is settled -- by a
feature, by nothing, or by the tracing that ran over it. `twice_over`
closes a gap the reconciliation prompt already asks the model for but
`kind` being prose left unchecked: four such pairs stood in 47 recorded
models, past the old proxy (`placed_by_nothing`), because both features in
a pair can carry a filled `position` framed off two different datums
pinning the same point.
"""


import re
from core.design.core import (
    A_GAP,
    _A_DIRECTION,
    _IDENTITY,
    _a_value,
    _conflict,
    _features,
    _filled,
    _named,
    _one_value,
    _under,
)
from core.design.presence_checks import _what_it_is, placements_twice
from core.design.orientation_checks import (
    _ALONG_ITS_OWN_AXIS,
    _has_an_axis,
    _which_way_each_datum_fixes,
)
from core.design.gaps import _each_value


def placement(model) -> list:
    """
    A feature with a size in a direction and nothing placing it in that
    one. Silence had to stop being an option: an unrecorded placement owns
    no check, so a run can go green on a part wrong in the way the drawing
    is loudest about; direction is attributed through the datum a position
    is keyed by. A depth/thickness on a feature with an axis runs along the
    feature's own axis, not the part's -- `direction`'s concern, not this
    one's.
    """
    fixed = _which_way_each_datum_fixes(model)

    found = []
    for feature in _features(model):
        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        placed = {fixed.get(str(datum)) for datum in (feature.get("position") or ())}
        along_its_own = _ALONG_ITS_OWN_AXIS if _has_an_axis(feature) else ()
        for way in _A_DIRECTION:
            if not _filled(feature.get(way)) or way in placed or way in along_its_own:
                continue
            found.append(_conflict(
                _under(_under(where, "position"), "the " + way),
                "the feature has a size in the %s and no position in the %s "
                "against a datum that fixes it, and a position nobody named "
                "defaults to centred" % (way, way),
                "%s: %s" % (_under(where, way),
                           _one_value(feature[way]) or feature[way]),
                kind=A_GAP))
    return found


_A_CAVITY = ("cutout", "cut-out", "cut out", "slot", "pocket", "recess",
             "relief", "notch", "opening", "scallop")


def placed_by_nothing(model) -> list:
    """
    A cavity nothing places, built wherever the profile happens to allow.
    The one cavity in the web was described twice: `slot-arm`, 30 mm long,
    and `cutout-web` with `position: {}`, nothing at all. Plan built a slot
    8 mm wide it invented, and an R10 circle it invented, and the circle
    cut the Body into two solids and ended the run.
    """
    cavities = [feature for feature in _features(model)
                if any(word in _what_it_is(feature) for word in _A_CAVITY)]

    found = []
    for feature in cavities:
        if _filled(feature.get("position")):
            continue
        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        stands = ("nothing places this cavity — it has no position against any datum, so "
                  "it is built wherever the profile happens to allow")
        alongside = [_named(other, _IDENTITY) for other in cavities
                     if other is not feature and _drawn_in_common(feature, other)]
        if alongside:
            stands += (", and %s is a cavity drawn in a view this one is drawn in — one "
                       "cavity described twice is two cavities built"
                       % ", ".join(name for name in alongside if name))
        found.append(_conflict(
            _under(where, "position"),
            stands,
            str(feature.get("kind") or feature.get("topology") or ""),
            kind=A_GAP))
    return found


def twice_over(model) -> list:
    """
    Two features of the same `kind`, placed against a shared datum, and not
    already reconciled by `superseded_by` -- one physical feature read
    twice. `kind` equality is exact, never a substring search: the enum
    this closes it into is what the rest of this module's prose matching
    cannot rely on here. Shared-datum values need not agree -- agreement
    is a duplicate, disagreement is `unsafe_supersession`'s territory once
    `superseded_by` names the pair.
    """
    found = []
    for feature_a, feature_b, shared in _placed_against_one_datum(model):
        kind = feature_a.get("kind")
        pair = sorted([feature_a.get("id") or "unknown", feature_b.get("id") or "unknown"])
        found.append(_conflict(
            "features → %s and %s (same kind, placed against %s)"
            % (pair[0], pair[1], ", ".join(shared)),
            "both are a %s, placed against %s — one physical feature described twice"
            % (kind, ", ".join(shared)),
            kind,
            kind=A_GAP))
    return found


def _placed_against_one_datum(model):
    features = [feature for feature in _features(model) if not feature.get("superseded_by")]
    for index, feature_a in enumerate(features):
        for feature_b in features[index + 1:]:
            kind_a, kind_b = feature_a.get("kind"), feature_b.get("kind")
            if not kind_a or kind_a != kind_b:
                continue
            position_a = feature_a.get("position") or {}
            position_b = feature_b.get("position") or {}
            if not isinstance(position_a, dict) or not isinstance(position_b, dict):
                continue
            shared = sorted(set(position_a) & set(position_b))
            if shared:
                yield feature_a, feature_b, shared


def described_twice(model) -> list:
    """
    Each pair of features `twice_over` or `twice_placed` finds to be one
    physical feature described twice, whose values differ at an address both
    carry: `(feature_a, feature_b, [(address, value_a, value_b), ...])`.
    """
    pairs = [(a, b) for a, b, _ in _placed_against_one_datum(model)]
    pairs += [(a, b) for a, b, _, differing, opposing in placements_twice(model)
              if differing or (not opposing and a.get("kind") and a.get("kind") == b.get("kind"))]
    found = []
    for feature_a, feature_b in pairs:
        differing = _values_that_differ(feature_a, feature_b)
        if differing and not any(feature_a is x and feature_b is y for x, y, _ in found):
            found.append((feature_a, feature_b, differing))
    return found


def _values_that_differ(feature_a, feature_b):
    said_a = {at: _said_value(node) for at, node in _each_value(feature_a, "")}
    said_b = {at: _said_value(node) for at, node in _each_value(feature_b, "")}
    return [(at, said_a[at], said_b[at]) for at in said_a
            if at in said_b and said_a[at] != said_b[at]]


def _said_value(node):
    return str(node.get("value")).strip()


_SAYS_WHAT_SETTLED_IT = {"read": "from", "assumed": "because", "derived": "from"}


def settled_by_nothing(model) -> list:
    """
    An entry that does not say what settled its value: `assumed` carries
    `because`, `read` and `derived` carry `from`, one line each saying who
    settled the value and on what. A `read` is asked the same question the
    other two are only where the part was read off a sheet, since the view
    and the callout a citation names are what a sheet has and a part
    described in words has not; two recorded runs over mm2023-phase1 flagged
    `read` a left end radius carried over from the right end's printed R12.5
    and a ring depth front to rear taken off a lug's height in another
    direction. One rule, not three: a gap, not a refusal, the run carries on.
    """
    off_a_sheet = bool(model.get("printed_on_the_sheet"))
    found = []
    for path, node in _each_value(model, ""):
        flag = str(node.get("flag") or "")
        if flag == "read" and not off_a_sheet:
            continue
        says = _SAYS_WHAT_SETTLED_IT.get(flag)
        if says and not _filled(node.get(says)):
            found.append(_conflict(
                path,
                "`%s` with no `%s`: nothing else on the model can say "
                "afterwards where the value came from"
                % (node.get("flag"), says),
                str(node.get("value") or ""),
                kind=A_GAP))
    return found


_CAME_OFF_THE_PICTURE = ("traced", "traces", "tracing")


_A_MEASUREMENT = re.compile(r"\d+(?:\.\d+)?\s*mm")


def settled_by_the_tracing(model) -> list:
    """
    Every conflict of whose two readings one was measured off the view.

    Recorded case: a pocket stated 40 mm wide by 30 mm high against a
    profile traced at 40 by 25; the Plan took the stated height, kept the
    R30 centres on the end-bore axes, and built 48.038476 mm wide.

    Where one reading came off the picture, that one governs; both or
    neither is a conflict nobody measured, and it rides to the Gate.
    """
    found = []
    for conflict in model.get("conflicts") or []:
        traced = _the_traced_reading(conflict)
        if traced:
            found.extend({"about": at, "instead": traced,
                          "stands": "the conflict about %s: this reading was "
                                    "traced off the view and the one it displaces "
                                    "was not" % conflict.get("about")}
                         for at in _where_the_conflict_points(model, conflict))
    return found


def _the_traced_reading(conflict):
    """The one side of *conflict* that was measured off the view, or ""."""
    if not isinstance(conflict, dict):
        return ""
    traced = [said for said in (str(conflict.get("stands") or ""),
                                str(conflict.get("was_also_said") or ""))
              if any(word in said.lower() for word in _CAME_OFF_THE_PICTURE)
              and _A_MEASUREMENT.search(said)]
    return traced[0] if len(traced) == 1 else ""


def _where_the_conflict_points(model, conflict):
    """The addresses a conflict in prose names: a feature it says, a field it is about."""
    said = " ".join(str(conflict.get(key) or "")
                    for key in ("about", "stands", "was_also_said")).lower()
    about = str(conflict.get("about") or "").lower()
    return [_under(_under("features", this), field)
            for feature in _features(model)
            for this in [_named(feature, _IDENTITY)]
            if this and this.lower() in said
            for field, entry in feature.items()
            if _a_value(entry) and not _filled(entry.get("was"))
            and re.search(r"\b%s\b" % re.escape(field.replace("_", " ")), about)]


def _drawn_in_common(one, other):
    """Whether two features name a view between them that both were read in."""
    return bool({str(view) for view in one.get("seen_in") or ()}
                & {str(view) for view in other.get("seen_in") or ()})
