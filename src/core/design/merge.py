"""Patch the held model with newly-offered evidence: diff two models,
merge one into the other, and correct or resolve one address at a time.

`diff` hands Plan a change-set of the part and what it disagrees about,
not the sweep of what the sheet never said -- the full document still
carries all of it to Assertion and Review, unchanged by `diff` existing.
"""


import re

from core.design.core import (
    CLOSED_KIND_VOCABULARY,
    FEATURE_KIND,
    UNDER,
    _BANNED_KIND_SYNONYM,
    _HEADED_BY,
    _IDENTITY,
    _REGION_WORD,
    _a_value,
    _appended,
    _conflict,
    _filled,
    _named,
    _the_entry,
    _under,
)
from core.design.presence_checks import (
    blanket_edge_treatment,
    silhouette,
    twice_placed,
    unsafe_supersession,
)
from core.design.orientation_checks import (
    crosswise,
    direction,
    pointing,
    unproven_datum,
)
from core.design.settlement_checks import (
    placed_by_nothing,
    placement,
    settled_by_nothing,
    twice_over,
)
from core.design.gaps import _written


ADDED = "added"


REMOVED = "removed"


CHANGED = "changed"


_DIFFED_SECTIONS = ("datums", "features", "conflicts")


def diff(previous, current):
    """
    The semantic change-set from *previous* to *current*.

    Matched by stable identity (`id`, `name`, `about`), so a moved datum is
    one `changed` entry, not a `removed`/`added` pair a text diff over
    re-serialized YAML would produce, and a conflict reaches Plan by the
    address it's about rather than being lost for lacking `id` or `name`.

    `envelope` and `not_defined` are not compared: values under a key, not objects with an identity.
    """
    changes = []
    for section in _DIFFED_SECTIONS:
        changes.extend(_section_diff(section, (previous or {}).get(section) or (),
                                     (current or {}).get(section) or ()))
    return changes


def _section_diff(section, before, after):
    by_identity_before = {_named(entry, _HEADED_BY): entry for entry in before
                          if _named(entry, _HEADED_BY)}
    by_identity_after = {_named(entry, _HEADED_BY): entry for entry in after
                         if _named(entry, _HEADED_BY)}
    changes = []
    for identity, entry in by_identity_after.items():
        if identity not in by_identity_before:
            changes.append({"section": section, "kind": ADDED, "id": identity,
                            "now": entry})
        elif by_identity_before[identity] != entry:
            changes.append({"section": section, "kind": CHANGED, "id": identity,
                            "was": by_identity_before[identity], "now": entry})
    for identity, entry in by_identity_before.items():
        if identity not in by_identity_after:
            changes.append({"section": section, "kind": REMOVED, "id": identity,
                            "was": entry})
    return changes


def merge(held, offered, record_conflicts=True):
    """
    *offered* written into *held*, filling only what is empty. A value that
    is already on the model stays, whatever a later pass believes it saw,
    what is empty is filled, what is new is added, and what disagrees is
    appended to `conflicts` unless *record_conflicts* is false, which drops
    only that record -- the held value still stands either way. A fragment
    that is not a model at all leaves the part exactly as it was.
    """
    if not isinstance(offered, dict):
        return _with_the_findings(dict(held or {}))

    conflicts = (list(_refused_kinds(offered)) + list(_refused_conflicts(offered))
                + list(_refused_dimensions(offered)))
    if conflicts:
        offered = _without_refused_kinds(offered)
        offered = _without_refused_conflicts(offered)
        offered = _without_refused_dimensions(offered)
    merged = _written(dict(held or {}), offered, "", conflicts, record_conflicts)
    if conflicts:
        merged["conflicts"] = _appended(merged.get("conflicts"), conflicts)
    return _with_the_findings(merged)


