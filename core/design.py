"""
What the part is: the one description the drawing and the user both write into.

Reading held the sheet as prose, and prose beside prose has nowhere to say
"this line is already filled". One run read the depth front to rear as 100 mm
off SECTION B-B and flagged it `read`; two turns later the Plan said
150 x 50 x 50. The 100 was not overruled, argued with, or flagged — it was gone
from the part. The same run read one bore axis 20 mm from the left end face and
then 25 mm, both `read`, and built 25.

So the part is a structure, and the structure only ever grows. A field already
filled cannot change. A later source may fill an empty field and may add a
conflict; it may never replace a value. Two incompatible dimensions stay
recorded as a conflict and are not reconciled — that is the FCC-59 boundary, and
the cost is accepted deliberately: the first pass becomes authoritative even
where it was wrong, so the disagreement has to reach the user at the Plan Gate
rather than be settled by the machine.

Declarative and unordered throughout — `datums`, `envelope`, `features`,
`conflicts`, `not_defined`, and no build order, no Cut/Join, no sketch planes.
Order is what let FCC-59's failure mode in: an ordered list has somewhere to
write an operation the drawing never showed, and an unordered one does not
(ADR-0019).

The merge is generic over the shape rather than written field by field. The
model comes back from a provider, so a schema hard-coded here would be one more
thing to drift; what is enforced is the rule — filled stays, empty fills,
different becomes a conflict — and identity, which is `id` on a feature and
`name` on a datum.

The same structure is what the run is checked against. `entries` addresses every
value on it by the path it sits under, and that one enumeration is both the
Acceptance the finished part is measured against and the set the loop takes a
Plan's citations away from (ADR-0020).
"""

# The order the Plan Gate reads them in: what was on the sheet, what the part is
# measured from, how big it is, what is on it, and then, apart from the part
# itself, what nobody agrees on and what nobody supplied.
SECTIONS = ("printed_on_the_sheet", "datums", "envelope", "features", "conflicts",
            "not_defined")

# What separates one step of an address from the next. One notation for the
# path a conflict is recorded under and the path an entry is cited by, because
# two would be two things for a reader to learn and one for a Plan to get wrong.
UNDER = " → "

_HEADINGS = {"printed_on_the_sheet": "Printed on the sheet", "datums": "Datums",
             "envelope": "Envelope", "features": "Features",
             "conflicts": "In conflict", "not_defined": "Not defined"}

# What makes two entries of a list the same thing. A feature is its `id`; a
# datum is its name.
_IDENTITY = ("id", "name")

# What a rendered entry is headed by. Wider than identity, because a conflict is
# read by what it is about and a gap by what is missing, and neither is a thing
# the merge has to recognise again.
_HEADED_BY = _IDENTITY + ("about", "what")

# What the part is, as against what is said about the reading of it. A conflict
# holds two values of which neither is settled, and a criterion derived from one
# would assert whichever half the merge happened to keep; a gap is nothing to
# measure; the sweep of the sheet is callouts, not values.
_NOT_THE_PART = ("conflicts", "not_defined", "printed_on_the_sheet")


def merge(held, offered):
    """
    *offered* written into *held*, filling only what is empty.

    The single rule the whole phase rests on, and it is enforced here rather
    than only asked for in the prompt: a value that is already on the model
    stays, whatever a later pass believes it saw. What is empty is filled, what
    is new is added, and what disagrees is appended to `conflicts` for the user
    to settle at the Plan Gate.

    A fragment that is not a model at all leaves the part exactly as it was.
    Design answering with prose is a phase that did not do its job, and losing
    the description over it would be the worse failure — the same call
    `_acceptance_of` makes about an acceptance list nobody wrote.

    What the merge cannot see it is told: `silhouette` reads the grown model for
    an outside described in words and carried by nothing, `direction` for a
    feature that points somewhere and says nowhere, `pointing` for one placed
    along the very direction its axis points, `placement` for one sized in
    a direction it is placed in nowhere, and `twice_over` for a cavity nothing
    places, which is how one cavity comes to be two features. Their findings
    join the same `conflicts` the disagreements go to.
    """
    if not isinstance(offered, dict):
        return _with_the_findings(dict(held or {}))

    conflicts = []
    merged = _written(dict(held or {}), offered, "", conflicts)
    if conflicts:
        merged["conflicts"] = _appended(merged.get("conflicts"), conflicts)
    return _with_the_findings(merged)


