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

`SECTIONS` is the order the Plan Gate reads them in: what was on the sheet, what
the part is measured from, how big it is, what is on it, and then, apart from the
part itself, what nobody agrees on and what nobody supplied. Of those last three,
none is the part: a conflict holds two values of which neither is settled, and a
criterion derived from one would assert whichever half the merge happened to
keep; a gap is nothing to measure; the sweep of the sheet is callouts, not
values.

One notation, `UNDER`, separates the steps of an address, for the path a conflict
is recorded under and the path an entry is cited by alike — two would be two
things for a reader to learn and one for a Plan to get wrong. Identity, which is
what makes two entries of a list the same thing, is `id` on a feature and `name`
on a datum. What a rendered entry is headed by is wider than that, because a
conflict is read by what it is about and a gap by what is missing, and neither is
a thing the merge has to recognise again.

An entry of `conflicts` is one of `KINDS` and says which: two readings that
cannot both be true, a value nothing on the part supplies, or a check that
could not be run. A user settles the three differently — choose, supply, read
the sheet again — and a Gate handed one flat column cannot tell them apart, nor
tell a sheet that agrees with itself from a sheet nobody could check. The kind
is a field, written by whoever found the thing, because what a finding is is
known where it is made and is a guess anywhere else. `A_CONFLICT` is what an
entry that does not say reads as, since that is what the section is for and
what `config/prompts/design.md` asks the model for.

Its address is `about`, and one address carries one entry wherever the list is
read: the log beside the document, and the reading the Gate is shown. The merge
grows the list, so the same disagreement written twice in two words is two
entries in it and a pass that spells a view differently is a third; which of
them stands is decided by address and never by the prose — the same choice
`_unconsumed` makes for the coverage check, and for the same reason.
"""
import re

from core.sheet_reader import A_DISTANCE, numbers_in

SECTIONS = ("printed_on_the_sheet", "datums", "envelope", "features", "conflicts",
            "not_defined")

UNDER = " → "

_HEADINGS = {"printed_on_the_sheet": "Printed on the sheet", "datums": "Datums",
             "envelope": "Envelope", "features": "Features",
             "conflicts": "In conflict", "not_defined": "Not defined"}

_IDENTITY = ("id", "name")

_HEADED_BY = _IDENTITY + ("about", "what")

_NOT_THE_PART = ("conflicts", "not_defined", "printed_on_the_sheet")

A_CONFLICT = "conflict"

A_GAP = "gap"

A_CHECK_HAZARD = "check hazard"

KINDS = (A_CONFLICT, A_GAP, A_CHECK_HAZARD)

HEADED = {A_CONFLICT: "Two readings that cannot both be true",
          A_GAP: "A value nothing on the part supplies",
          A_CHECK_HAZARD: "A check that could not be run"}

FEATURE_KIND = ("hole", "slot", "pocket", "cutout", "pad", "fillet", "chamfer",
                "boss", "rib", "thread", "counterbore", "countersink", "groove",
                "notch", "tab", "lug", "ear", "flange", "step", "shoulder",
                "shell", "draft")

CLOSED_KIND_VOCABULARY = "closed"

_BANNED_KIND_SYNONYM = {"bore": "hole", "round": "fillet", "rounded": "fillet",
                         "rounds": "fillet", "opening": "cutout", "window": "cutout",
                         "split": "slot", "slit": "slot", "saw cut": "slot",
                         "keyway": "slot", "recess": "counterbore",
                         "recesses": "counterbore"}

_REGION_WORD = ("arm", "ring", "web", "band", "collar", "profile", "plate",
                "body", "outline", "contour")


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
    a direction it is placed in nowhere, `placed_by_nothing` for a cavity
    nothing places, `twice_over` for two features of the same kind placed
    against a shared datum, which is how one physical feature comes to be
    two, and `settled_by_nothing` for a value whose flag promises a reason it
    does not carry. Their findings join the same `conflicts` the
    disagreements go to.
    """
    if not isinstance(offered, dict):
        return _with_the_findings(dict(held or {}))

    conflicts = list(_refused_kinds(offered)) + list(_refused_conflicts(offered))
    if conflicts:
        offered = _without_refused_kinds(offered)
        offered = _without_refused_conflicts(offered)
    merged = _written(dict(held or {}), offered, "", conflicts)
    if conflicts:
        merged["conflicts"] = _appended(merged.get("conflicts"), conflicts)
    return _with_the_findings(merged)


def _refused_kinds(offered):
    """
    Features in *offered* naming a `kind` off `FEATURE_KIND`, checked only
    when *offered* itself carries `"kind_vocabulary": "closed"` — the marker
    `config/prompts/design.md` asks a Design turn to send on every reply once
    it is written under ADR-0026's closed kind list.

    A recorded model with no marker took the prose grammar this ticket
    replaces, and is not checked here at all: enforcing the closed list
    against every recorded reading is the alternative ADR-0026 rejects by
    name — "a closed list added to the prompts and nothing else", already
    measured at 14.9% compliance — so the gate is the marker the model
    itself sends, not a guess made from the shape of what it sent.

    The reason named is actionable rather than a bare refusal, so a re-ask
    costs one turn and not a loop that spends the revision budget: a value
    one of FCC-231's banned prose synonyms maps onto (`round` onto `fillet`,
    `recess` onto `counterbore`) names that kind, a region word (`arm`,
    `ring`, `web`, ...) names `pad` and the new `region` field it belongs in
    instead, and anything else names only the count of the closed list.
    """
    if not isinstance(offered, dict) or offered.get("kind_vocabulary") != CLOSED_KIND_VOCABULARY:
        return []
    found = []
    for feature in offered.get("features") or []:
        if not isinstance(feature, dict):
            continue
        kind = feature.get("kind")
        if not kind or kind in FEATURE_KIND:
            continue
        where = _under(_under("features", feature.get("id") or "a feature"), "kind")
        found.append({
            "about": where,
            "stands": _refusal_reason(kind),
            "was_also_said": "refused — the field is left as not yet filled",
            "kind": A_CONFLICT,
            "machine_written": True,
        })
    return found


def _refusal_reason(kind):
    lowered = str(kind).strip().lower()
    mapped = _BANNED_KIND_SYNONYM.get(lowered)
    if mapped:
        return "%r is not a kind; the kind is %r" % (kind, mapped)
    if lowered in _REGION_WORD:
        return "%r is not a kind; the kind is %r, and %r is the region" % (kind, "pad", kind)
    return "%r is not one of the closed kind vocabulary's %d values" % (kind, len(FEATURE_KIND))


def _without_refused_kinds(offered):
    features = []
    for feature in offered.get("features") or []:
        if isinstance(feature, dict) and feature.get("kind") \
                and feature["kind"] not in FEATURE_KIND:
            feature = {key: value for key, value in feature.items() if key != "kind"}
        features.append(feature)
    return dict(offered, features=features)


CONFLICT_ENTRY_FIELDS = ("about", "stands", "was_also_said")


