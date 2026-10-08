"""
The Acceptance entries a number alone settles, turned into checks: sizes, the axis and count of the feature that carries them, the envelope, and an edit's claim that nothing else moved.
An entry whose value says more than its number, or whose axis names no single global direction, is left to the model with the reason stated rather than guessed at.
"""
import re

from core.design.core import UNDER
from core.loop.describe import _A_SIZE

REASONS = {
    "no number": "its value holds no single number to measure",
    "several": "its value gives several figures, and which one is meant is a guess",
    "disagrees": "its figure is not the number Design holds for it",
    "mark": "its mark is not the one a diameter or a radius carries",
    "no size": "no size of the same feature to find its faces by",
    "axis": "its axis names no single global direction the datums settle",
    "not a side": "it is not named as a side of the bounding box",
    "second claim": "it claims more than one thing, and only the first is mechanical",
    "no base": "it speaks of an edit and there is no base part",
    "volume": "it asks a volume change another entry already set differently",
    "approximate": "its value is written as approximate, and a number alone does not settle it",
    "other": "it is not one of the kinds a number settles",
}

_FLAG = r"(?:\s*\((?:read|derived|assumed|suspected)\))?"
_SIZE_OF = {"diameter": "diameter", "inner_diameter": "diameter", "outer_diameter": "diameter", "radius": "radius"}
_MARKS = {"diameter": ("", "\u00d8", "\u2300"), "radius": ("", "R")}
_APPROXIMATE = re.compile(r"^(?:about|approximately) |^\u2248")
_LEAD = re.compile(r"^(?P<mark>[\u00d8\u2300R]?)\s*(?P<number>\d+(?:\.\d+)?)\s*(?:mm)?(?![\d.])")
_ENVELOPE_LEAD = re.compile(r"^(?P<number>\d+(?:\.\d+)?)\s*mm\b")
_MARKED = re.compile(r"(?P<mark>[\u00d8\u2300R])\s*(?P<number>\d+(?:\.\d+)?)")
_UNMARKED = re.compile(r"\d(?:\.\d+)?\s*mm\b")
_BARE_AXIS = re.compile(r"\b(?:parallel to|along) ([xyz])\b", re.IGNORECASE)
_EQUALS = re.compile(r"\b([xyz])\s*=\s*-?\d")
_COUNT = re.compile(r"^(?P<n>\d+)" + _FLAG + "$")
_VECTOR = re.compile(r"\(\s*(-?[01](?:\.0+)?)\s*,\s*(-?[01](?:\.0+)?)\s*,\s*(-?[01](?:\.0+)?)\s*\)")
_NAMED_AXIS = re.compile(r"\b([xyz])\s+(?:axis|direction)\b", re.IGNORECASE)
_PHRASE = re.compile(r"\b(parallel to|along|coaxial with|perpendicular to) the ([a-z][a-z' ]*?)(?= and\b| for\b|\s*[,;()]|\s*$)",
                     re.IGNORECASE)
_PLANE_OF = re.compile(r"\b([xyz])\s*=|[+-]([xyz])\b")
_SIDES = ("length", "width", "height", "depth")
_UNCHANGED = re.compile(r"^(?:unchanged|unmoved)(?: (?:by the edit|from the (?:imported STEP|base part)))?" + _FLAG + "$",
                        re.IGNORECASE)
_BOX_FIXED = re.compile(r"^(?:the )?bounding box (?:is )?unchanged$", re.IGNORECASE)
_VOLUME = re.compile(r"^(?:volume (?:about )?[\d.e+]+ mm3, )?(?:about |approximately )?(?P<mm3>\d+(?:\.\d+)?) mm3 "
                     r"(?P<way>less|smaller|more|larger|greater) than the base part$", re.IGNORECASE)


def _is_a_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _node(description, parts):
    """The Design value an entry address names, or an empty dict."""
    description = description or {}
    if parts[0] == "envelope" and len(parts) == 2:
        node = (description.get("envelope") or {}).get(parts[1])
    elif parts[0] == "features" and len(parts) == 3:
        features = description.get("features") or ()
        features = features.values() if isinstance(features, dict) else features
        owner = next((f for f in features if isinstance(f, dict) and f.get("id") == parts[1]), {})
        node = owner.get(parts[2])
    else:
        node = None
    return node if isinstance(node, dict) else {}


