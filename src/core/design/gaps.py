"""
Findings about a value nothing on the part supplies, and the write-up
`decided_for_you` hands back for one the model settled on its own.
`_answered` retires the conservative way round: a missed retirement leaves
an untidy but harmless gap, while a gap wrongly retired removes the views a
user needs to spot the miss. An `assumed` value with no `because` is what
`settled_by_nothing` raises at the merge, read by that model as
`_NOBODY_SAID`.
"""


import re
from core.sheets.sheet_reader import numbers_in
from core.design.core import (
    A_CONFLICT,
    KINDS,
    UNDER,
    _HEADED_BY,
    _IDENTITY,
    _NOT_THE_PART,
    _a_value,
    _filled,
    _named,
    _one_value,
    _superseded,
    _under,
    _which_kind_it_is,
)
from core.design.orientation_checks import (
    _ONE_WORD_FROM_THE_NEXT,
    _as_a_view,
    _which_way_each_datum_fixes,
)


def _one_to_an_address(conflicts):
    """
    The disagreements, one to an address, each saying which of the three it
    is. The first entry at an address is the one that stands: it is the
    earliest reading, and the later one is what the merge goes on holding. A
    disagreement takes the address over a gap standing at the same one,
    whatever their order: two readings that cannot both be true are what the
    user settles, and a value short of what settled it is not news beside one.
    """
    kinds = {}
    for conflict in conflicts:
        at, said = _addressed_by(conflict), _named(conflict, ("kind",))
        if said not in KINDS and isinstance(conflict, dict):
            said = _which_kind_it_is(conflict)
        if not at or said not in KINDS:
            continue
        if said == A_CONFLICT:
            kinds[at] = said
        else:
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

    Matched by address, every step named in the entry, with the innermost
    (the field) named ahead of what carries it -- so a field answers for
    itself but a datum it's measured from does not.
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

    The invariant a discarded feature leaves behind: once a feature is
    discarded, it cannot be what another value is measured from. Left
    standing, the datum is a face the drawing deleted and every value
    keyed by it is a criterion the finished part cannot meet.
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
    Datum name -> the datum that takes its place, for the feature it belonged to.

    A feature owns a datum by name, or by direction if it's placed where
    the replaced feature was. A datum fixing two directions at once is
    left alone -- `_which_way_each_datum_fixes`'s own rule.
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
    A value measured from a datum that is gone, measured from the one that
    took over -- magnitude not carried across. Recorded so a person checks
    it at the Gate rather than the number being silently kept and failing
    on the finished part.
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
    The numbers the document states that neither the log nor the Intent
    does. The distiller's boundary: its mechanical half states no number
    the model didn't give it; its prose half could invent one, which this
    phase exists to prevent. Checked against four sources only: printed on
    the sheet, measured off it, worked out from something that was, or said
    in the Intent.
    """
    return sorted(numbers_in(document) - numbers_in(evidence) - numbers_in(intent))


def entries(model) -> list:
    """
    Every value the part carries, addressed by where it sits and said in
    words. One enumeration doing two jobs: Acceptance checks this list, and
    Plan coverage is this list with citations taken away.
    """
    return _addressed(model, "")


def decided_for_you(model) -> list:
    """
    Every value on this part that nobody supplied, said in one line each,
    walked off the model each time, not accumulated. Holds two kinds: a
    chosen value (`assumed`, with `because` saying why) and an overruled
    reading (the old value under `was`); `read`/`derived` values are
    nobody's decision and are excluded. An `assumed` with no `because` is a
    Design turn that skipped its job.
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

    Nothing from a feature a later page replaced: the Model Mania run kept
    a Phase 1 mounting ear beside the Phase 2 arm deleting it, so a bore
    the drawing removed was still planned and had to be walked back.
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

    A value and its flag are one thing -- 20 mm read disagreeing with
    20 mm assumed is a real disagreement. `not_defined` is the one
    exception: it records that nothing supplied a value, so a later pass can fill it.
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

    By `id`, never by position -- a fragment naming one feature must not
    land on whichever happens to be first. An entry with no identity (a
    view, a gap) is appended if not already there.
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