def _refused_conflicts(offered):
    """
    Entries of *offered*'s own `conflicts` missing one of `CONFLICT_ENTRY_FIELDS`,
    checked only under the same `kind_vocabulary: closed` marker `_refused_kinds`
    gates on.

    A conflict's shape has nothing to do with naming a `kind`, so this reuses,
    rather than adds to, the one marker that already exists. Read here not as
    "kinds only" but as "this reply follows the declared artefact contract" —
    the whole of ADR-0026's closed vocabulary, kind and conflict shape alike.
    That reading is chosen for a measured reason: markers are sent about a
    third of the time in production (Design, session
    `afa0142baef847b0a9cd9139390af993`: 3 of 9 replies carried
    `kind_vocabulary` at all). A second, precise marker for conflict shape
    would be forgotten independently of the first and enforced a third of a
    third of the time at best; one marker meaning "the current contract" is
    worth more than several that are each mostly absent. A reply that never
    sent `kind_vocabulary` never promised this shape either, so the fixtures
    recorded before FCC-237 keep passing untouched.

    FCC-237: a Design reply invented four different conflict shapes across one
    session, 44 of 46 entries carrying no `what` at all — entries in that many
    shapes cannot be rendered, compared, or told apart by the Plan Gate. The
    entry is refused whole rather than patched, and travels on in
    `was_also_said` so nothing it said is lost — only its shape was wrong.
    """
    if not isinstance(offered, dict) or offered.get("kind_vocabulary") != CLOSED_KIND_VOCABULARY:
        return []
    found = []
    for index, entry in enumerate(offered.get("conflicts") or [], start=1):
        if not isinstance(entry, dict):
            continue
        missing = [field for field in CONFLICT_ENTRY_FIELDS if field not in entry]
        if not missing:
            continue
        found.append({
            "about": entry.get("about") or _under("conflicts", "entry %d" % index),
            "stands": "refused — a conflict entry carries %s, missing %s"
                      % (", ".join(CONFLICT_ENTRY_FIELDS), ", ".join(missing)),
            "was_also_said": entry,
            "kind": A_CONFLICT,
            "machine_written": True,
        })
    return found


def _without_refused_conflicts(offered):
    kept = [entry for entry in offered.get("conflicts") or []
            if not (isinstance(entry, dict)
                    and any(field not in entry for field in CONFLICT_ENTRY_FIELDS))]
    return dict(offered, conflicts=kept)


def corrected(model, address, value, because):
    """
    The one write that overrules what is already on the model, and its receipt.

    Everything above this line rests on the merge only ever filling what is
    empty, and that rule is right: it is what keeps a second opinion from
    quietly replacing a reading, and it is the whole of FCC-59. But it is also
    why a check that finds something wrong has exactly two moves — say nothing,
    or stop the run. Nothing can be *repaired*, because repair is a write over
    something written. The recorded run
    (`logs/sessions/daa17fb149474317840ef98439cfef6f.jsonl`) stopped before the
    Plan Gate for four findings, of which four were wrong, and the loop had no
    third move to make.

    So there is one, and it is narrow on purpose. A correction lands at one
    address, which a check has to have named; it carries *because*, which is
    the evidence off the drawing that earns the overrule; and it leaves the
    value it displaced behind it under `was`, in the reading, which is a file.
    A value is therefore never lost — it is superseded, in writing, by
    something the sheet was measured for. What may not do this is a phase
    changing its mind: prose is not evidence, and a caller with nothing
    measured to cite has nothing to pass here.
    """
    if not address or not _filled(value):
        return model

    steps = str(address).split(UNDER)
    node = model
    for step in steps[:-1]:
        node = _the_entry(node, step)
        if node is None:
            return model

    last = steps[-1]
    if not isinstance(node, dict) or not _filled(node.get(last)):
        return model

    held = node[last]
    was = held.get("value") if isinstance(held, dict) and _a_value(held) else held
    node[last] = (dict(value, was=was, because=because) if isinstance(value, dict)
                  else {"value": value, "was": was, "because": because})
    return model


def entry_at(model, address):
    """The record sitting at *address* on this model, whole, or None."""
    node = model
    for step in str(address).split(UNDER):
        node = _the_entry(node, step)
        if node is None:
            return None
    return node


def said_at(model, address):
    """What sits at *address* on this model, as a value, or None."""
    node = entry_at(model, address)
    return node.get("value") if isinstance(node, dict) and _a_value(node) else node


def _the_entry(node, step):
    """One step of an address, through a mapping or a list of identified things."""
    if isinstance(node, dict):
        return node.get(step)
    if isinstance(node, list):
        return next((entry for entry in node if _named(entry, _IDENTITY) == step), None)
    return None


def _with_the_findings(model):
    found = (silhouette(model) + direction(model) + pointing(model)
             + crosswise(model) + placement(model) + placed_by_nothing(model)
             + twice_over(model) + settled_by_nothing(model)
             + unsafe_supersession(model) + twice_placed(model))
    if found:
        model["conflicts"] = _appended(model.get("conflicts"), found)
    return model


def unsafe_supersession(model) -> list:
    """
    Features marked superseded_by that carry values differing from the canonical.

    When reconciliation marks a feature superseded_by another, _features()
    filters it out and its values are lost. If those values differ from the
    canonical feature's values, that is a silent reading loss — worse than
    the loud conflict it should be. This check catches such cases and raises
    them as conflicts so the user settles them at the Plan Gate.

    Only features are covered. Envelope entries and datums are not filtered by
    superseded_by — envelope values stay in entries() because _a_value() returns
    True before _superseded() is checked, and datums have no values to lose.

    Order-independence: the detection runs on the final merged model, comparing
    the values on two features that both already exist. The conflict entry names
    them in sorted (alphabetical) order, so the same pair produces the same
    conflict regardless of which page landed first or which feature carries the
    superseded_by marker. ADR-0019 requires this: merge() is order-independent,
    and this finding must be too.
    """
    features = (model or {}).get("features") or []
    by_id = {f.get("id"): f for f in features
             if isinstance(f, dict) and f.get("id")}

    found = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        canonical_id = feature.get("superseded_by")
        if not canonical_id:
            continue
        canonical = by_id.get(canonical_id)
        if not canonical:
            continue

        differing = _differing_feature_values(feature, canonical)
        if not differing:
            continue

        this_id = feature.get("id") or "unknown"
        pair = sorted([this_id, canonical_id])
        found.append({
            "about": "features → %s and %s (synonym pair, values differ)" % (pair[0], pair[1]),
            "stands": "%s says %s" % (pair[0], differing[pair[0]]),
            "was_also_said": "%s says %s" % (pair[1], differing[pair[1]]),
            "kind": A_CONFLICT,
            "machine_written": True,
        })
    return found


def _differing_feature_values(superseded, canonical):
    """
    The first position value where superseded and canonical disagree, or None.

    Compares ONLY the value content of position entries, never provenance keys
    (flag, round_in, seen_in, from, because, was). Two entries with the same
    value but read from different views are NOT a conflict.

    Does NOT compare axis (design.md:497-500 and crosswise() own axis rules),
    and skips position comparisons where values have different units (mm vs °)
    since those are different physical quantities, not conflicting readings.

    Returns a dict mapping each feature id to its value at the differing field,
    suitable for the conflict message, or None if values agree.
    """
    sup_id = superseded.get("id") or "unknown"
    can_id = canonical.get("id") or "unknown"

    superseded_pos = superseded.get("position") or {}
    canonical_pos = canonical.get("position") or {}
    if isinstance(superseded_pos, dict) and isinstance(canonical_pos, dict):
        for datum in set(superseded_pos) & set(canonical_pos):
            sv = superseded_pos.get(datum)
            cv = canonical_pos.get(datum)
            if not sv or not cv:
                continue
            sv_value = _extract_value(sv)
            cv_value = _extract_value(cv)
            if sv_value and cv_value and sv_value != cv_value:
                if _different_units(sv_value, cv_value):
                    continue
                return {
                    sup_id: "position → %s: %s" % (datum, sv_value),
                    can_id: "position → %s: %s" % (datum, cv_value),
                }
    return None


