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
    an outside described in words and carried by nothing, and its findings join
    the same `conflicts` the disagreements go to.
    """
    if not isinstance(offered, dict):
        return _with_silhouette(dict(held or {}))

    conflicts = []
    merged = _written(dict(held or {}), offered, "", conflicts)
    if conflicts:
        merged["conflicts"] = _appended(merged.get("conflicts"), conflicts)
    return _with_silhouette(merged)


def _with_silhouette(model):
    found = silhouette(model)
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
    where, why = rounded[0]
    if not any(_carries_the_outside(feature) for feature in _features(model)):
        found.append({
            "about": "envelope → the shape of the outside",
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