def _with_the_findings(model):
    found = (silhouette(model) + direction(model) + pointing(model)
             + crosswise(model) + placement(model) + twice_over(model))
    if found:
        model["conflicts"] = _appended(model.get("conflicts"), found)
    return model


# ── the outside of the part ───────────────────────────────────────────────────

# Words that say the outside of the part is curved rather than boxed. They are
# the words a draughtsman and a description both use for it; the list is short
# on purpose, because a word that also means something inside the part would
# raise this on every model.
_ROUNDED_OUTSIDE = ("capsule", "obround", "racetrack", "oval",
                    "rounded end", "semicircular end", "radiused end",
                    "rounded exterior", "rounded end surfaces")

# What a datum says when it has the same face flat.
_FLAT = ("planar", "flat")

# A datum only contradicts a rounded outside where it is about the ends.
_AN_END = ("end face", "end faces", "end of", "left end", "right end")


# What a conflict is about when the part is described as round on the outside
# and no feature carries that contour. Named here because the loop has to
# recognise this one finding: it is the only one that says the *shape* of the
# part is nobody's, and a run that goes on past it builds a box.
THE_OUTSIDE = "envelope → the shape of the outside"


def nobody_carries_the_outside(model) -> str:
    """
    The standing finding that the outside of the part is nobody's, or "".

    A conflict is settled by the user at the Plan Gate — that is what the
    docstrings here have said since the finding existed, and what the recorded
    run (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`) showed nobody
    doing. It stood in `conflicts`, the Gate showed the Steps, the Plan padded
    the profile it had, and the part came out the wrong shape. So the loop asks
    this before it plans, and this is the question it asks with.
    """
    for finding in silhouette(model):
        if finding["about"] == THE_OUTSIDE:
            return str(finding["was_also_said"])
    return ""


def silhouette(model) -> list:
    """
    The outside of the part described in words and carried by nothing.

    An `envelope` is three distances between named faces, and three distances
    are satisfied exactly as well by a rectangular prism as by the obround plate
    the drawing shows. The recorded run
    (`logs/sessions/57af6c09950047bf9f0582cd398d2a66.jsonl`, mm25-p1-1.pdf) is
    the whole case: the description said "the two closed capsule-shaped exterior
    perimeter edge loops" and "the exterior top, bottom, and rounded end
    surfaces" — but said it inside the *fillet's* edge selection, where it is
    prose about which edges to round and not geometry anybody builds. No feature
    carried the outer contour, so the Plan padded a 150 × 100 rectangle 50 deep
    and every acceptance check passed on a box.

    So where the model calls the outside rounded and no feature owns that
    contour, that is a conflict, in the same list and settled the same way: by
    the user at the Plan Gate, or by Design filling in the feature it left out.
    The words are looked for anywhere in the model and the ownership only in a
    feature's `id`, `kind` and `topology` — the fields that say what a feature
    *is*. A fillet whose edge-selection prose mentions a capsule is the very
    thing this exists to catch, so its prose must not count as carrying one.

    The second finding is the datum that says the same face is flat. "the planar
    end face at the left of the 150 mm overall length" cannot both be true and
    sit beside "rounded end surfaces", and the merge cannot see it because
    neither was ever written under the same address.
    """
    said = _said(model, "")
    rounded = [(path, text) for path, text in said
               if any(word in text.lower() for word in _ROUNDED_OUTSIDE)]
    if not rounded:
        return []

    found = []
    # Quoted from the description's own words where it has any. The sweep of the
    # sheet is in `_said` too, and quoted from there the finding reads as the
    # draughtsman's callout — `R12.5 — plan view (rounded ends)` — rather than
    # as the reading the user is being asked to settle. The sweep is still the
    # fallback: a sheet that prints rounded ends the description never wrote
    # down is the same finding, and it is the evidence there is.
    where, why = next((entry for entry in rounded
                       if not entry[0].startswith("printed_on_the_sheet")), rounded[0])
    if not any(_carries_the_outside(feature) for feature in _features(model)):
        found.append({
            "about": THE_OUTSIDE,
            "stands": "no feature carries the outer contour, so the outside of the part is "
                      "whatever profile the envelope's overall sizes are padded into",
            "was_also_said": "%s: %s" % (where, why),
        })

    for datum in model.get("datums") or ():
        if not isinstance(datum, dict):
            continue
        text = ("%s %s" % (datum.get("name") or "", datum.get("is") or "")).lower()
        if any(word in text for word in _FLAT) and any(word in text for word in _AN_END):
            found.append({
                "about": _under("datums", datum.get("name") or "an end face"),
                "stands": datum.get("is") or datum.get("name"),
                "was_also_said": "%s: %s" % (where, why),
            })
    return found