def _numeric_value(text):
    """
    The numeric part of a measurement, or None if the text is not a number.

    Extracts the first number from strings like "25 mm", "0 mm (origin)",
    "42.5 mm (reference: ...)", but returns None for non-numeric placeholders
    like "to be read again off the later page".

    Two features agreeing on a non-numeric placeholder is not evidence they
    describe the same placement — only agreement on actual measurements counts.
    """
    if not text:
        return None

    text_str = str(text)
    match = re.search(r'[-+]?(?:\d+\.?\d*|\.\d+)', text_str)
    if match:
        try:
            return float(match.group(0))
        except ValueError:
            return None
    return None


def _axis_direction(feature):
    """
    The direction (length/width/height/depth/thickness) an axis runs along, or None.

    Classifies the axis value by the words it uses: "along the depth",
    "perpendicular to the front face", "parallel to the height", etc.
    Returns one of _A_DIRECTION or None if the axis is missing or does not
    name a direction from that vocabulary.

    Features whose axis is described through faces ("perpendicular to the top
    and bottom faces") rather than a direction word are invisible to this
    check — they have no direction the check can classify, so they are excluded
    from comparison. This is a known limitation: the check sees only features
    whose axis is named by a direction word.
    """
    axis_val = feature.get("axis")
    if not axis_val:
        return None

    if isinstance(axis_val, dict):
        axis_val = axis_val.get("value")
    if not axis_val:
        return None

    axis_text = str(axis_val).lower()

    directions_found = [direction for direction in _A_DIRECTION
                       if direction in axis_text]

    if len(directions_found) == 1:
        return directions_found[0]

    return None


def twice_placed(model) -> list:
    """
    Two features with the same axis direction, agreeing at one datum and
    differing at another — one placement described twice.

    The recorded run (logs/sessions/247c2aaed0734866a8aa9d169f7303bd.jsonl,
    2023 pipe clamp) produced bore-ring and boss-ring. Both run along the
    depth ("perpendicular to the front view, running front-to-rear" and
    "perpendicular to the front face"). Both agree at "the left end face:
    65.5 mm" and differ at "the bottom face": bore-ring says 25 mm, boss-ring
    says 15 mm. One physical ring, two descriptions with conflicting heights,
    and both passed into Acceptance where satisfying them simultaneously is
    impossible.

    Agreement and disagreement count only for actual measurements, never for
    placeholders. Two features both saying "to be read again" at a datum is
    not evidence they describe the same placement — the check compares numbers,
    and a datum carrying no number is skipped.

    The rule requires all three:
    1. Axes point the same way (both along depth, both along height, etc.)
    2. At least one shared datum where numeric values agree
    3. At least one shared datum where numeric values differ

    Without (1), two features may share datums by coincidence but describe
    different geometry: bore-ring and hole-right both say "the bottom face:
    25 mm" but bore-ring runs along depth while hole-right is described
    through faces, so its axis is not classified and it is excluded from
    the check entirely.

    Without (2), two features along the same axis but placed differently are
    legitimately separate: two holes both vertical but at different positions.

    Without (3), two features agreeing everywhere are synonym candidates for
    reconciliation (superseded_by), not conflicts.

    Only features NOT already marked superseded_by are checked — synonyms
    resolved by reconciliation do not trigger this.

    Returns conflicts naming both features in sorted order (ADR-0019
    order-independence: same pair produces same conflict entry regardless
    of which page arrived first).
    """
    features = [feature for feature in _features(model)
                if not feature.get("superseded_by")]

    by_direction = {}
    for feature in features:
        direction = _axis_direction(feature)
        if direction:
            by_direction.setdefault(direction, []).append(feature)

    found = []
    for direction, group in by_direction.items():
        for index_a, feature_a in enumerate(group):
            for feature_b in group[index_a + 1:]:
                position_a = feature_a.get("position") or {}
                position_b = feature_b.get("position") or {}

                if not isinstance(position_a, dict) or not isinstance(position_b, dict):
                    continue

                shared_datums = set(position_a.keys()) & set(position_b.keys())
                if not shared_datums:
                    continue

                agreeing = []
                differing = []

                for datum in shared_datums:
                    value_a = _extract_value(position_a[datum])
                    value_b = _extract_value(position_b[datum])

                    if not value_a or not value_b:
                        continue

                    if _different_units(value_a, value_b):
                        continue

                    numeric_a = _numeric_value(value_a)
                    numeric_b = _numeric_value(value_b)

                    if numeric_a is None or numeric_b is None:
                        continue

                    if numeric_a == numeric_b:
                        agreeing.append((datum, value_a))
                    else:
                        differing.append((datum, value_a, value_b))

                if agreeing and differing:
                    id_a = feature_a.get("id") or "unknown"
                    id_b = feature_b.get("id") or "unknown"
                    pair = sorted([id_a, id_b])

                    datum_diff, value_a_diff, value_b_diff = differing[0]

                    found.append({
                        "about": "features → %s and %s (same axis, one placement described twice)" % (pair[0], pair[1]),
                        "stands": "%s says %s: %s" % (pair[0], datum_diff, value_a_diff if pair[0] == id_a else value_b_diff),
                        "was_also_said": "%s says %s: %s" % (pair[1], datum_diff, value_b_diff if pair[1] == id_b else value_a_diff),
                        "kind": A_CONFLICT,
                        "machine_written": True,
                    })

    return found


def _different_units(value_a, value_b):
    """
    Whether two values have different units and thus cannot be compared.

    A length (mm) and an angle (°) are different physical quantities, not two
    readings of one dimension that might conflict.
    """
    a = str(value_a).lower()
    b = str(value_b).lower()
    a_is_length = "mm" in a or "cm" in a or "m " in a
    b_is_length = "mm" in b or "cm" in b or "m " in b
    a_is_angle = "°" in a or "deg" in a
    b_is_angle = "°" in b or "deg" in b
    if (a_is_length and b_is_angle) or (a_is_angle and b_is_length):
        return True
    return False


def _extract_value(payload):
    """
    The value a payload states, stripped of provenance.

    A payload is either a dict with a value key (the measurement) and provenance
    keys (flag, round_in, seen_in, from, because, was), or a bare value. This
    returns only the measurement, never the provenance.
    """
    if isinstance(payload, dict) and "value" in payload:
        return payload["value"]
    if isinstance(payload, dict):
        return None
    return payload


_ROUNDED_OUTSIDE = ("capsule", "obround", "racetrack", "oval",
                    "rounded end", "semicircular end", "radiused end",
                    "rounded exterior", "rounded end surfaces")

_FLAT = ("planar", "flat")

_AN_END = ("end face", "end faces", "end of", "left end", "right end")


THE_OUTSIDE = "envelope → the shape of the outside"


def nobody_carries_the_outside(model) -> str:
    """
    The standing finding that the outside of the part is nobody's, or "".

    `THE_OUTSIDE` is what that conflict is about, and it is named rather than
    left to the prose because the loop has to recognise this one finding: it is
    the only one that says the *shape* of the part is nobody's, and a run that
    goes on past it builds a box.

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
    neither was ever written under the same address. A datum only contradicts a
    rounded outside where it is about the ends.

    The words looked for are the ones a draughtsman and a description both use
    for an outside that is curved rather than boxed, and the list is short on
    purpose: a word that also means something inside the part would raise this on
    every model.

    The finding is quoted from the description's own words where it has any. The
    sweep of the sheet is in `_said` too, and quoted from there it reads as the
    draughtsman's callout — `R12.5 — plan view (rounded ends)` — rather than as
    the reading the user is being asked to settle. The sweep is still the
    fallback: a sheet that prints rounded ends the description never wrote down
    is the same finding, and it is the evidence there is.
    """
    said = _said(model, "")
    rounded = [(path, text) for path, text in said
               if any(word in text.lower() for word in _ROUNDED_OUTSIDE)]
    if not rounded:
        return []

    found = []
    where, why = next((entry for entry in rounded
                       if not entry[0].startswith("printed_on_the_sheet")), rounded[0])
    if not any(_carries_the_outside(feature) for feature in _features(model)):
        found.append({
            "about": THE_OUTSIDE,
            "stands": "no feature carries the outer contour, so the outside of the part is "
                      "whatever profile the envelope's overall sizes are padded into",
            "was_also_said": "%s: %s" % (where, why),
            "machine_written": True,
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
                "machine_written": True,
            })
    return found