def _refused_kinds(offered):
    """
    Features in *offered* naming a `kind` off `FEATURE_KIND`, checked only
    when *offered* carries `"kind_vocabulary": "closed"`. Rejects the
    enforce-everywhere alternative, measured at 14.9% compliance; a banned
    prose synonym (`round` onto `fillet`) names the kind.
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
        found.append(_conflict(
            where, _refusal_reason(kind),
            "refused — the field is left as not yet filled"))
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
    Entries of `conflicts` missing one of `CONFLICT_ENTRY_FIELDS`, checked
    only under `kind_vocabulary: closed`. A reply invented four conflict
    shapes in one session, 44 of 46 entries with no `what` at all -- refused
    whole, travels on in `was_also_said`.
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
        found.append(_conflict(
            entry.get("about") or _under("conflicts", "entry %d" % index),
            "refused — a conflict entry carries %s, missing %s"
            % (", ".join(CONFLICT_ENTRY_FIELDS), ", ".join(missing)),
            entry))
    return found


def _without_refused_conflicts(offered):
    kept = [entry for entry in offered.get("conflicts") or []
            if not (isinstance(entry, dict)
                    and any(field not in entry for field in CONFLICT_ENTRY_FIELDS))]
    return dict(offered, conflicts=kept)


_DIMENSION_KEYS = ("mm", "deg")

_A_RANGE = re.compile(r"\bfrom\s+(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)\s*mm\b")

_A_LENGTH = re.compile(r"(-?\d+(?:\.\d+)?)\s*mm\b")

_AN_ANGLE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:\xb0|deg\b)")


def _the_number(text):
    """
    The one mm or degree magnitude *text* states -- `("mm", 24)`, `("mm",
    [0, 24])` for a range, `("deg", 45)` -- or None where *text* states no
    magnitude, or states more than one, since an entry naming several (an
    envelope giving two faces' distances in one sentence, a `tangent_to`
    explaining itself) is prose to read, not one value to attach a number to.
    """
    ranged = _A_RANGE.search(text)
    if ranged:
        return "mm", [_a_number(ranged.group(1)), _a_number(ranged.group(2))]
    lengths = _A_LENGTH.findall(text)
    angles = _AN_ANGLE.findall(text)
    if len(lengths) == 1 and not angles:
        return "mm", _a_number(lengths[0])
    if len(angles) == 1 and not lengths:
        return "deg", _a_number(angles[0])
    return None


def _a_number(digits):
    value = float(digits)
    return int(value) if value.is_integer() else value


def _needs_its_number(entry):
    """The `(unit, magnitude)` *entry*'s text names but does not carry, or
    None where it carries one already or names none."""
    if any(key in entry for key in _DIMENSION_KEYS):
        return None
    text = entry.get("value")
    return _the_number(text) if isinstance(text, str) else None


def _value_entries(node, path):
    """Every `{"value": ..., "flag": ...}` entry on *node*, with the address
    it sits under -- `conflicts` is skipped, the way `_said` skips it, since
    a disagreement's own `was_also_said` can itself be shaped like a value."""
    if isinstance(node, dict):
        if _a_value(node):
            yield path, node
            return
        for key, value in node.items():
            if key == "conflicts":
                continue
            yield from _value_entries(value, _under(path, key))
    elif isinstance(node, list):
        for at, value in enumerate(node, start=1):
            yield from _value_entries(value, _under(path, _named(value, _IDENTITY) or at))


def _refused_dimensions(offered):
    """
    A value in *offered* naming a single mm or degree magnitude with no
    `mm`/`deg` field beside it, refused -- the number is Design's to give,
    read off the drawing, not guessed later from the text by a Step.
    Checked only under `kind_vocabulary: closed`, the same marker
    `_refused_kinds` and `_refused_conflicts` gate on, so a recorded fixture
    written against the older prose-only grammar is not flagged for a
    contract it never opted into.
    """
    if not isinstance(offered, dict) or offered.get("kind_vocabulary") != CLOSED_KIND_VOCABULARY:
        return []
    found = []
    for path, entry in _value_entries(offered, ""):
        number = _needs_its_number(entry)
        if number is None:
            continue
        unit = number[0]
        found.append(_conflict(
            path, "%r names a %s magnitude with no %r field" % (entry["value"], unit, unit),
            "refused — the field is left as not yet filled"))
    return found


def _without_refused_dimensions(offered):
    return _stripped_of_bare_dimensions(offered)


def _stripped_of_bare_dimensions(node):
    if isinstance(node, dict):
        if _a_value(node) and _needs_its_number(node) is not None:
            return None
        cleaned = {}
        for key, value in node.items():
            if key == "conflicts":
                cleaned[key] = value
                continue
            stripped = _stripped_of_bare_dimensions(value)
            if stripped is not None:
                cleaned[key] = stripped
        return cleaned
    if isinstance(node, list):
        return [entry for entry in (_stripped_of_bare_dimensions(item) for item in node)
                if entry is not None]
    return node


def corrected(model, address, value, because):
    """
    The one write that overrules what is already on the model.

    A check finding something wrong has two moves -- say nothing, or stop
    the run; four findings stopped it and four were wrong, so this is the
    third move.

    Narrow: lands at the address a check named, carries *because* (the
    evidence off the drawing), and leaves the displaced value under `was`.
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


def _with_the_findings(model):
    found = (silhouette(model) + direction(model) + pointing(model)
             + crosswise(model) + placement(model) + placed_by_nothing(model)
             + twice_over(model) + settled_by_nothing(model)
             + unsafe_supersession(model) + twice_placed(model)
             + blanket_edge_treatment(model) + unproven_datum(model))
    if found:
        model["conflicts"] = _appended(model.get("conflicts"), found)
    return model