def _dimension_letters(description):
    letters = {}
    for datum in (description or {}).get("datums") or ():
        if isinstance(datum, dict) and " and " not in str(datum.get("fixes", "")):
            found = {a or b for a, b in _PLANE_OF.findall(str(datum.get("is", "")).lower())}
            letters.setdefault(_bare(datum.get("fixes", "")), set()).update(found)
    return {name: next(iter(found)) for name, found in letters.items() if len(found) == 1}


def _bare(name):
    return re.sub(r"^the ", "", str(name).strip().lower())


def _datum(description, name):
    return next((d for d in (description or {}).get("datums") or ()
                 if isinstance(d, dict) and _bare(d.get("name", "")) == name), None)


def _resolved(preposition, subject, description, dimensions, nested):
    subject = _bare(subject)
    if subject.startswith("document"):
        return None
    datum = _datum(description, subject)
    if preposition == "perpendicular to":
        return dimensions.get(_bare(datum.get("fixes", ""))) if datum else None
    if subject in dimensions:
        return dimensions[subject]
    if datum and not nested and subject.endswith("axis"):
        return _axis_letter(str(datum.get("is", "")), description, dimensions, True)
    return None


def _axis_letter(prose, description, dimensions, nested=False):
    """The one global axis the prose names outright, or through a dimension or datum the design fixes, or None where it names none, two, or a phrase it cannot resolve."""
    named = {m.group(1).lower() for pattern in (_NAMED_AXIS, _BARE_AXIS) for m in pattern.finditer(prose)}
    equal = {m.group(1).lower() for m in _EQUALS.finditer(prose)}
    if nested and len(equal) == 2 and not named:
        named.add((set("xyz") - equal).pop())
    vector = _VECTOR.search(prose)
    if vector:
        unit = [abs(float(v)) for v in vector.groups()]
        named.add("xyz"[unit.index(1.0)] if unit.count(1.0) == 1 and sum(unit) == 1.0 else "?")
    for phrase in _PHRASE.finditer(prose):
        if _bare(phrase.group(2)).startswith("document") or _NAMED_AXIS.search(phrase.group(2)):
            continue
        named.add(_resolved(phrase.group(1).lower(), phrase.group(2), description, dimensions, nested) or "?")
    return named.pop() if len(named) == 1 and "?" not in named else None


def _figures(value, node, kind):
    """([numbers], None) for a size, or (None, reason): Design's number when it holds one, else the number the value leads with, then each further marked number of the same kind."""
    marks = _MARKS[kind]
    if _APPROXIMATE.match(value):
        return None, "approximate"
    lead = _LEAD.match(value)
    rest = re.sub(r"\([^)]*\)", "", value[lead.end():] if lead else value)
    extra = list(_MARKED.finditer(rest))
    if _UNMARKED.search(_MARKED.sub("", rest)):
        return None, "several"
    if (lead and lead.group("mark") not in marks) or any(m.group("mark") not in marks[1:] for m in extra):
        return None, "mark"
    held = node.get("mm")
    figures = [float(m.group("number")) for m in extra]
    if _is_a_number(held):
        if lead and abs(float(lead.group("number")) - held) > 0.01:
            return None, "disagrees"
        return [float(held)] + figures, None
    if not lead:
        return None, "no number"
    return [float(lead.group("number"))] + figures, None


def _envelope_figure(value, node):
    held = node.get("mm")
    if _is_a_number(held):
        return float(held)
    lead = _ENVELOPE_LEAD.match(value)
    if lead:
        return float(lead.group("number"))
    found = _A_SIZE.findall(value)
    return float(found[0]) if len(found) == 1 else None


def _collateral_claim(value):
    """{"volume": mm3 or None, "box": bool} when every clause of the value is unchanged, a volume change or a fixed box, else None."""
    claim = {"volume": None, "box": False}
    for clause in (c.strip() for c in re.sub(_FLAG + "$", "", value).split(";")):
        volume = _VOLUME.match(clause)
        if volume:
            sign = -1.0 if volume.group("way").lower() in ("less", "smaller") else 1.0
            claim["volume"] = sign * float(volume.group("mm3"))
        elif _BOX_FIXED.match(clause):
            claim["box"] = True
        elif not _UNCHANGED.match(clause):
            return None
    return claim


