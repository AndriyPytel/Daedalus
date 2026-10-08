"""
What a check measures a finished part with, in the kernel: the faces of its largest solid sorted by surface type once per check call, the generated size, axis and count check, and the `face_at`, `distance` and `cylinder` helpers.
Linear judgements use `LINEAR` and angular ones 0.5 degree, as the assertion prompt says; nothing here calls `distToShape` or measures a face's volume, so a body of two thousand faces costs one pass over its faces.
"""
import math
import re

LINEAR = 0.5
_PARALLEL = math.cos(math.radians(0.5))
_PERPENDICULAR = math.sin(math.radians(0.5))
_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
_EXTREME = re.compile(r"^([+-])([xyz])$")
_PLANE_AT = re.compile(r"\b([xyz])\s*=\s*(-?\d+(?:\.\d+)?)")


def _vector(v):
    return (v.x, v.y, v.z)


def _dot(a, b):
    return sum(p * q for p, q in zip(a, b))


def _along(axis, letter):
    return abs(_dot(axis, _AXES[letter])) >= _PARALLEL


class Faces:
    """
    The faces of the largest solid outside a Body, found once and sorted by surface type on first use; `measure` is the check call's `RememberedMeasures`.
    It must not outlive an edit to the Document.
    """

    def __init__(self, doc, measure):
        self._doc = doc
        self._measure = measure
        self._shape = None
        self._by_type = None

    @property
    def shape(self):
        if self._shape is None:
            solids = [o for o in self._doc.Objects if getattr(o, "Shape", None) is not None
                      and not o.Shape.isNull() and o.Shape.Solids
                      and getattr(o.getParentGeoFeatureGroup(), "TypeId", "") != "PartDesign::Body"]
            assert solids, "the Document has no solid to measure"
            self._shape = max(solids, key=lambda o: self._measure.volume(o.Shape)).Shape
        return self._shape

    def _of(self, kind):
        if self._by_type is None:
            self._by_type = {}
            for face in self.shape.Faces:
                surface = face.Surface
                self._by_type.setdefault(surface.__class__.__name__, []).append((face, surface))
        return self._by_type.get(kind, ())

    def _rounds(self):
        rounds = [("Cylinder", surface.Radius, _vector(surface.Axis)) for _, surface in self._of("Cylinder")]
        rounds += [("Torus", surface.MinorRadius, _vector(surface.Axis)) for _, surface in self._of("Torus")]
        rounds += [("Sphere", surface.Radius, None) for _, surface in self._of("Sphere")]
        return rounds

    def measured(self, features):
        """
        Each feature is `{"sizes": [{"entry", "kind", "value"}], "axis": {"entry", "letter"} or None, "count": {"entry", "n"} or None}`; returns (held, failed) entry lists.
        A diameter must be a cylinder, a radius a cylinder, a torus' minor radius or a sphere; an axis holds when every size has a face along it, and a count when some size has at least that many faces.
        """
        rounds = self._rounds()
        held, failed = [], []
        for feature in features:
            axis, count = feature.get("axis"), feature.get("count")
            sized, off_axis, most = False, False, 0
            for size in feature["sizes"]:
                diameter = size["kind"] == "diameter"
                want, tol = (size["value"] / 2, LINEAR / 2) if diameter else (size["value"], LINEAR)
                kinds = ("Cylinder",) if diameter else ("Cylinder", "Torus", "Sphere")
                found = [r for r in rounds if r[0] in kinds and abs(r[1] - want) <= tol]
                if not found:
                    failed.append(size["entry"])
                    continue
                held.append(size["entry"])
                sized = True
                if axis:
                    found = [r for r in found if r[2] is not None and _along(r[2], axis["letter"])]
                    off_axis = off_axis or not found
                most = max(most, len(found))
            if sized and axis:
                (failed if off_axis else held).append(axis["entry"])
            if sized and count:
                (failed if most < count["n"] else held).append(count["entry"])
        return list(dict.fromkeys(held)), list(dict.fromkeys(failed))

    def _where(self, design, datum):
        extreme = _EXTREME.match(datum)
        if extreme:
            box = self._measure.box(self.shape)
            low, high = {"x": (box.XMin, box.XMax), "y": (box.YMin, box.YMax), "z": (box.ZMin, box.ZMax)}[extreme.group(2)]
            return [(extreme.group(2), high if extreme.group(1) == "+" else low, 1e-3)]
        datums = [d for d in (design or {}).get("datums") or () if isinstance(d, dict)]
        named = [d for d in datums if str(d.get("name", "")).lower() == datum.lower()]
        if not named:
            raise LookupError("face_at knows no datum %r; the design names %s, or give '+x', '-y', '+z' for the part's extreme"
                              % (datum, [d.get("name") for d in datums]))
        planes = sorted({(m.group(1), float(m.group(2))) for m in _PLANE_AT.finditer(str(named[0].get("is", "")))})
        if len({letter for letter, _ in planes}) > 1:
            raise LookupError("face_at: %r is an axis, the meeting of %s, not a face; find its cylinder with cylinder(d, axis) and use distance(a, b)"
                              % (datum, ", ".join("%s = %s" % plane for plane in planes)))
        if not planes:
            raise LookupError("face_at cannot tell which plane %r is: its description names none" % datum)
        return [(letter, at, LINEAR) for letter, at in planes]

    def face_at(self, design, datum):
        """
        The largest planar face lying on a datum plane: *datum* is a name from `design["datums"]` or '+x', '-x', '+y', '-y', '+z', '-z' for the part's extreme.
        A datum that names several planes along one axis resolves to the one that has a face and raises LookupError saying 'ambiguous' when more than one does, or when it names an axis or no plane.
        """
        found = {}
        for letter, at, tol in self._where(design, str(datum)):
            candidates = [(face.Area, face) for face, surface in self._of("Plane")
                          if _along(_vector(surface.Axis), letter) and abs(_dot(_AXES[letter], _vector(surface.Position)) - at) <= tol]
            if candidates:
                found[(letter, at)] = max(candidates, key=lambda pair: pair[0])[1]
        if not found:
            raise LookupError("face_at found no planar face for %r" % (datum,))
        if len(found) > 1:
            raise LookupError("face_at: %r is ambiguous, planar faces lie on %s; pick one with cylinder or distance"
                              % (datum, " and ".join("%s = %s" % plane for plane in sorted(found))))
        return next(iter(found.values()))

    def cylinder(self, diameter, axis=None, expect=1, tol=LINEAR):
        """
        The cylindrical face of this *diameter* (within *tol*) whose axis runs along global *axis* ('x', 'y' or 'z') when given; a list of *expect* faces where *expect* is not 1.
        Raises LookupError naming every cylinder the part has when the count is not *expect*.
        """
        every = self._of("Cylinder")
        found = [face for face, surface in every
                 if abs(surface.Radius * 2 - diameter) <= tol and (axis is None or _along(_vector(surface.Axis), axis))]
        if len(found) != expect:
            raise LookupError("cylinder wanted %d faces of diameter %s along %s, found %d; the part has cylinders %s"
                              % (expect, diameter, axis, len(found),
                                 sorted({(round(s.Radius * 2, 3), tuple(round(c, 3) for c in _vector(s.Axis))) for _, s in every})))
        return found[0] if expect == 1 else found


