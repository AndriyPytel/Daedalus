"""Vocabulary and primitive accessors every design.py module reads the model through -- addresses, conflict entries, and the closed word lists a feature or the sheet is checked against."""


SECTIONS = ("printed_on_the_sheet", "datums", "envelope", "features", "conflicts",
            "not_defined")


UNDER = " → "


_HEADINGS = {"printed_on_the_sheet": "Printed on the sheet", "datums": "Datums",
             "envelope": "Envelope", "features": "Features",
             "conflicts": "In conflict", "not_defined": "Not defined"}


_IDENTITY = ("id", "name")


_HEADED_BY = _IDENTITY + ("about", "what")


_NOT_THE_PART = ("conflicts", "not_defined", "printed_on_the_sheet")


A_CONFLICT = "conflict"


A_GAP = "gap"


A_CHECK_HAZARD = "check hazard"


KINDS = (A_CONFLICT, A_GAP, A_CHECK_HAZARD)


HEADED = {A_CONFLICT: "Two readings that cannot both be true",
          A_GAP: "A value nothing on the part supplies",
          A_CHECK_HAZARD: "A check that could not be run"}


def _conflict(about, stands, was_also_said, kind=A_CONFLICT):
    """The shared conflict-finding dict."""
    return {"about": about, "stands": stands, "was_also_said": was_also_said,
            "kind": kind, "machine_written": True}


FEATURE_KIND = ("hole", "slot", "pocket", "cutout", "pad", "fillet", "chamfer",
                "boss", "rib", "thread", "counterbore", "countersink", "groove",
                "notch", "tab", "lug", "ear", "flange", "step", "shoulder",
                "shell", "draft")


PLACE = ("left", "right", "front", "rear", "top", "bottom", "upper", "lower",
         "centre", "inner", "outer")


def canonical_place(tokens):
    """*tokens* (a feature `id`'s place words) ordered the way `PLACE` lists
    them, so two readings of the same feature's place produce the same
    order whichever order the model wrote them in."""
    return tuple(sorted(tokens, key=PLACE.index))


CLOSED_KIND_VOCABULARY = "closed"


_BANNED_KIND_SYNONYM = {"bore": "hole", "round": "fillet", "rounded": "fillet",
                         "rounds": "fillet", "opening": "cutout", "window": "cutout",
                         "split": "slot", "slit": "slot", "saw cut": "slot",
                         "keyway": "slot", "recess": "counterbore",
                         "recesses": "counterbore"}


_REGION_WORD = ("arm", "ring", "web", "band", "collar", "profile", "plate",
                "body", "outline", "contour")


def entry_at(model, address):
    """The record sitting at *address* on this model, whole, or None."""
    node = model
    for step in str(address).split(UNDER):
        node = _the_entry(node, step)
        if node is None:
            return None
    return node


def said_at(model, address):
    """What sits at *address* on this model, as a value, or None."""
    node = entry_at(model, address)
    return node.get("value") if isinstance(node, dict) and _a_value(node) else node


def _the_entry(node, step):
    """One step of an address, through a mapping or a list of identified things."""
    if isinstance(node, dict):
        return node.get(step)
    if isinstance(node, list):
        return next((entry for entry in node if _named(entry, _IDENTITY) == step), None)
    return None


_A_DIRECTION = ("length", "width", "height", "depth", "thickness")


def _features(model):
    """
    The features the part has, which is not the features the description holds:
    a feature the later page replaced is judged by nothing.
    """
    return [feature for feature in (model or {}).get("features") or ()
            if isinstance(feature, dict) and not _superseded(feature)]


def _superseded(node):
    """Whether a later page replaced this, which is what makes it not the part."""
    return isinstance(node, dict) and _filled(node.get("superseded_by"))


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


def _which_kind_it_is(conflict):
    """
    Which of `KINDS` an entry is, and `A_CONFLICT` for one that does not say.

    Read off the entry, never its prose: two readings that cannot both be
    true is what this section is for, so an entry nobody typed is that
    one -- a value nobody supplied or an outline nobody could ask about.
    """
    said = _named(conflict, ("kind",))
    return said if said in KINDS else A_CONFLICT


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