# ── which way a feature points ────────────────────────────────────────────────

# The kinds of feature that have an axis — the direction the hole is drilled,
# the slot cut, the pocket sunk. Short and literal, because a word that also
# describes a face would raise this on every model.
_HAS_AN_AXIS = ("hole", "bore", "slot", "pocket")

# What a feature says when it has no face it starts from. A blind pocket is
# placed along its own axis by the face it is sunk from and that is the whole
# of its placement there; one that goes through is placed there by nothing.
_GOES_THROUGH = "through"

# The kinds that are a curve rather than a hole. What a radius means depends on
# the plane it is drawn in and on nothing else on the sheet, so these carry a
# plane the way a hole carries an axis. Fillets and chamfers are deliberately
# not here: they follow the edges they are put on, and asking them for a plane
# would raise a finding on every part that has any.
_LIES_IN_A_PLANE = ("profile", "arc", "rounded", "radiused")


def _what_it_is(feature):
    """The fields that say what a feature is, rather than what is said about it."""
    return " ".join(str(feature.get(key) or "")
                    for key in ("id", "kind", "topology")).lower()


def direction(model) -> list:
    """
    A feature that has a direction and no field carrying it.

    The recorded run (`logs/sessions/8d07a842d06b487d8e7729ecba27ce4b.jsonl`,
    the 2023 pipe clamp) built both M6 holes parallel to the ring axis out of
    one sketch in the elevation plane. Both are drawn as circles in the plan, so
    both axes are perpendicular to that ring axis, and SECTION A-A shows the
    right-hand one crossing the split it exists to pinch — the built screw
    missed the split and the part could not clamp.

    Nothing was overruled: `position` is magnitudes keyed by datum and says how
    far, never which way, and direction survived only as prose in `topology`,
    which nothing downstream reads. So the right reading had nowhere to be
    written, the wrong one could not be contradicted, and `entries` could not
    derive a check for it. The same argument that made the outer contour a
    feature rather than three numbers: what is not a field is not built and not
    checked.

    A hole, bore, slot or pocket therefore carries `axis`, and a profile, arc or
    rounded end carries `plane`, both as ordinary values — named by the datums
    they are measured against, so the acceptance measures a direction the same
    way it measures a distance. Where one is missing this is a conflict, in the
    same list `silhouette` writes to and settled the same way: by the user at
    the Plan Gate, or by Design filling in the field it left out.
    """
    found = []
    for feature in _features(model):
        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        text = _what_it_is(feature)
        for words, field, missing in (
                (_HAS_AN_AXIS, "axis", "which way its axis points"),
                (_LIES_IN_A_PLANE, "plane", "which plane it lies in")):
            if any(word in text for word in words) and not _filled(feature.get(field)):
                found.append({
                    "about": _under(where, field),
                    "stands": "nothing records %s, so it is built along the base "
                              "profile like everything else" % missing,
                    "was_also_said": str(feature.get("kind")
                                         or feature.get("topology") or ""),
                })
    return found


# ── a size in a direction, and where it sits in that direction ────────────────

# The directions a part is measured in, in the words the envelope uses for them.
# A feature carrying one of these carries a size along a direction; a diameter
# and a radius are not here, because neither is a size along anything named.
_A_DIRECTION = ("length", "width", "height", "depth", "thickness")