class _Analysis:
    def __init__(self, acceptance, description, base):
        self.verdicts = {}
        self.envelope = []
        self.features = {}
        self.volume = None
        self.box_fixed = False
        self.held_by_no_collateral = False
        dimensions = _dimension_letters(description)
        later = []
        for entry in acceptance or ():
            address, _, value = str(entry).partition(": ")
            parts = [p.strip() for p in address.split(UNDER)]
            value = value.strip()
            leaf = parts[-1]
            if parts[0] == "envelope" and len(parts) == 2:
                self._envelope(entry, parts, value, description)
            elif parts[0] == "features" and len(parts) == 3 and leaf in _SIZE_OF:
                self._size(entry, parts, value, description)
            elif parts[0] == "features" and len(parts) == 3 and leaf in ("axis", "count"):
                later.append((entry, parts, value))
            else:
                self._collateral(entry, value, base)
        for entry, parts, value in later:
            self._carried(entry, parts, value, description, dimensions)

    def _envelope(self, entry, parts, value, description):
        figure = _envelope_figure(value, _node(description, parts))
        if parts[1].split(" ")[0].lower() not in _SIDES:
            self.verdicts[entry] = "not a side"
        elif _APPROXIMATE.match(value):
            self.verdicts[entry] = "approximate"
        elif figure is None:
            self.verdicts[entry] = "no number"
        else:
            self.verdicts[entry] = None
            self.envelope.append((entry, figure))

    def _size(self, entry, parts, value, description):
        kind = _SIZE_OF[parts[2]]
        figures, why = _figures(value, _node(description, parts), kind)
        self.verdicts[entry] = why
        for figure in figures or ():
            self.features.setdefault(parts[1], {"sizes": []})["sizes"].append(
                {"entry": entry, "kind": kind, "value": figure})

    def _carried(self, entry, parts, value, description, dimensions):
        feature = self.features.get(parts[1])
        if feature is None:
            self.verdicts[entry] = "no size"
        elif parts[2] == "axis":
            letter = _axis_letter(value, description, dimensions)
            self.verdicts[entry] = None if letter else "axis"
            if letter:
                feature["axis"] = {"entry": entry, "letter": letter}
        else:
            count = _COUNT.match(value)
            self.verdicts[entry] = None if count else "no number"
            if count:
                feature["count"] = {"entry": entry, "n": int(count.group("n"))}

    def _collateral(self, entry, value, base):
        claim = _collateral_claim(value)
        if claim is None:
            self.verdicts[entry] = "second claim" if re.search(r"unchanged|unmoved", value, re.IGNORECASE) else "other"
        elif not base:
            self.verdicts[entry] = "no base"
        elif claim["volume"] is not None and self.volume not in (None, claim["volume"]):
            self.verdicts[entry] = "volume"
        else:
            self.verdicts[entry] = None
            self.volume = claim["volume"] if claim["volume"] is not None else self.volume
            self.box_fixed = self.box_fixed or claim["box"]


def classified(acceptance, description, base):
    """{entry: None where generated code settles it, else the reason it stays with the model}, in the order given."""
    verdicts = _Analysis(acceptance, description, base).verdicts
    return {entry: REASONS[verdicts[entry]] if verdicts[entry] else None for entry in dict.fromkeys(acceptance or ())}


def covered_entries(acceptance, description, base):
    """Every entry generated code settles, in the order given."""
    verdicts = classified(acceptance, description, base)
    return [entry for entry in acceptance if verdicts.get(entry, "") is None]


def envelope_figures(acceptance, description=None):
    """The (entry, mm) pairs of every envelope entry that names a side of the bounding box with a number."""
    return _Analysis([e for e in acceptance or () if str(e).startswith("envelope" + UNDER)], description, None).envelope


def the_sizes_measured(acceptance, description):
    """The script that measures the plain sizes, axes and counts, or an empty string where there are none."""
    features = [f for f in _Analysis(acceptance, description, None).features.values()]
    return _SIZES_CHECK % features if features else ""


def the_collateral_measured(acceptance, base):
    """The `no_collateral` call of an edit, carrying the volume change and the fixed box the entries state outright; empty where there is no base part."""
    if not base:
        return ""
    analysis = _Analysis(acceptance, {}, base)
    arguments = []
    if analysis.volume is not None:
        arguments.append("volume_change=%r" % analysis.volume)
    if analysis.box_fixed:
        arguments.append("moved=()")
    return "no_collateral(%s)" % ", ".join(["doc"] + arguments)


_SIZES_CHECK = """
sizes_held, sizes_failed = _faces.measured(%r)
assert not sizes_failed, 'these did not hold: ' + '; '.join(sizes_failed)
"""