_HAS_AN_AXIS = ("hole", "slot", "pocket")

_HAS_AN_AXIS_PROSE = ("hole", "bore", "slot", "pocket")

_GOES_THROUGH = "through"

_LIES_IN_A_PLANE = ("profile", "arc", "rounded", "radiused")


def _what_it_is(feature):
    """
    `kind`, and `kind` alone — the one field the closed vocabulary lives in.

    `id` is a label the model chooses and is never checked against anything,
    and `topology` says what a feature runs into, not what it is: a fillet
    whose edge-selection prose runs "...the outer edges of the arms and band,
    the ring's two end rims, the cutout and the slot" is still a fillet
    (FCC-243, session `480b9995421e443d97b439a4f55bc3fe` —
    `fillet-outer`, `kind: "fillet"`, was asked which way its axis points, and
    flagged as an unplaced cavity, because "slot" and "cutout" sat in its
    `topology`, off the same sentence, through this one field).
    """
    return str(feature.get("kind") or "").strip().lower()


def _is_one_of(feature, closed_members, prose_words):
    """
    Whether *feature*'s `kind` names one of *closed_members* of ADR-0026's
    22, read whole where `kind` is one of them, or a substring of
    *prose_words* where it is not.

    `config/prompts/design.md`'s `kind_vocabulary: closed` marker is sent on
    roughly a third of Design replies (`_refused_kinds`, session
    `afa0142baef847b0a9cd9139390af993`: 3 of 9), so most `kind` fields are
    still the prose grammar this reads as a fallback rather than stops
    reading — only `id` and `topology` are dropped (FCC-243).

    *prose_words* may say a word *closed_members* does not: `bore` reads a
    prose `kind` (a legacy "Ø42 bore" is still a hole) but is never checked
    as one of the 22, because it is not one — FCC-232 folded it into `hole`
    and a compliant reply is refused before `kind` can be `"bore"`
    (`_refused_kinds`). `_BANNED_KIND_SYNONYM` and this are therefore not two
    places disagreeing about whether `bore` is a word; one is the gate on
    what a closed `kind` may say, the other is a fallback read of what a kind
    that never passed the gate said anyway.
    """
    kind = _what_it_is(feature)
    if kind in FEATURE_KIND:
        return kind in closed_members
    return any(word in kind for word in prose_words)


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
    way it measures a distance. Where one is missing this is a gap, in the same
    list `silhouette` writes to and settled the same way: by the user at the
    Plan Gate, or by Design filling in the field it left out.

    The kinds that have an axis — the direction the hole is drilled, the slot
    cut, the pocket sunk — are listed short and literal, because a word that also
    describes a face would raise this on every model. The kinds that lie in a
    plane are the curves rather than the holes: what a radius means depends on
    the plane it is drawn in and on nothing else on the sheet, so they carry a
    plane the way a hole carries an axis. Fillets and chamfers are deliberately
    not among them, because they follow the edges they are put on and asking them
    for a plane would raise a finding on every part that has any.

    What a feature *is* is read off `kind` alone, through `_is_one_of`, never
    off `id` or `topology`: a fillet whose edge-selection prose runs "...the
    cutout and the slot" is still a fillet, and `id` is a label the model
    chooses and never checked against anything (FCC-243). Neither list is
    exact-only, because `config/prompts/design.md`'s closed marker is sent on
    about a third of Design replies and `kind` is prose the rest of the
    time — a fillet is excluded from the plane check the same way whichever
    world it is read in: its closed `kind` is not one of `_LIES_IN_A_PLANE`'s
    four, and its prose carries none of their words either.
    """
    found = []
    for feature in _features(model):
        where = _under("features", _named(feature, _IDENTITY) or "a feature")
        for closed, prose, field, missing in (
                (_HAS_AN_AXIS, _HAS_AN_AXIS_PROSE, "axis", "which way its axis points"),
                (_LIES_IN_A_PLANE, _LIES_IN_A_PLANE, "plane", "which plane it lies in")):
            if _is_one_of(feature, closed, prose) and not _filled(feature.get(field)):
                found.append({
                    "about": _under(where, field),
                    "stands": "nothing records %s, so it is built along the base "
                              "profile like everything else" % missing,
                    "was_also_said": str(feature.get("kind")
                                         or feature.get("topology") or ""),
                    "kind": A_GAP,
                    "machine_written": True,
                })
    return found


_A_DIRECTION = ("length", "width", "height", "depth", "thickness")

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
    over, since even a blind one starts only once. A blind feature is the one
    that has a face it starts from; what a feature says when it has none is that
    it goes through.

    "Through" is read off `topology` alone, never off `id` or `kind`: it is
    not one of ADR-0026's 22 and never will be, because it says how a feature
    sits rather than what it is (FCC-243).

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

    Says `kind: A_CONFLICT` on every entry rather than leaving it for
    `_which_kind_it_is`'s default to supply (FCC-248): the two axes not
    agreeing is a conflict by the same words `HEADED` gives that kind, and an
    unset field said nothing about who found it. The rate this changes is
    zero — A_CONFLICT is what the default already returned for an unset
    `kind`, so `tests/recorded/evidence_kind_ratio.json`'s 23 of 26 sessions
    with a `conflict`-kind entry read 23 of 26 before this tag and 23 of 26
    after it. Tagging makes the kind intentional; it does not move the count.
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
            found.append({
                "about": _under(where, "axis"),
                "stands": "this is round in %s, and %s is round in %s — two views, so "
                          "the two axes cross, and these were written the same way"
                          % ((view,) + next((other.split(UNDER)[-1], at)
                                            for at, other in along if at != view)),
                "was_also_said": "%s: %s" % (_under(where, "axis"), axis),
                "kind": A_CONFLICT,
                "machine_written": True,
            })

    for view in {at for at, _, _ in seen}:
        here = [(said, where) for at, said, where in seen if at == view]
        if len({said for said, _ in here}) < 2:
            continue
        for axis, where in here:
            found.append({
                "about": _under(where, "axis"),
                "stands": "everything round in %s has the one axis, and the features "
                          "round in it here were given %d different ones"
                          % (view, len({said for said, _ in here})),
                "was_also_said": "%s: %s" % (_under(where, "axis"), axis),
                "kind": A_CONFLICT,
                "machine_written": True,
            })
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

    The directions are the words the envelope measures the part in, and a feature
    carrying one of them carries a size along a direction. A diameter and a
    radius are not among them, because neither is a size along anything named.
    The two a feature spends on itself are its depth and its thickness: a blind
    pocket's depth is 6 mm down its own axis and a boss's thickness is what it
    stands proud by, neither of them 6 mm of the part's depth.
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
                "kind": A_GAP,
                "machine_written": True,
            })
    return found


_A_CAVITY = ("cutout", "cut-out", "cut out", "slot", "pocket", "recess",
             "relief", "notch", "opening", "scallop")


def placed_by_nothing(model) -> list:
    """
    A cavity nothing places, built wherever the profile happens to allow.

    The recorded run (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`,
    the 2023 pipe clamp) described the one cavity in the web twice: `slot-arm`,
    30 mm long, off the plan, and `cutout-web`, "profile as drawn in VIEW B",
    off the detail the sheet points at with `SEE VIEW B FOR CLARITY OF CUTOUT`.
    `cutout-web` carried `position: {}` — nothing at all. Plan then built both:
    a slot 8 mm wide it invented, and an R10 circle it invented, and the
    circle cut the Body into two solids and ended the run. This finding is the
    empty `position` alone, checkable and wrong on its own whether or not a
    second feature is the same physical cavity — `twice_over` is the arithmetic
    check for that; this one predates it and stays a separate defect
    (ADR-0026, FCC-232).

    `placement` cannot reach it — that one asks whether a size in a direction
    has a position in that direction, and this feature had no sizes either.
    Where another cavity is drawn in a view this one is also drawn in, the
    finding says so as well: two cavities sharing a view, one of them placed by
    nothing, is the shape this defect arrives in.

    A cavity is a hole in the material that is not a drilled one, in the words a
    description uses for it. It is what a detail view is usually drawn for, and a
    detail view is the second picture a cavity gets described from.
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
            "kind": A_GAP,
            "machine_written": True,
        })
    return found


def twice_over(model) -> list:
    """
    Two features of the same `kind`, placed against a shared datum, and not
    already reconciled by `superseded_by` — one physical feature read twice.

    `config/prompts/design.md`'s reconciliation turn already asks the model
    for exactly this arithmetic: "same kind, overlapping position datums ...
    if all values match, add superseded_by ... if any value differs, raise a
    conflicts entry" — a rule the prompt could ask for and this module could
    not check, because `kind` was prose and no two readings of it were ever
    equal. Four such pairs stand in 47 recorded models — `slot-arm`/`cutout-web`, `slot`/`cutout-blend`,
    `bore-upper`/`hole-boss`, `left-mounting-hole`/`phase2-left-mounting-slot`
    — none caught before this ticket because the only check written was an
    empty `position` (`placed_by_nothing`), which is a proxy the sheet does
    not always leave: `bore-upper` and `hole-boss` both carry a filled
    `position`, framed off two different datums that pin the same point.

    `kind` equality is exact — the enum this ticket closes it into — never a
    substring search of `id`/`kind`/`topology` the way the rest of this module
    reads what a feature is elsewhere (ADR-0026: "no substring search, and no
    proxy standing in for a check that could not be written"). Values at a
    shared datum are not required to agree: agreement is a plain duplicate,
    disagreement is `unsafe_supersession`'s territory once `superseded_by`
    names the pair — this finding is what points the model at declaring it.
    """
    features = [feature for feature in _features(model) if not feature.get("superseded_by")]

    found = []
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
            if not shared:
                continue
            id_a = feature_a.get("id") or "unknown"
            id_b = feature_b.get("id") or "unknown"
            pair = sorted([id_a, id_b])
            found.append({
                "about": "features → %s and %s (same kind, placed against %s)"
                         % (pair[0], pair[1], ", ".join(shared)),
                "stands": "both are a %s, placed against %s — one physical feature described twice"
                          % (kind_a, ", ".join(shared)),
                "was_also_said": kind_a,
                "kind": A_GAP,
                "machine_written": True,
            })
    return found


_SAYS_WHAT_SETTLED_IT = {"assumed": "because", "derived": "from"}


def settled_by_nothing(model) -> list:
    """
    An entry that does not say what settled its value.

    `assumed` carries `because` and `derived` carries `from` — one line each
    saying who settled the value and on what (ADR-0022). Both requirements were
    written in `config/prompts/design.md` and in nothing that runs, so the
    section the Plan Gate shows a user rested on the model doing as it was told:
    `decided_for_you` fell back to prose saying nobody supplied the value and
    nothing said what settled it, which is a value nobody can check.

    One rule rather than two, because it is one defect: the entry is incomplete,
    and which field is missing follows from the flag. A gap and not a refusal,
    like every other reading defect — the run carries on and the user settles
    it at the Gate.
    """
    found = []
    for path, node in _each_value(model, ""):
        says = _SAYS_WHAT_SETTLED_IT.get(str(node.get("flag") or ""))
        if says and not _filled(node.get(says)):
            found.append({
                "about": path,
                "stands": "`%s` with no `%s`: nothing else on the model can say "
                          "afterwards where the value came from"
                          % (node.get("flag"), says),
                "was_also_said": str(node.get("value") or ""),
                "kind": A_GAP,
                "machine_written": True,
            })
    return found


_CAME_OFF_THE_PICTURE = ("traced", "traces", "tracing")

_A_MEASUREMENT = re.compile(r"\d+(?:\.\d+)?\s*mm")


def settled_by_the_tracing(model) -> list:
    """
    Every conflict of whose two readings one was measured off the view.

    A conflict is settled by the user at the Plan Gate, and that is right where
    both readings are somebody's opinion of a callout. It is not the shape of
    this one. The recorded run
    (`logs/sessions/f8aaa6953cf54651bd57a3f65432d474.jsonl`, mm25-p1-1.pdf)
    filed the central pocket as stated 40 mm wide by 30 mm high against a
    profile traced off the middle-left view at 40 by 25, and the Plan used
    neither: it took the stated height, kept the R30 centres on the end-bore
    axes, and built a pocket 48.038476 mm wide, agreeing with nothing on the
    sheet. Before that it declared the Step unbuildable and asked once for the
    conflict to be settled — a question the description had already answered.

    So where one of the readings came off the picture, that one governs: it is
    the only one of the two that was measured rather than reasoned, which is the
    same thing that earns `grounded` its correction. A finding, not a write:
    `about` is the address, `instead` is the tracing and `stands` names the
    conflict it settles, and the loop writes it through `corrected` so the
    displaced reading stays beside it under `was`.

    Narrow on purpose. Both sides talking about a tracing, or neither, is a
    conflict nobody measured and it rides to the Gate. The address has to be
    named twice over — the conflict names a feature the model carries, and what
    it is `about` names a field of that feature — because a tracing written at
    a field nobody pointed at is one more profile the sheet does not have.
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


def _features(model):
    """
    The features the part has, which is not the features the description holds:
    a feature the later page replaced is judged by nothing, the way it is
    measured by nothing (FCC-127).
    """
    return [feature for feature in (model or {}).get("features") or ()
            if isinstance(feature, dict) and not _superseded(feature)]


def _superseded(node):
    """Whether a later page replaced this, which is what makes it not the part."""
    return isinstance(node, dict) and _filled(node.get("superseded_by"))


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
        written.extend(_under_each_kind(model[section]) if section == "conflicts"
                       else _lines(model[section], "  "))
    return "\n".join(written)


def _under_each_kind(conflicts):
    """
    What nobody agrees on, one to an address, under a heading for each kind
    there is any of.

    A user does three different things with the three — choose between two
    readings, supply a value nothing on the part carries, look at the sheet a
    check never reached — and read as one column they are thirty-three
    paragraphs to sort by hand. The recorded run's four entries that named what
    ended it stood among twenty-nine that did not.

    One to an address, because this is where a person reads the list and the
    same disagreement written twice in two words is one disagreement to settle
    (`each_disagreement_once`). The merge holds both and goes on holding them.

    The kind is not written on the entry as well, because the heading it stands
    under has just said it. `machine_written` (FCC-249) is dropped the same
    way: who found it is not what the Gate reads a finding to settle.
    """
    written = []
    grouped = _grouped_by_kind(_one_to_an_address(conflicts))
    for kind in KINDS:
        found = [{key: said for key, said in conflict.items()
                 if key not in ("kind", "machine_written")}
                 if isinstance(conflict, dict) else conflict
                 for conflict in grouped[kind]]
        if found:
            written.append("  " + HEADED[kind])
            written.extend(_lines(found, "    "))
    return written


_THE_PROOF = ("from", "seen_in", "round_in")


def distilled(model) -> dict:
    """
    The part, without the evidence Design gathered to settle it.

    Design ends in two steps — gather every piece of evidence it can, then
    distil — and this is the second, which is why it is not a phase of its own:
    a projection of the model that already exists, so there is no second
    structure to hold an order the drawing never showed (ADR-0019, ADR-0024).

    The distiller is a hybrid and this is its mechanical half, kept mechanical
    so that what is left to judgement is only what no rule can decide. What
    goes is the sweep of the sheet, the disagreements and the gaps something
    has since answered, none of which is the part and none of which `entries`
    has ever walked; on a value, what fixed it, the views it was seen in and
    the view its circle was traced round in; and the features a later page
    deletes, which are what the sheet said and not what the part is (FCC-127).
    All of it stays in the evidence log, which is where the reading is checked
    against the drawing. What stays here is every address the Acceptance is
    derived from, so the description and the criteria still come off one model
    (ADR-0020).

    A disagreement goes whole and is settled off the reading rather than off
    this: Plan is handed the part and nothing that argues about it (FCC-156),
    and the Gate reads what nobody agrees on where it stands — beside the
    document, one entry to an address (`each_disagreement_once`).

    A gap is retired here for the same reason a dead datum's values are
    re-measured here: the merge only ever grows, so what nobody had supplied
    when the sheet was first read stays on the reading and in the log, and what
    the Gate and Plan are shown is what nobody supplies now (ADR-0019,
    FCC-153). A gap nobody answered survives whole, with the views it was
    looked in.

    A datum goes with the feature it belonged to, and every live value measured
    from a dead one is re-measured from the datum that takes over (FCC-153).
    That is the one thing here the document says and the reading does not, and
    it is why the re-measure is the distiller's rather than the merge's: the
    merge only ever grows, so the description the Gate is read against is still
    the sheet's own (ADR-0019). `measured_from_what_is_gone` is what says none
    of them is left.

    `because` stays with it. It is not the proof of a number off the sheet, it
    is what the model settled a value nobody supplied — the one thing at the
    Gate that is the user's to overrule, read there through `decided_for_you`
    and in the file afterwards (ADR-0022).

    The other half is the model's, because neither identity nor an
    inconsistency is decidable by a rule: two entries can be one feature under
    two names, and a distilled part can still hold values that do not follow
    from each other. Its boundary is five verbs — drop, merge, re-address,
    re-measure from a surviving datum, flag — and `carried_by_no_source` is
    what holds it. A value no source carries stays a gap and is settled the way
    every gap is.

    `tests/test_loop.py` measures what this leaves of the recorded reading of
    the 2023 pipe clamp, against what Plan was handed of it before, and
    `tests/test_modelmania_evidence.py` measures the title block it drops off
    the sheet that reading was taken from.
    """
    taken_over = _which_datum_takes_over(model)
    document = {section: _re_measured(_without_the_evidence(value), taken_over)
                for section, value in (model or {}).items()
                if section not in _NOT_THE_PART}
    still_missing = [gap for gap in (model or {}).get("not_defined") or ()
                     if not _answered(document, gap)]
    return dict(document, not_defined=still_missing) if still_missing else document


def _without_the_evidence(node):
    """
    What is left once the rule has taken what proves a value and what the
    drawing deleted, both of which the log beside the document keeps.
    """
    if isinstance(node, dict):
        return {key: _without_the_evidence(value) for key, value in node.items()
                if key not in _THE_PROOF}
    if isinstance(node, list):
        return [_without_the_evidence(entry) for entry in node
                if not _superseded(entry)]
    return node


def each_disagreement_once(model) -> dict:
    """
    The reading with what nobody agrees on said once, each entry saying which
    of `KINDS` it is.

    The other half of what distillation leaves, and the half that is no part of
    the document: Plan is handed the part and nothing that argues about it
    (FCC-156), so the list stands in the log beside it and the user settles it
    at the Gate off the reading they are shown.

    That sentence names the Gate because FCC-156 does, but no code path opens
    the log for a person to read: the Plan Gate is shown the distilled document
    alone, and nothing in `ui/` links, narrates or opens the file this list is
    written to (FCC-244). "The user settles it" is FCC-156's aspiration for
    that log, not a description of a screen; the log itself is still the
    intended surface, so `grouped_conflicts` orders what is written to it
    rather than inventing a second one.

    Said once here and not in the merge, because the merge only ever grows: a
    pass writes down what it found in the words it found it in, and which of
    those entries is one disagreement is decided where the list is written down
    and read out (ADR-0019, FCC-153).
    """
    standing = _one_to_an_address((model or {}).get("conflicts") or ())
    return dict(model, conflicts=standing) if standing else model


def _grouped_by_kind(conflicts):
    """
    *conflicts*, one list per member of `KINDS`, in `KINDS`' own order —
    disagreements, then gaps, then check hazards — each list keeping the
    order its entries arrived in.

    `_under_each_kind` and `grouped_conflicts` both need this grouping, one to
    render it as headed text and the other to write it back out as structured
    entries a heading cannot carry, so the grouping is pulled out once rather
    than kept as `_under_each_kind`'s private loop (FCC-244).
    """
    return {kind: [conflict for conflict in conflicts if _which_kind_it_is(conflict) == kind]
            for kind in KINDS}


def grouped_conflicts(model) -> dict:
    """
    *model*, with `conflicts` reordered so every model-written entry stands
    before every machine-written one.

    Ordered by `kind` before FCC-249 — disagreement, then gap, then check
    hazard — which still buried the one thing that mattered: `crosswise` and
    `_every_number_is_the_sheets` read as `A_CONFLICT` too
    (`_which_kind_it_is`'s default, FCC-248), the same kind the model's own
    disagreement reads as, so a real reading of the sheet stood wherever the
    merge happened to append it among however many machine-detected
    disagreements shared its kind. `machine_written` (FCC-249) is the field
    that actually knows; ordering by it is what `kind` was always asked to do
    and could not (FCC-248: at least one `A_CONFLICT` entry stands in 88% of
    recorded sessions, 73% even with the two worst-offending machine checks
    excluded — no kind-based order clears that bar).

    `_under_each_kind` orders `render()`'s Plan Gate text by `kind` still —
    the three-kind grouping FCC-244 built is a different question, choose
    between two readings against supply a value against read the sheet again,
    and this ticket does not reopen it. Only the evidence sidecar
    `part_file.logged` writes through this function moves.
    """
    conflicts = (model or {}).get("conflicts")
    if not conflicts:
        return model
    model_written = [conflict for conflict in conflicts if not _is_machine_written(conflict)]
    machine_written = [conflict for conflict in conflicts if _is_machine_written(conflict)]
    return dict(model, conflicts=model_written + machine_written)


def _is_machine_written(conflict):
    """
    Whether *conflict* was appended by a validator, read off the field it
    signs itself with at the moment it appends (FCC-249) — never off `kind`
    (`A_CONFLICT` is the correct tag for a validator's own finding as much as
    for the model's, FCC-248 measured that at 88% of sessions) and never off
    shape (a model reply can land on the exact field set a validator writes,
    FCC-237: 44 of 46 entries in one session missing `what` entirely).
    """
    return bool(isinstance(conflict, dict) and conflict.get("machine_written"))


def _worth_a_notice(model) -> bool:
    """
    FCC-248's condition, decidable now that authorship is signed rather than
    guessed: whether *model*'s conflicts hold at least one entry the model
    itself wrote.

    Stated, not wired — called from nowhere in this file or in `ui/`. Not
    settled by a rate measured here, either: `tests/recorded/vocabulary_
    kinds.json`'s 47 descriptions carry no `conflicts` entry at all before
    `merge()` runs, so nothing computed from them can say how often this
    predicate holds. The two recorded corpora that do carry conflicts put it
    near one model-written entry per description (session `480b9995421e44
    3d97b439a4f55bc3fe`: 1 of 29; `reading_2023_clamp.json`: 1 of 33) — not
    the rare case a naive reading of the empty corpus would suggest. Whether
    the notice ships, and against which real rate, is FCC-248's question,
    reopened once signed entries accumulate from runs made after this ships.
    """
    return any(not _is_machine_written(conflict)
              for conflict in (model or {}).get("conflicts") or ())


def _which_kind_it_is(conflict):
    """
    Which of `KINDS` an entry is, and `A_CONFLICT` for one that does not say.

    Read off the entry and never off its prose. Two readings that cannot both
    be true is what the section is for, what the prompt asks the model for and
    what the merge writes, so an entry nobody typed is that one; a check that
    finds a value nobody supplied or an outline nobody could ask about says so
    where it is found.
    """
    said = _named(conflict, ("kind",))
    return said if said in KINDS else A_CONFLICT


def _one_to_an_address(conflicts):
    """
    The disagreements, one to an address, each saying which of the three it is.

    The address is the merge's own path, which is what `_written` records in
    `about` and what `entries` cites a value by, so two entries are the same
    disagreement when they are about the same thing rather than when they are
    worded alike. `_appended` compares whole records, which is why the recorded
    run reached the Gate with one finding written three ways.

    Compared the way a view's name is compared, because an outline's verdict is
    addressed by the view it was traced in and the reader and the description
    spell a view differently — the rule `_as_a_view` keeps for `round_in`,
    applied to what an entry is about.

    The first entry at an address is the one that stands, for the reason a
    filled value stands: it is the earliest reading, and the later one is what
    the merge goes on holding (ADR-0019). An entry about nothing addresses
    nothing and stands beside them all.

    Its kind is whatever was said at that address, which is not always said by
    the entry that stands there: a check names the kind where it makes the
    finding, and the same finding read back to Design and written down again in
    the model's own words has lost it. An entry saying nothing is unclassified
    rather than classified, so what another entry about the same thing says
    governs it, and `A_CONFLICT` is what is left when nothing anywhere said.
    """
    kinds = {}
    for conflict in conflicts:
        at, said = _addressed_by(conflict), _named(conflict, ("kind",))
        if at and said in KINDS:
            kinds.setdefault(at, said)

    seen = set()
    standing = []
    for conflict in conflicts:
        at = _addressed_by(conflict)
        if at and at in seen:
            continue
        seen.add(at)
        standing.append(dict(conflict, kind=kinds.get(at) or _which_kind_it_is(conflict))
                        if isinstance(conflict, dict) else conflict)
    return standing


def _addressed_by(conflict):
    """What an entry is about, said the one way a view's name is said."""
    return _as_a_view(_named(conflict, ("about",)) or "")


def _answered(document, gap):
    """
    Whether *document* carries a value at what *gap* says is missing.

    A gap is prose and an address is not, so what the two share is words: every
    step of the address is named in the entry, and the innermost of them — the
    field — is named ahead of what carries it. That order is what tells "the
    width of the ring split", which the split's own width answers, from "the
    lengthwise position from the left mounting-hole axis", which the hole's
    axis does not: the second names the datum the missing value would be
    measured from, and a datum is not what the entry lacks.

    The conservative way round deliberately. A retirement missed leaves a gap
    on the list that something has answered, which is untidy; a gap retired
    that nobody answered goes off the Gate taking with it the views it was
    looked in, and those are what let a user point at the view that was missed.
    """
    said = _in_the_same_words(_named(gap, ("what",)) or "")
    return bool(said) and any(_named_ahead(said, at.split(UNDER)[1:] or [at])
                              for at, _ in _each_value(document, ""))


def _named_ahead(said, steps):
    """Whether *said* names every step of an address, the innermost of them first."""
    where = [_where_it_is_named(said, _in_the_same_words(step)) for step in steps]
    return None not in where and where[-1] == min(where)


def _where_it_is_named(said, step):
    """Where *said* says *step* whole, rather than inside a longer word."""
    found = re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(step), said)
    return found.start() if found else None


def _in_the_same_words(said):
    """One phrase with the difference between two ways of writing it gone."""
    return " ".join(word for word in _ONE_WORD_FROM_THE_NEXT.split(str(said).lower())
                    if word)


def measured_from_what_is_gone(document, evidence) -> list:
    """
    The values *document* measures from a datum that went with its feature.

    The invariant a discarded feature leaves behind: once something has
    discarded a feature, that feature can no longer be what another value is
    measured from (FCC-153). Left standing, the datum is a face the drawing
    deleted and every value keyed by it is a criterion the finished part is
    checked against and cannot meet.

    Read against the reading and not against the document alone: a document
    carries no `superseded_by`, because the features that carried it are what
    it dropped, so nothing in it can say which datums died. It is the same two
    arguments `carried_by_no_source` is held with, for the same reason.

    `tests/test_loop.py` runs it over the recorded reading of the 2023 pipe
    clamp, over the document distilled out of it, and over the same reading
    taken under an Intent that asks for the earlier revision.
    """
    taken_over = _which_datum_takes_over(evidence)
    found = []
    for at, entry in _each_value(document, ""):
        gone = _the_datum_that_is_gone("%s: %s" % (at, entry.get("value")), taken_over)
        if gone:
            found.append({"about": at, "stands": str(entry.get("value")),
                          "instead": taken_over[gone]})
    return found


def _which_datum_takes_over(model):
    """
    Datum name → the datum that takes its place, for every datum that went with
    the feature it belonged to.

    A feature owns a datum two ways, and both are read off the model rather
    than out of its prose. It owns the one named after it — `the left-lug-hole
    axis` is the hole's, and the slot that replaces the hole brings its own in
    the same words. And it owns the one it was placed at in a direction the
    feature replacing it is placed in too: the ear was where the length began
    and the arm at 45° is, so the arm's free end face takes over from the ear's
    tangent plane. A datum fixing two directions at once names no single one
    and is left where it is, which is `_which_way_each_datum_fixes`'s own rule.
    """
    fixes = _which_way_each_datum_fixes(model)
    features = [feature for feature in (model or {}).get("features") or ()
                if isinstance(feature, dict)]
    by_id = {str(feature.get("id")): feature for feature in features
             if feature.get("id")}

    taken_over = {}
    for gone in features:
        instead = by_id.get(str(gone.get("superseded_by") or ""))
        if not instead:
            continue
        for datum in _every_datum_named(model):
            named = _under_the_new_name(datum, str(gone.get("id") or ""),
                                        str(instead.get("id") or ""))
            if named:
                taken_over[datum] = named
        placed = _placed_in_each_direction(instead, fixes)
        for way, datum in _placed_in_each_direction(gone, fixes).items():
            if placed.get(way) and placed[way] != datum:
                taken_over[datum] = placed[way]
    return taken_over


def _every_datum_named(model):
    """Every datum the model names: the list, and what a position is keyed by."""
    return {str(datum["name"]) for datum in (model or {}).get("datums") or ()
            if isinstance(datum, dict) and datum.get("name")} | {
        str(key) for feature in (model or {}).get("features") or ()
        if isinstance(feature, dict) for key in (feature.get("position") or ())}


def _placed_in_each_direction(feature, fixes):
    """The datum this feature is placed by, in each direction that names one."""
    return {fixes[datum]: datum for datum in (feature.get("position") or ())
            if datum in fixes}


def _under_the_new_name(datum, gone, instead):
    """
    *datum* said with the feature replacing *gone*, where it is named after it.

    A whole identity or none of one: `left-arm` is written inside
    `left-arm-slot`, and a datum of the slot is not the arm's to rename.
    """
    written = re.sub(r"(?<![\w-])%s(?![\w-])" % re.escape(gone), instead, datum) \
        if gone and instead else datum
    return written if written != datum else None


def _the_datum_that_is_gone(said, taken_over):
    return next((datum for datum in taken_over if datum in said), None)


def _re_measured(node, taken_over):
    """
    A value measured from a datum that is gone, measured from the one that took
    over — and the magnitude not carried across with it.

    The two are different faces, so the number the sheet gave from one is not
    the number from the other, and a projection may not work out the one it was
    never given: the 108 mm the 2023 clamp is long was measured from a lug the
    second page replaces with an arm at 45°, and no overall to the arm's free
    end is printed anywhere. So the value says it is to be read again, keeps
    what it said under `was`, and is `assumed` with a `because` — which is what
    puts all seven of them in front of a person at the Gate, where a number
    silently kept would have been checked on the finished part and failed
    (ADR-0022).
    """
    if isinstance(node, dict):
        written = {}
        for key, value in node.items():
            value = _re_measured(value, taken_over)
            gone = _the_datum_that_is_gone("%s: %s" % (key, value.get("value")),
                                           taken_over) if _a_value(value) else None
            written[_instead(str(key), taken_over)] = \
                _read_again(value, gone, taken_over) if gone else value
        return written
    if isinstance(node, list):
        return [_re_measured(entry, taken_over) for entry in node
                if str(_named(entry, ("name",)) or "") not in taken_over]
    return node


def _read_again(value, gone, taken_over):
    return {"value": _TO_BE_READ_AGAIN, "flag": "assumed",
            "because": _WENT_WITH_THE_FEATURE % (gone, taken_over[gone]),
            "was": value.get("value")}


def _instead(said, taken_over):
    for gone, instead in taken_over.items():
        said = said.replace(gone, instead)
    return said


_TO_BE_READ_AGAIN = "to be read again off the later page"

_WENT_WITH_THE_FEATURE = "%s went with the feature the later page deleted, " \
                         "and %s takes its place"


def carried_by_no_source(document, evidence, intent) -> list:
    """
    The numbers the document states that neither the log nor the Intent does.

    The boundary the distiller is held to. Its mechanical half cannot cross it,
    since a projection states no number the model it came from did not; its
    other half is a model deciding identity and inconsistency in prose, and a
    model free to decide those is free to write a value no source ever carried,
    which is the failure the phase exists to prevent (ADR-0019). So it is
    checked rather than asked for: a number in the document was printed on the
    sheet, measured off it, worked out from something that was, or said in the
    Intent, and there is no fifth place for one to come from.

    `tests/test_loop.py` runs it over the recorded reading of the 2023 pipe
    clamp, and over the same document with one number added to it.
    """
    return sorted(numbers_in(document) - numbers_in(evidence) - numbers_in(intent))


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


def decided_for_you(model) -> list:
    """
    Every value on this part that nobody supplied, said in one line each.

    Walked off the model and never accumulated beside it: a list kept for the
    run is `you_asked_and_were_answered` rebuilt, which is what downstream
    started preferring over the description itself (ADR-0019, ADR-0022). The
    same walk therefore reads the file a user opens tomorrow and the section the
    Plan Gate showed them today.

    Two kinds are in it and they are the two the model may settle: a value it
    chose, which is `assumed` and says in `because` what it chose it off, and a
    reading it overruled, which carries the displaced value under `was`. What
    the sheet gave — `read`, `derived` — is nobody's decision and is not here.

    Who settled it is read off the line and not off a filter: `because` says it,
    and every `assumed` is here, including the one a person answered at Design's
    surviving question. Sniffing that prose for the word "user" would be a
    guess, and the section is honest either way — these are the values the sheet
    did not give. An `assumed` value with no `because` at all is a Design turn
    that did not do its job, and `settled_by_nothing` raises it at the merge —
    so `_NOBODY_SAID` is what a model that never went through one reads as, and
    not what a user is shown at the Gate.
    """
    return [_the_decision(path, node) for path, node in _each_value(model, "")
            if _filled(node.get("was")) or node.get("flag") == "assumed"]


def _the_decision(path, node):
    written = "%s: %s" % (path, node.get("value"))
    if _filled(node.get("was")):
        written += ", not %s" % node["was"]
    return written + " \u2014 %s" % (node.get("because")
                                     or node.get("from") or _NOBODY_SAID)


_NOBODY_SAID = "nobody supplied it and nothing says what settled it"


def _addressed(node, path):
    return [{"about": at, "flag": str(entry.get("flag") or ""),
             "says": "%s: %s" % (at, _one_value(entry) or entry.get("value"))}
            for at, entry in _each_value(node, path)]


def _each_value(node, path):
    """
    Every value on the model, with the address it sits under.

    Nothing of a feature a later page replaced. It stays on the description,
    because what the sheet said is what the user checks the reading against at
    the Gate, but it is not the part any more: the Model Mania run kept the
    Phase 1 mounting ear beside the Phase 2 arm that deletes it, every entry of
    it `read`, and so it was measured on the finished part and had to be cited
    by a Step — a bore the drawing had deleted, planned, and a revision spent
    being told it had been dropped (FCC-127).

    An entry of a list is addressed by its identity where it has one, so that an
    address survives a feature being added ahead of another — the same rule the
    merge matches by.
    """
    if _a_value(node):
        return [(path, node)]

    if isinstance(node, dict):
        if _superseded(node):
            return []
        return [found for key, value in node.items() if key not in _NOT_THE_PART
                for found in _each_value(value, _under(path, key))]

    if isinstance(node, list):
        return [found for at, value in enumerate(node, start=1)
                for found in _each_value(value, _under(path, _named(value, _IDENTITY) or at))]

    return []


def _written(held, offered, path, conflicts):
    """
    *offered* grown into *held* at *path*, a node at a time.

    A value and its flag are one thing: there is no half-filling a value, and
    20 mm read disagreeing with 20 mm assumed is a disagreement worth showing.

    `not_defined` -> a value is the one exception to the field growing only
    once: `not_defined` records that nothing supplied a value, not a value
    itself, so a later pass supplying one closes the gap instead of disagreeing
    with it (FCC-235). A field already carrying a value is unmoved either way.
    """
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

    if _stood_not_defined(held) and _a_value(offered) and not _stood_not_defined(offered):
        return offered

    conflicts.append({"about": path, "stands": held, "was_also_said": offered})
    return held


def _stood_not_defined(value):
    return isinstance(value, dict) and value.get("flag") == "not_defined"


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


def _lines(value, indent):
    """
    One value of the model as the lines it is rendered on.

    A handful of plain entries — the views a feature was seen in — read better on
    one line than as a column of bullets. A sweep of the whole sheet does not, so
    the line has a width.
    """
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
