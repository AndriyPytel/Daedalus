"""
The part Design read off the sheet, as a file on disk: until this module
nothing recorded what the part is, it went out as prose in a Plan request
and as one transcript event, neither openable, diffable or correctable.
One reading is two files, the document Design distilled and beside it what
Design gathered to arrive at it, kept for whoever asks tomorrow where a
number came from; a second reading is a second file beside the first,
named by the sheet's digest and the time taken, sortable to the
microsecond. Readings live beside the transcripts, in the addon's own
directory unless the caller says otherwise, rebindable so a test suite
does not fill the directory a user reads, and what is in the file is the
requirement -- a user's edit changes what the run must satisfy, unmerged
with what the provider said.
"""
import datetime
import hashlib
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - the addon installs PyYAML
    yaml = None

from core.design.core import SECTIONS
from core.design.report import grouped_conflicts
from core.logs_root import LOGS


class MissingPyYAML(RuntimeError):
    """
    PyYAML is not importable in this FreeCAD's Python, so there is no reading
    or writing a Design document at all — an error to surface, not the empty
    result an absent model or an absent file would legitimately return.
    """

    def __init__(self):
        super().__init__(
            "PyYAML is not installed in this FreeCAD's Python (%s). Restart "
            "FreeCAD so Daedalus's dependency check can install it, or "
            "run `pip install PyYAML` into that Python yourself." % sys.executable)


SCHEMA = "fcc.part/1"

WHEN = "%Y%m%d-%H%M%S-%f"

WITHOUT_A_SHEET = "the-intent"

_EVIDENCE = "-evidence.yaml"

_DIGEST = 8

WHERE = os.path.join(LOGS, "parts")

_PROVENANCE = "read_from"
_SCHEMA_KEY = "schema"
_ASKED_FOR = "what_was_asked_for"


def saved(model, source, into=None, read_from=None, asked_for=None) -> str:
    """
    *model* written as one reading of *source*, returning the path, or ""
    if there is nothing to write. A Design turn that described nothing
    writes nothing, rather than a file to mistake for a reading later; an
    empty *source* means read off no sheet, not off a drawing that does
    not exist. `asked_for` (the Intent read under) is written first, the
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
    """The whole of what was read, as a log beside the reading distilled from it."""
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
