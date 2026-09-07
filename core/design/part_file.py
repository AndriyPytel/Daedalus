"""
The part Design read off the sheet, as a file on disk.

Design produces the one thing every later phase depends on — what the part is —
and until this module it left nothing behind: the description went out as prose
in the Plan request and into the transcript as one event, and neither can be
opened, diffed or corrected. The reading is the part of the pipeline that is
wrong most often and it was the only part with no record of its own (FCC-108).

One reading is two files: the document Design distilled, which is the reading,
and beside it the whole of what Design gathered to arrive at it. Downstream
opens the first and nothing opens the second — the proof of a number is worth
nothing once the number is settled, and worth having for the person who asks
tomorrow where it came from.

A second reading of the same sheet is a second file beside the first, named by
when it was taken, so "what did the reading change"
is a diff rather than an afternoon with two transcripts. The name carries the
sheet, enough of its digest to tell two drawings of the same name apart and not
so much that the name stops being readable, and the time the reading was taken:
sortable, so a sheet's readings list in the order they were taken, and down to
the microsecond, so that two taken inside one second are still two names and the
sort still holds. A counter would not — `-1` sorts before `.yaml`. The file also
says what schema it is, so one written by an older addon can be told apart from
one this code understands.

Readings live beside the transcripts, in the addon's own directory, which is
where a user already goes to find a run, unless the caller says where. That
default is rebindable, because a suite that wrote its fixtures there would fill
the very directory a user reads.

What is in the file is the requirement. A user who opens it and changes an axis
has changed what the run must satisfy, and nothing here merges their edit with
what the provider said or warns them off it.
"""
import datetime
import hashlib
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - the addon installs PyYAML
    yaml = None

from core.design.design import SECTIONS, grouped_conflicts


class MissingPyYAML(RuntimeError):
    """
    PyYAML is not importable in this FreeCAD's Python, so there is no reading
    or writing a Design document at all — an error to surface, not the empty
    result an absent model or an absent file would legitimately return.
    """

    def __init__(self):
        super().__init__(
            "PyYAML is not installed in this FreeCAD's Python (%s). Restart "
            "FreeCAD so FreeCadCopilot's dependency check can install it, or "
            "run `pip install PyYAML` into that Python yourself." % sys.executable)


SCHEMA = "fcc.part/1"

WHEN = "%Y%m%d-%H%M%S-%f"

WITHOUT_A_SHEET = "the-intent"

_EVIDENCE = "-evidence.yaml"

_DIGEST = 8

WHERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "logs", "parts")

_PROVENANCE = "read_from"
_SCHEMA_KEY = "schema"
_ASKED_FOR = "what_was_asked_for"


def saved(model, source, into=None, read_from=None, asked_for=None) -> str:
    """
    *model* written as one reading of *source*. The path, or "" if there is
    nothing to write.

    A Design turn that described nothing wrote nothing down, and a file saying
    so is a file to mistake for a reading later. An empty *source* is not that
    case: a part described from the Intent alone was read off no sheet, and the
    name and the provenance say so rather than naming a drawing that does not
    exist.

    `asked_for` is the Intent the reading was taken under, and it is written
    first: a reading says what it was read off, and what it was read for is the
    other half of holding the run to it.
    """
    if not model:
        return ""
    if yaml is None:
        raise MissingPyYAML()

    into = into or WHERE
    os.makedirs(into, exist_ok=True)
    path = os.path.join(into, "%s-%s.yaml" % (_named(source),
                                              datetime.datetime.now().strftime(WHEN)))

    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(dict(_head(source, read_from, asked_for), **_ordered(model)), handle,
                       sort_keys=False, allow_unicode=True, default_flow_style=False)
    return path


def logged(model, reading) -> None:
    """
    The whole of what was read, as a log beside the reading distilled from it.

    Once the document is distilled the proof of a number is worth nothing to the
    run — it is planned, checked and built out of the reading — and it is worth
    something to whoever asks afterwards where a number came from. So it is kept
    where they are already looking, under the name of the reading it belongs to,
    and nothing downstream opens it.

    `conflicts` is written grouped by authorship (`grouped_conflicts`), every
    entry the model itself wrote ahead of every one a validator appended,
    rather than in the order the merge appended it (FCC-249; `kind` grouped
    it before this, and could not: `A_CONFLICT` is correct for a validator's
    own finding as much as for the model's, FCC-248).
    """
    if not model or not reading:
        return
    if yaml is None:
        raise MissingPyYAML()
    with open(evidence(reading), "w", encoding="utf-8") as handle:
        yaml.safe_dump(_ordered(grouped_conflicts(model)), handle, sort_keys=False,
                       allow_unicode=True, default_flow_style=False)


def evidence(reading) -> str:
    """Where that log is: the reading's own name, said to be the evidence."""
    return os.path.splitext(reading)[0] + _EVIDENCE if reading else ""


def read(path) -> dict:
    """The part in *path*, or {} where there is no readable file there."""
    return {key: value for key, value in _whole(path).items()
            if key not in (_SCHEMA_KEY, _PROVENANCE, _ASKED_FOR)}


def provenance(path) -> dict:
    """Which sheet this reading was taken from, and what it was taken with."""
    whole = _whole(path)
    return dict(whole.get(_PROVENANCE) or {},
                **{_SCHEMA_KEY: whole.get(_SCHEMA_KEY, "")})


def asked_for(path) -> str:
    """What the run that took this reading was asked to produce."""
    return str(_whole(path).get(_ASKED_FOR) or "")


def _whole(path):
    if not path or not os.path.isfile(path):
        return {}
    if yaml is None:
        raise MissingPyYAML()
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _named(source):
    """
    The stem of the file: the sheet and enough of its digest to tell two
    drawings of one name apart, or, with no sheet behind the reading, what it
    was read off instead.
    """
    if not source:
        return WITHOUT_A_SHEET
    return "%s-%s" % (os.path.splitext(os.path.basename(source))[0], _digest_of(source))


def _read_off(source):
    """
    What the reading says it was taken from.

    A reading with no sheet behind it carries neither name nor digest: an empty
    name and a digest of nothing are two claims about a drawing that does not
    exist, and the file is what the run is held to.
    """
    if not source:
        return {}
    return {"sheet": os.path.basename(source), "digest": _digest_of(source)}


def _head(source, read_from, asked_for):
    read = dict(_read_off(source),
                read_at=datetime.datetime.now().isoformat(timespec="seconds"))
    return {_ASKED_FOR: str(asked_for or ""),
            _SCHEMA_KEY: SCHEMA,
            _PROVENANCE: dict(read, **(read_from or {}))}


def _ordered(model):
    """The part's own sections in the order the Plan Gate reads them."""
    known = [key for key in SECTIONS if key in model]
    return {key: model[key] for key in known + [key for key in model
                                                if key not in known]}


def _digest_of(source):
    try:
        with open(source, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:_DIGEST]
    except OSError:
        return "00000000"
