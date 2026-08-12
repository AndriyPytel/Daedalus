"""
The part Design read off the sheet, as a file on disk.

Design produces the one thing every later phase depends on — what the part is —
and until this module it left nothing behind: the description went out as prose
in the Plan request and into the transcript as one event, and neither can be
opened, diffed or corrected. The reading is the part of the pipeline that is
wrong most often and it was the only part with no record of its own (FCC-108).

One reading is one file. A second reading of the same sheet is a second file
beside the first, named by when it was taken, so "what did the reading change"
is a diff rather than an afternoon with two transcripts.

What is in the file is the requirement. A user who opens it and changes an axis
has changed what the run must satisfy, and nothing here merges their edit with
what the provider said or warns them off it.
"""
import datetime
import hashlib
import os

try:
    import yaml
except ImportError:  # pragma: no cover - the addon installs PyYAML
    yaml = None

from core.design import SECTIONS

# What the file claims to be, so a file written by an older addon can be told
# apart from one this code understands.
SCHEMA = "fcc.part/1"

# How the reading's time is written into the name. Sortable, so the latest
# reading of a sheet is the last one by name — and down to the microsecond, so
# that two readings taken inside one second are still two names and the sort
# still holds. A counter would not: `-1` sorts before `.yaml`.
WHEN = "%Y%m%d-%H%M%S-%f"

# Enough of the sheet's digest to tell two drawings of the same name apart, and
# not so much that the name stops being readable.
_DIGEST = 8

# Where readings live when the caller does not say: beside the transcripts, in
# the addon's own directory, which is where a user already goes to find a run.
# Rebindable, because a suite that wrote its fixtures here would fill the very
# directory a user reads.
WHERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "logs", "parts")

# The two keys the file carries that are not the part.
_PROVENANCE = "read_from"
_SCHEMA = "schema"


def saved(model, source, into=None, read_from=None) -> str:
    """
    *model* written as one reading of *source*. The path, or "" if there is
    nothing to write.

    A run with no drawing describes no part, and a file saying so is a file to
    mistake for a reading later.
    """
    if not model or yaml is None:
        return ""

    into = into or WHERE
    os.makedirs(into, exist_ok=True)
    path = os.path.join(into, "%s-%s-%s.yaml" % (
        os.path.splitext(os.path.basename(source))[0], _digest_of(source),
        datetime.datetime.now().strftime(WHEN)))

    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(_head(source, read_from), handle, sort_keys=False,
                       allow_unicode=True, default_flow_style=False)
        yaml.safe_dump(_ordered(model), handle, sort_keys=False,
                       allow_unicode=True, default_flow_style=False)
    return path


def read(path) -> dict:
    """The part in *path*, or {} where there is no readable file there."""
    return {key: value for key, value in _whole(path).items()
            if key not in (_SCHEMA, _PROVENANCE)}


def provenance(path) -> dict:
    """Which sheet this reading was taken from, and what it was taken with."""
    whole = _whole(path)
    return dict(whole.get(_PROVENANCE) or {}, **{_SCHEMA: whole.get(_SCHEMA, "")})


def latest(source, into=None) -> str:
    """The most recent reading of *source*, or None where there is none."""
    into = into or WHERE
    if not os.path.isdir(into):
        return None
    stem = "%s-%s-" % (os.path.splitext(os.path.basename(source))[0],
                       _digest_of(source))
    readings = sorted(name for name in os.listdir(into)
                      if name.startswith(stem) and name.endswith(".yaml"))
    return os.path.join(into, readings[-1]) if readings else None


def _whole(path):
    if not path or not os.path.isfile(path) or yaml is None:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _head(source, read_from):
    return {_SCHEMA: SCHEMA,
            _PROVENANCE: dict({"sheet": os.path.basename(source),
                               "digest": _digest_of(source),
                               "read_at": datetime.datetime.now().isoformat(
                                   timespec="seconds")},
                              **(read_from or {}))}


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