# The two of them a feature spends on itself: a blind pocket's depth is 6 mm
# down its own axis and a boss's thickness is what it stands proud by, neither
# of them 6 mm of the part's depth. Measured on the ordinary part they are what
# every blind hole, pocket and counterbore carries.
_ALONG_ITS_OWN_AXIS = ("depth", "thickness")


def _has_an_axis(feature):
    return any(word in _what_it_is(feature) for word in _HAS_AN_AXIS)


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
    placed in the two directions across it and in neither of them is it the
    axis. So a feature whose `axis` runs in the depth and whose `position`
    names a datum that fixes the depth has one of the two wrong, and which one
    is not something the description can settle — it is a conflict, in the same
    list `silhouette` and `direction` write to.

    A blind feature is the exception that proves it: a pocket 6 mm deep starting
    at the top face *is* placed along its own axis, by the face it is sunk from,
    and asking otherwise would raise a finding on every blind hole, pocket and
    counterbore there is. So the finding is for a feature that goes through —
    which starts at no face at all — and for one placed along its axis twice
    over, since even a blind one starts only once.

    The recorded run (`logs/sessions/141a41df15544013a9740a45138fa7ad.jsonl`,
    the 2023 pipe clamp) is both halves of it. Both M6 holes were written
    `axis: perpendicular to the front face` — along the depth — while carrying
    `position → the longitudinal centre plane: coincident`, and that datum
    fixes the depth. The axis was the wrong one: the holes are drawn as circles
    in the plan and drilled through the height, and built along the depth the
    right-hand screw missed the split it exists to pinch. The same run wrote
    the central cutout `axis: parallel to the depth front to rear` and then
    placed it 6 mm from the front face and 6 mm from the rear face — two
    positions along its own axis, taken off the `(6)` and `(6)` of SECTION A-A,
    which are the depths of the counterbore and the hex recess and belong to
    the mounting hole. Neither could be contradicted: `direction` is satisfied
    by an `axis` that is merely present, and a wrong direction reads exactly
    like a right one.
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
        if _GOES_THROUGH in _what_it_is(feature):
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
            })
    return found


# The field that says which view a feature was seen round in — the one thing a
# drawing states about direction outright, and the one an axis can be checked
# against without knowing what any view is called.
ROUND_IN = "round_in"


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


