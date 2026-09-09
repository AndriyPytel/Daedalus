"""Turn a model into what a reader or a Plan reads back -- the rendered page, the distilled summary, and conflicts grouped by kind."""


from core.design.core import (
    HEADED,
    KINDS,
    SECTIONS,
    _HEADINGS,
    _NOT_THE_PART,
    _filled,
    _superseded,
    _which_kind_it_is,
)
from core.design.gaps import (
    _answered,
    _lines,
    _one_to_an_address,
    _re_measured,
    _which_datum_takes_over,
)


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
    What nobody agrees on, one to an address, under a heading for each kind.

    The recorded run's four entries that named what ended it stood among
    twenty-nine that did not. `machine_written` is dropped the same way:
    who found it is not what the Gate reads.
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

    A projection of the model, so there is no second structure to hold an order
    the drawing never showed (ADR-0019, ADR-0024). The description and the
    criteria still come off one model (ADR-0020).

    A disagreement goes whole: Plan is handed the part and nothing that argues
    about it. A gap nobody answered survives whole (ADR-0019). A datum goes
    with the feature it belonged to, and every live value measured from a dead
    one is re-measured from the datum that takes over. `because` stays: the
    one thing the user can overrule, read through `decided_for_you` (ADR-0022).
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

    Plan is handed the part and nothing that argues about it (ADR-0019).
    """
    standing = _one_to_an_address((model or {}).get("conflicts") or ())
    return dict(model, conflicts=standing) if standing else model


def _grouped_by_kind(conflicts):
    """
    *conflicts*, one list per member of `KINDS`, in `KINDS`' own order —
    disagreements, then gaps, then check hazards — each list keeping the
    order its entries arrived in.

    `_under_each_kind` and `grouped_conflicts` both need this grouping, so it
    is pulled out once.
    """
    return {kind: [conflict for conflict in conflicts if _which_kind_it_is(conflict) == kind]
            for kind in KINDS}


def grouped_conflicts(model) -> dict:
    """
    *model*, with `conflicts` reordered so every model-written entry stands
    before every machine-written one.

    At least one machine-written `A_CONFLICT` entry stands in 88% of recorded
    sessions — no kind-based order clears that bar.
    """
    conflicts = (model or {}).get("conflicts")
    if not conflicts:
        return model
    model_written = [conflict for conflict in conflicts if not _is_machine_written(conflict)]
    machine_written = [conflict for conflict in conflicts if _is_machine_written(conflict)]
    return dict(model, conflicts=model_written + machine_written)


def _is_machine_written(conflict):
    """Whether *conflict* was appended by a validator, read off `machine_written`."""
    return bool(isinstance(conflict, dict) and conflict.get("machine_written"))


def _worth_a_notice(model) -> bool:
    """Whether *model*'s conflicts hold at least one entry the model itself wrote.

    Recorded corpora put it near one model-written entry per description
    (1 of 29; 1 of 33) — not the rare case a naive reading would suggest.
    """
    return any(not _is_machine_written(conflict)
              for conflict in (model or {}).get("conflicts") or ())
