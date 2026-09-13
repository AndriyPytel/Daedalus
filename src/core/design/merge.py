"""Patch the held model with newly-offered evidence: diff two models,
merge one into the other, and correct or resolve one address at a time.

`diff` hands Plan a change-set of the part and what it disagrees about,
not the sweep of what the sheet never said -- the full document still
carries all of it to Assertion and Review, unchanged by `diff` existing.
"""


from core.design.core import (
    CLOSED_KIND_VOCABULARY,
    FEATURE_KIND,
    UNDER,
    _BANNED_KIND_SYNONYM,
    _HEADED_BY,
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
    silhouette,
    twice_placed,
    unsafe_supersession,
)
from core.design.orientation_checks import (
    crosswise,
    direction,
    pointing,
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


def merge(held, offered):
    """
    *offered* written into *held*, filling only what is empty. A value that
    is already on the model stays, whatever a later pass believes it saw,
    what is empty is filled, what is new is added, and what disagrees is
    appended to `conflicts`. A fragment that is not a model at all leaves
    the part exactly as it was.
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
             + unsafe_supersession(model) + twice_placed(model))
    if found:
        model["conflicts"] = _appended(model.get("conflicts"), found)
    return model