def _seen_round_in(feature):
    """The view a feature is round in, where the reading says which one."""
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
    axes that are not parallel, whatever either axis was written as — and the
    check needs no map of which view is which, which is the reason it can run
    at all. The reader names views after where they sit on the sheet and the
    description names them after what they show; nothing reconciles the two,
    and nothing has to.

    The recorded run (`logs/sessions/549e6232c9e04b659e5a2d536354bd7a.jsonl`,
    the 2023 pipe clamp) is the whole argument. The Ø42 bore is round in the
    elevation. Both M6 holes are round in the plan — the reader traces them
    there, `traced circle Ø6.10 centred (19.40, 19.27)` — and SECTION A-A shows
    the right-hand one crossing the split it is there to pinch. All three were
    written `axis: perpendicular to the front face, along the depth front to
    rear`, all three were built parallel, and the clamp screw ended up running
    alongside the split instead of through it.

    Its mirror is the same rule read the other way: two features round in one
    view whose axes were written differently cannot both be right either.
    """
    along = {}
    for feature in _features(model):
        view, axis = _seen_round_in(feature), _said_of(feature, "axis")
        if not view or not axis:
            continue
        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        along.setdefault(axis, []).append((view, where))

    found = []
    for axis, seen in along.items():
        views = {view for view, _ in seen}
        if len(views) < 2:
            continue
        for view, where in seen:
            found.append({
                "about": _under(where, "axis"),
                "stands": "this is round in %s, and %s is round in %s — two views, so "
                          "the two axes cross, and these were written the same way"
                          % (view, _elsewhere(seen, view), _other_than(views, view)),
                "was_also_said": "%s: %s" % (_under(where, "axis"), axis),
            })

    for view, seen in _by_view(model).items():
        axes = {axis for axis, _ in seen}
        if len(axes) < 2:
            continue
        for axis, where in seen:
            found.append({
                "about": _under(where, "axis"),
                "stands": "everything round in %s has the one axis, and the features "
                          "round in it here were given %d different ones"
                          % (view, len(axes)),
                "was_also_said": "%s: %s" % (_under(where, "axis"), axis),
            })
    return found


def _by_view(model):
    """Which features are round in each view, with the axis each was given."""
    seen = {}
    for feature in _features(model):
        view, axis = _seen_round_in(feature), _said_of(feature, "axis")
        if view and axis:
            seen.setdefault(view, []).append(
                (axis, _under("features", _named(feature, _IDENTITY) or "a feature")))
    return seen


def _elsewhere(seen, view):
    """The first entry of *seen* that is not in *view*, named."""
    return next((where.split(UNDER)[-1] for at, where in seen if at != view), "another")


def _other_than(views, view):
    return next((at for at in sorted(views) if at != view), "another view")


def read_off_the_wrong_view(model) -> list:
    """
    Every finding that says a direction on this part cannot be right.

    The two of them are one question asked from either side: `pointing` catches
    a feature placed along the very direction it points, `crosswise` catches two
    features round in two views pointing the same way. Both mean the same thing
    — a view was read for something it does not show — and neither can be
    settled by anything downstream, which is why the loop stops on them (FCC-110).
    """
    return pointing(model) + crosswise(model)


def placement(model) -> list:
    """
    A feature with a size in a direction and nothing placing it in that one.

    The recorded run (`logs/sessions/8d07a842d06b487d8e7729ecba27ce4b.jsonl`,
    the 2023 pipe clamp) built the left mounting foot symmetric about the ring
    axis, `arm_top = 32.5` and `arm_bottom = 17.5`, where the drawing has it
    flush with the ring's lowest tangent. Nothing was overruled: the description
    named "the lower tangent plane of the ring" a datum and then never wrote a
    value against it. The arm carried `height: 15 mm`, a length from the
    leftmost tangent plane and a length to the cutout's closed end — a size in
    the height and no position in the height at all. The prompt has said for as
    long as it has existed that a position nobody named degrades into "somewhere
    sensible" and that "sensible" defaults to centred, and centred is what came
    out.

    Silence had to stop being an option, because the Acceptance is derived from
    the entries the description holds: a placement nobody recorded owns no
    check, so the run measured 108, 50, 15 and the fillets and went green on a
    part wrong in the way the drawing is loudest about.

    A direction is attributed through the datum a position is keyed by, which is
    why a datum says which direction it fixes. A datum that fixes two of them
    places nothing: the ring axis runs front to rear, so 30 mm from it is 30 mm
    along the length or 30 mm up the height and the magnitude does not say
    which — which is exactly how the arm came to look placed and was not.

    A depth or a thickness on a feature that has an axis is not a size in one of
    the part's directions at all: it runs along the feature's own axis, and
    whatever placed that axis has already placed it. A blind pocket 6 mm deep,
    20 mm from the left end face and starting at the top face, is described
    whole — asked for a position in the depth as well it would raise a finding
    on every blind hole, pocket and counterbore there is, and a check that fires
    on work that is right is a check nobody keeps. That turns on the *kind* of
    feature and not on the field being filled in: a pocket's depth runs along
    its own axis whether or not anybody wrote the axis down, so the pocket that
    never said which way is short of its axis and of nothing else, and
    `direction` is where that is said.
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
            found.append({
                "about": _under(_under(where, "position"), "the " + way),
                "stands": "the feature has a size in the %s and no position in the %s "
                          "against a datum that fixes it, and a position nobody named "
                          "defaults to centred" % (way, way),
                "was_also_said": "%s: %s" % (_under(where, way),
                                             _one_value(feature[way]) or feature[way]),
            })
    return found


# ── one cavity, one feature ───────────────────────────────────────────────────

# The words a description uses for a hole in the material that is not a drilled
# one. A cavity is what a detail view is usually drawn for, and a detail view is
# the second picture a cavity gets described from.
_A_CAVITY = ("cutout", "cut-out", "cut out", "slot", "pocket", "recess",
             "relief", "notch", "opening", "scallop")