def _reduced(face):
    surface = face.Surface
    kind = surface.__class__.__name__
    if kind == "Plane":
        return "plane", _vector(surface.Position), _vector(surface.Axis)
    if kind == "Cylinder":
        return "axis", _vector(surface.Center), _vector(surface.Axis)
    raise ValueError("distance wants planar or cylindrical faces, got a %s" % kind)


def distance(a, b):
    """
    The distance between two faces: plane to parallel plane, a cylinder's axis to a parallel plane, or one cylinder's axis to another's.
    Raises ValueError where they are not parallel, or are neither planar nor cylindrical.
    """
    (kind_a, point_a, dir_a), (kind_b, point_b, dir_b) = _reduced(a), _reduced(b)
    if kind_a == "axis" and kind_b == "plane":
        (kind_a, point_a, dir_a), (kind_b, point_b, dir_b) = (kind_b, point_b, dir_b), (kind_a, point_a, dir_a)
    between = tuple(p - q for p, q in zip(point_b, point_a))
    if kind_a == "plane" and kind_b == "plane":
        if abs(_dot(dir_a, dir_b)) < _PARALLEL:
            raise ValueError("distance: the two planes are not parallel")
        return abs(_dot(dir_a, between))
    if kind_a == "plane":
        if abs(_dot(dir_a, dir_b)) > _PERPENDICULAR:
            raise ValueError("distance: the cylinder's axis is not parallel to the plane")
        return abs(_dot(dir_a, between))
    if abs(_dot(dir_a, dir_b)) < _PARALLEL:
        raise ValueError("distance: the two axes are not parallel")
    along = _dot(between, dir_a)
    return math.sqrt(max(0.0, _dot(between, between) - along * along))
