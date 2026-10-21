"""The guard an edit must pass before it replaces the base part: one valid solid, only the change asked for, no collateral."""

_SIDES = ("-x", "-y", "-z", "+x", "+y", "+z")
_BOX_MM = 0.5
_ASKED_RELATIVE = 0.05
_ASKED_FLOOR = 0.001
_UNASKED_LOSS = 0.5
_UNASKED_GAIN = 2.0


ORIGINAL_BASE = "ImportedBase"


def original_base(doc, body):
    """The imported part an edit started from: the object labelled `ORIGINAL_BASE` where there is one, else the Body's own BaseFeature, so a Step that swaps the Body's BaseFeature for a code-built solid is still measured against what was imported."""
    return next(iter(doc.getObjectsByLabel(ORIGINAL_BASE)), None) or body.BaseFeature


class Measures:
    """
    Reads a shape's volume, validity and exact bounding box straight off the shape.
    """

    def volume(self, shape):
        return shape.Volume

    def valid(self, shape):
        return bool(shape.isValid())

    def box(self, shape):
        return shape.optimalBoundingBox(False)


class RememberedMeasures(Measures):
    """
    `Measures` that answer each question once per shape, for one check call.
    The shapes are held and matched with `isSame`, so it must not outlive an edit to them; the box is found by measuring exactly only the faces whose loose box reaches past the box found so far.
    """

    def __init__(self):
        self._known = []

    def _of(self, shape):
        for held, known in self._known:
            if held.isSame(shape):
                return known
        known = {}
        self._known.append((shape, known))
        return known

    def _once(self, shape, key, compute):
        known = self._of(shape)
        if key not in known:
            known[key] = compute(shape)
        return known[key]

    def volume(self, shape):
        return self._once(shape, "volume", super().volume)

    def valid(self, shape):
        return self._once(shape, "valid", super().valid)

    def box(self, shape):
        return self._once(shape, "box", _exact_box)


def _exact_box(shape):
    """The box `shape.optimalBoundingBox(False)` returns, as a FreeCAD BoundBox, found from the few faces that reach it."""
    import FreeCAD
    import numpy as np

    faces = shape.Faces
    loose = np.array([(b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)
                      for b in (face.BoundBox for face in shape.copy().Faces)])
    found = None
    measured_faces = np.zeros(len(faces), dtype=bool)
    while True:
        if found is None:
            index = int(np.argmax(loose[:, 3] - loose[:, 0]))
        else:
            overhang = (np.maximum(found[:3] - loose[:, :3], 0).sum(1)
                        + np.maximum(loose[:, 3:] - found[3:], 0).sum(1))
            overhang[measured_faces] = 0
            index = int(np.argmax(overhang))
            if overhang[index] <= 0:
                break
        box = faces[index].optimalBoundingBox(False)
        measured_faces[index] = True
        exact = np.array((box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax))
        found = exact if found is None else np.r_[np.minimum(found[:3], exact[:3]),
                                                  np.maximum(found[3:], exact[3:])]
    return FreeCAD.BoundBox(*found)


def measured(shape, measures=Measures()):
    """
    The four numbers the guard compares, read off one Part shape: solid count, validity, volume and the exact bounding box.
    The box ignores tessellation padding, the same as Assertion's envelope check.
    """
    box = measures.box(shape)
    return {"solids": len(shape.Solids), "valid": measures.valid(shape), "volume": measures.volume(shape),
            "bbox": (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)}


def collateral(base, built, volume_change=None, moved=None):
    """
    Why *built* is not the *base* part edited as asked, one sentence per reason; empty where it is.
    *volume_change* is the signed mm3 the edit should add; within 5% of it plus 0.1% of the base volume holds, and where it is not given the volume may only lose half or gain a whole base.
    *moved* names the bounding box sides ('+z', '-x', ...) the edit may move, beyond 0.5 mm; None skips the box check.
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
    for side, was, now in zip(_SIDES, base["bbox"], built["bbox"]) if moved is not None else ():
        if abs(now - was) > _BOX_MM and side not in moved:
            reasons.append("the bounding box moved on %s by %.2f mm and the edit does not ask for it"
                           % (side, now - was))
    return reasons
