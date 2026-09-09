"""Findings about whether a value is present at all, once -- superseded features that still disagree, the same placement said twice, and an outside no feature carries."""


import re
from core.design.core import (
    FEATURE_KIND,
    _A_DIRECTION,
    _conflict,
    _features,
    _said,
    _under,
)


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
        found.append(_conflict(
            "features → %s and %s (synonym pair, values differ)" % (pair[0], pair[1]),
            "%s says %s" % (pair[0], differing[pair[0]]),
            "%s says %s" % (pair[1], differing[pair[1]])))
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

    The rule requires all three: (1) axes point the same way, (2) at least
    one shared datum where numeric values agree, (3) at least one shared
    datum where numeric values differ. Without (3), two features agreeing
    everywhere are synonym candidates (superseded_by), not conflicts.

    Returns conflicts in sorted order (ADR-0019 order-independence).
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

                    found.append(_conflict(
                        "features → %s and %s (same axis, one placement described twice)" % (pair[0], pair[1]),
                        "%s says %s: %s" % (pair[0], datum_diff, value_a_diff if pair[0] == id_a else value_b_diff),
                        "%s says %s: %s" % (pair[1], datum_diff, value_b_diff if pair[1] == id_b else value_a_diff)))

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

    An `envelope` is three distances between named faces; a rectangular prism
    satisfies them exactly as well as an obround plate. So where the model
    calls the outside rounded and no feature owns that contour, that is a
    conflict. Ownership is looked for only in a feature's `id`, `kind` and
    `topology` — a fillet whose edge-selection prose mentions a capsule is
    what this exists to catch.

    The second finding is the datum that says the same face is flat — the
    merge cannot see it because neither was ever written under the same
    address.
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
    whose edge-selection prose mentions "cutout and slot" was flagged as an
    unplaced cavity, because those words sat in its `topology`.
    """
    return str(feature.get("kind") or "").strip().lower()


def _is_one_of(feature, closed_members, prose_words):
    """
    Whether *feature*'s `kind` names one of *closed_members* of ADR-0026's
    22, read whole where `kind` is one of them, or a substring of
    *prose_words* where it is not.

    The closed marker is sent on roughly a third of Design replies (3 of 9),
    so most `kind` fields are still the prose grammar this reads as a
    fallback. `bore` reads a prose `kind` but is never checked as one of the
    22, because it is not one — a compliant reply is refused before `kind`
    can be `"bore"`.
    """
    kind = _what_it_is(feature)
    if kind in FEATURE_KIND:
        return kind in closed_members
    return any(word in kind for word in prose_words)


def _carries_the_outside(feature):
    """Whether this feature *is* the rounded outside, rather than talking about it."""
    text = " ".join(str(feature.get(key) or "") for key in ("id", "kind", "topology")).lower()
    return any(word in text for word in _ROUNDED_OUTSIDE)
