"""The guard an edit must pass before it replaces the base part: one valid solid, only the change asked for, no collateral."""

_SIDES = ("-x", "-y", "-z", "+x", "+y", "+z")
_BOX_MM = 0.5
_ASKED_RELATIVE = 0.05
_ASKED_FLOOR = 0.001
_UNASKED_LOSS = 0.5
_UNASKED_GAIN = 2.0


def measured(shape):
    """
    The four numbers the guard compares, read off one Part shape: solid count, validity, volume and the exact bounding box.
    The box ignores tessellation padding, the same as Assertion's envelope check.
    """
    box = shape.optimalBoundingBox(False)
    return {"solids": len(shape.Solids), "valid": bool(shape.isValid()), "volume": shape.Volume,
            "bbox": (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)}


def collateral(base, built, volume_change=None, moved=()):
    """
    Why *built* is not the *base* part edited as asked, one sentence per reason; empty where it is.
    *volume_change* is the signed mm3 the edit should add; within 5% of it plus 0.1% of the base volume holds, and where it is not given the volume may only lose half or gain a whole base.
    *moved* names the bounding box sides ('+z', '-x', ...) the edit may move, beyond 0.5 mm.
    """
    reasons = []
    if built["solids"] != 1:
        reasons.append("the part has %d solids and an edit leaves exactly one" % built["solids"])
    if not built["valid"]:
        reasons.append("the part is not a valid solid")
    delta = built["volume"] - base["volume"]
    if volume_change is not None:
        slack = _ASKED_RELATIVE * abs(volume_change) + _ASKED_FLOOR * base["volume"]
        if abs(delta - volume_change) > slack:
            reasons.append("the volume changed by %.1f mm3 where the edit asks for %.1f mm3 (within %.1f)"
                           % (delta, volume_change, slack))
    elif not _UNASKED_LOSS * base["volume"] <= built["volume"] <= _UNASKED_GAIN * base["volume"]:
        reasons.append("the volume went from %.1f to %.1f mm3, more than an edit moves"
                       % (base["volume"], built["volume"]))
    for side, was, now in zip(_SIDES, base["bbox"], built["bbox"]):
        if abs(now - was) > _BOX_MM and side not in moved:
            reasons.append("the bounding box moved on %s by %.2f mm and the edit does not ask for it"
                           % (side, now - was))
    return reasons
