"""Findings about a value nothing on the part supplies, and the write-up `decided_for_you` hands back for one the model settled on its own."""


import re
from core.sheets.sheet_reader import numbers_in
from core.design.core import (
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
    The disagreements, one to an address, each saying which of the three it is.

    The first entry at an address is the one that stands: it is the earliest
    reading, and the later one is what the merge goes on holding (ADR-0019).
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
    measured from. Left standing, the datum is a face the drawing deleted and
    every value keyed by it is a criterion the finished part cannot meet.
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

    One enumeration doing two jobs (ADR-0020): Acceptance checks this list, and
    Plan coverage is this list with citations taken away.
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

    Nothing of a feature a later page replaced: the Model Mania run kept the
    Phase 1 mounting ear beside the Phase 2 arm that deletes it, and so a
    bore the drawing had deleted was planned and a revision spent being told
    it had been dropped.
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

    `not_defined` -> a value is the one exception: `not_defined` records that
    nothing supplied a value, so a later pass supplying one closes the gap.
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