def twice_over(model) -> list:
    """
    A cavity nothing places, which is how one cavity becomes two features.

    The recorded run (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`,
    the 2023 pipe clamp) described the one cavity in the web twice: `slot-arm`,
    30 mm long, off the plan, and `cutout-web`, "profile as drawn in VIEW B",
    off the detail the sheet points at with `SEE VIEW B FOR CLARITY OF CUTOUT`.
    One physical cavity, two features, and the second carried `position: {}` —
    nothing at all. Plan then built both: a slot 8 mm wide it invented, and an
    R10 circle it invented, and the circle cut the Body into two solids and
    ended the run.

    The finding is the empty `position`, because that is what is checkable and
    what is wrong on its own: a cavity nobody placed is built wherever the
    builder likes. `placement` cannot reach it — that one asks whether a size in
    a direction has a position in that direction, and this feature had no sizes
    either. Where another cavity is drawn in a view this one is also drawn in,
    the finding says so as well: two cavities sharing a view, one of them
    placed by nothing, is the shape this defect arrives in.
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
        found.append({
            "about": _under(where, "position"),
            "stands": stands,
            "was_also_said": str(feature.get("kind") or feature.get("topology") or ""),
        })
    return found


def _drawn_in_common(one, other):
    """Whether two features name a view between them that both were read in."""
    return bool({str(view) for view in one.get("seen_in") or ()}
                & {str(view) for view in other.get("seen_in") or ()})


def _features(model):
    return [feature for feature in (model or {}).get("features") or ()
            if isinstance(feature, dict)]


def _carries_the_outside(feature):
    """Whether this feature *is* the rounded outside, rather than talking about it."""
    text = " ".join(str(feature.get(key) or "") for key in ("id", "kind", "topology")).lower()
    return any(word in text for word in _ROUNDED_OUTSIDE)


def _said(node, path):
    """Every string on the model, with the address it sits under."""
    if isinstance(node, str):
        return [(path, node)]
    if isinstance(node, dict):
        return [entry for key, value in node.items() if key != "conflicts"
                for entry in _said(value, _under(path, key))]
    if isinstance(node, list):
        return [entry for at, value in enumerate(node, start=1)
                for entry in _said(value, _under(path, _named(value, _IDENTITY) or at))]
    return []


def render(model) -> str:
    """
    The part as something a person reads at a glance.

    The Plan Gate is where a misreading is cheap to correct, and a JSON dump is
    not read — so the sections are named in the order they are read in and the
    values fall out as an indented outline. Generic over the shape, so a model
    carrying a key nobody anticipated shows it rather than swallowing it.
    """
    if not model:
        return ""

    written = []
    for section in SECTIONS + tuple(key for key in model if key not in SECTIONS):
        if not _filled(model.get(section)):
            continue
        written.append(_HEADINGS.get(section, str(section).replace("_", " ").capitalize()))
        written.extend(_lines(model[section], "  "))
    return "\n".join(written)


def entries(model) -> list:
    """
    Every value the part carries, addressed by where it sits and said in words.

    One enumeration doing two jobs, and deliberately not two functions. The
    Acceptance the finished part is checked against *is* this list — the
    description's own entries, with their datums and their flags, instead of
    Plan's paraphrase of them — and the coverage the loop checks over a Plan is
    this same list with the Plan's citations taken away. Two enumerations would
    be two things to drift apart, and the drift would be silent: a Plan could
    cover every address of one and leave an entry of the other unmeasured
    (ADR-0020).

    The address is the merge's own path, so an entry is cited by exactly the
    words it is recorded under, and an envelope entry is as citable as a
    feature's. That is not a detail: the value the recorded run lost between the
    description and the Plan was the 100 mm front to rear, which is an envelope
    entry and not a feature.

    Each entry is `{"about": …, "flag": …, "says": …}` — where it sits, whether
    it was read or assumed, and the whole of it on one line for a person and for
    the assert message that has to name it.
    """
    return _addressed(model, "")


# ── the entries ───────────────────────────────────────────────────────────────


def _addressed(node, path):
    if _a_value(node):
        return [{"about": path, "flag": str(node.get("flag") or ""),
                 "says": "%s: %s" % (path, _one_value(node) or node.get("value"))}]

    if isinstance(node, dict):
        return [entry for key, value in node.items() if key not in _NOT_THE_PART
                for entry in _addressed(value, _under(path, key))]

    if isinstance(node, list):
        # By identity where an entry has one, so an address survives a feature
        # being added ahead of another — the same rule the merge matches by.
        return [entry for at, value in enumerate(node, start=1)
                for entry in _addressed(value, _under(path, _named(value, _IDENTITY) or at))]

    return []


# ── the merge ─────────────────────────────────────────────────────────────────


def _written(held, offered, path, conflicts):
    # A value and its flag are one thing: there is no half-filling a value, and
    # 20 mm read disagreeing with 20 mm assumed is a disagreement worth showing.
    if isinstance(held, dict) and isinstance(offered, dict) \
            and not (_a_value(held) or _a_value(offered)):
        grown = dict(held)
        for key, value in offered.items():
            grown[key] = (_written(grown[key], value, _under(path, key), conflicts)
                          if _filled(grown.get(key)) else value)
        return grown

    if isinstance(held, list) and isinstance(offered, list):
        return _alongside(held, offered, path, conflicts)

    if held == offered:
        return held

    conflicts.append({"about": path, "stands": held, "was_also_said": offered})
    return held


def _alongside(held, offered, path, conflicts):
    """
    Two lists of the same things, matched by what makes an entry that thing.

    By `id`, never by position: a fragment that names one feature must not land
    on whichever one happens to be first. An entry with no identity at all — a
    view a feature was seen in, a gap nobody has closed — is appended if it is
    not already there.
    """
    grown = list(held)
    for entry in offered:
        this = _named(entry, _IDENTITY)
        at = next((index for index, other in enumerate(grown)
                   if this and _named(other, _IDENTITY) == this), None)
        if at is None:
            if entry not in grown:
                grown.append(entry)
        else:
            grown[at] = _written(grown[at], entry, _under(path, this), conflicts)
    return grown


def _a_value(entry):
    return isinstance(entry, dict) and "value" in entry


def _named(entry, keys):
    if not isinstance(entry, dict):
        return None
    for key in keys:
        if entry.get(key):
            return str(entry[key])
    return None


def _under(path, key):
    return path + UNDER + str(key) if path else str(key)


def _filled(value):
    """Nought and False are values somebody supplied; empty is nobody supplying one."""
    return value is not None and value != "" and value != [] and value != {}


def _appended(held, more):
    grown = list(held or ())
    for entry in more:
        if entry not in grown:
            grown.append(entry)
    return grown


# ── the rendering ─────────────────────────────────────────────────────────────


def _lines(value, indent):
    if isinstance(value, dict):
        said = _one_value(value)
        if said:
            return [indent + said]
        written = []
        for key, entry in value.items():
            if _filled(entry):
                written.extend(_labelled(str(key).replace("_", " "), entry, indent))
        return written

    if isinstance(value, list):
        # A handful of plain entries — the views a feature was seen in — read
        # better on one line than as a column of bullets. A sweep of the whole
        # sheet does not, so the line has a width.
        said = ", ".join(str(entry) for entry in value)
        if value and len(said) <= 72 \
                and all(not isinstance(entry, (dict, list)) for entry in value):
            return [indent + said]
        return [line for entry in value for line in _bulleted(entry, indent)]

    return [indent + str(value)]


def _labelled(label, entry, indent):
    written = _lines(entry, indent + "  ")
    if len(written) == 1:
        return ["%s%s: %s" % (indent, label, written[0].strip())]
    return ["%s%s:" % (indent, label)] + written


def _bulleted(entry, indent):
    head = _named(entry, _HEADED_BY)
    rest = ({key: field for key, field in entry.items() if key not in _HEADED_BY}
            if head else entry)
    written = _lines(rest, indent + "  ") if _filled(rest) else []
    if not head and len(written) == 1:
        head, written = written[0].strip(), []
    return [("%s- %s" % (indent, head or "")).rstrip()] + written


def _one_value(value):
    """
    A value and its flag on one line — the shape that repeats everywhere.

    Only where that is the whole of it: a record carrying anything else falls
    through to the outline rather than having the rest of it dropped.
    """
    if "value" not in value or set(value) - {"value", "flag"}:
        return ""
    return "%s (%s)" % (value["value"], value["flag"]) if value.get("flag") \
        else str(value["value"])
