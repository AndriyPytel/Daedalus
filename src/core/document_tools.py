"""
The FreeCAD-backed Tools, the only code besides Apply that touches the Document: API knowledge comes from introspecting the running FreeCAD -- property lists, supported types, real dependencies -- never a documentation corpus that mixes versions.
`build_registry` registers them over whichever Document is in focus, with `apply_step` when it is handed an Apply.
Document operations go through a *dispatch* so the Qt main thread does the touching.
"""
import math

from core.kernel import copy_of
from core.tools import ToolError, ToolRegistry


def _direct(func, timeout=None):
    """The identity dispatch: run *func* right here, no Qt, no marshalling."""
    return func()


_SNAPSHOT_LIMIT = 200
_MATERIALS_LIMIT = 40

_GEOMETRY_MODULES = ("Part", "PartDesign", "Sketcher")


def _load_geometry_modules():
    """
    Import the geometry modules, so the Document will admit their types.

    `supportedTypes()` only reports types whose module has been imported —
    unimported, a session that never opened PartDesign is told PartDesign::Pad
    does not exist, a confident, wrong absence.
    """
    for module in _GEOMETRY_MODULES:
        try:
            __import__(module)
        except ImportError:
            pass  # except-pass-allowed: an unavailable workbench is not installed, not an error


def _selection(dispatch=_direct):
    """
    What the user has selected right now, sub-element names included -- the
    one sanctioned source of an `Edge12`; headless has no GUI and therefore
    no selection. Read through *dispatch*, since the worker thread that
    calls this cannot touch Qt directly.
    """
    try:
        import FreeCADGui

        selection = dispatch(FreeCADGui.Selection.getSelectionEx)
    except (ImportError, AttributeError):
        return []

    return [
        {"object": sel.ObjectName, "elements": list(sel.SubElementNames)}
        for sel in selection
    ]


def active_document():
    """Whichever Document is in focus right now, or None with nothing open."""
    import FreeCAD
    return FreeCAD.ActiveDocument


def _active_body(doc, dispatch=_direct):
    """
    The Body the user is working in, or the only one there is.

    `ActiveDocument.ActiveView` is the Gui's state and is read through
    *dispatch* for the reason `_selection` is.
    """
    try:
        import FreeCADGui

        body = dispatch(
            lambda: FreeCADGui.ActiveDocument.ActiveView.getActiveObject("pdbody"))
    except (ImportError, AttributeError):
        body = None
    if body is not None:
        return body.Name
    bodies = [o.Name for o in doc.Objects if o.isDerivedFrom("PartDesign::Body")]
    return bodies[0] if len(bodies) == 1 else None


def _errors(obj):
    return [s for s in (obj.State or []) if s in ("Invalid", "Error", "Touched")]


_SCAFFOLDING = 1e6


def _measurable(obj):
    """
    The object's bounding box where it is modelled geometry, else None.

    A datum plane is a real Shape with a real face and it is 1e100 across, so
    everything that measures the part has to refuse the same things. Anything
    bigger than a kilometre across is scaffolding rather than a part.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.Faces:
        return None
    bound = shape.BoundBox
    if not bound.isValid() or bound.DiagonalLength > _SCAFFOLDING:
        return None
    return bound


def export_step(document, path, only=None):
    """
    Write *document*'s modelled geometry to *path* as STEP, and hand the
    path back. Only the top-level objects `_measurable` calls real geometry go in --
    a Sketch or Pad an unhidden Body already fused into its own Shape, or
    a Body's own Origin scaffolding, would double the geometry or fail
    `Part.export` outright. *only* names the one object that goes out alone, as an edit's Body does,
    and a shape the mesh gate rejects is repaired first unless that moves the volume over 0.5%.
    """
    import warnings

    import Part

    from core.manifold import repair_fc, rewrite_step

    objects = [obj for obj in document.Objects
               if not obj.InList and _measurable(obj) is not None
               and (only is None or obj.Name == only)]
    shapes = []
    for obj in objects:
        fixed, note = repair_fc(obj.Shape)
        if note and fixed.isSame(obj.Shape):
            warnings.warn("%s: %s" % (obj.Name, note))
        shapes.append(fixed)
    if all(fixed is obj.Shape or fixed.isSame(obj.Shape) for fixed, obj in zip(shapes, objects)):
        Part.export(objects, path)
    else:
        Part.Compound(shapes).exportStep(path)
    note = rewrite_step(Part.Compound(shapes), path)
    if note:
        warnings.warn(note)
    return path


def has_solid(document):
    """Whether *document* holds modelled geometry with a solid, the only thing worth exporting."""
    return any(not obj.InList and _measurable(obj) is not None and obj.Shape.Solids
               for obj in document.Objects)


def _overall_size(doc):
    """
    How big the modelled part is, `[x, y, z]` in mm, or None if nothing is.

    A Body's Origin carries three planes and three axes, each 1e100 across,
    so a literal union of every bounding box makes any part 2e+100 mm wide;
    `_measurable` is what keeps this to the modelled geometry.
    """
    box = None
    for obj in doc.Objects:
        bound = _measurable(obj)
        if bound is None:
            continue
        box = bound if box is None else box.united(bound)
    if box is None:
        return None
    return [round(box.XLength, 6), round(box.YLength, 6), round(box.ZLength, 6)]


_ANGULAR = ("Angle", "AngleViaPoint")
_DIMENSIONAL = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter")


def description(doc):
    """
    What the Document now holds, in the two layers Review can judge by. The
    feature tree with its parameters is trustworthy because Lint forces it
    to be genuine PartDesign; the inventory read off `Shape` is what it came
    out as, the only one that reports a hole nobody thought to ask about,
    since the Acceptance list is written before the part exists.
    """
    return {"features": [_described(obj) for obj in doc.Objects],
            "geometry": _inventory(doc)}


def _described(obj):
    described = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    parameters = _parameters(obj)
    if parameters:
        described["parameters"] = parameters
    constraints = _constraints(obj)
    if constraints:
        described["constraints"] = constraints
    return described


def _parameters(obj):
    """
    The named values of one feature, as the property editor would show them.

    Hidden/ReadOnly is FreeCAD's own "is this editable here", not a
    relevance filter — an unthreaded Hole still reports its thread
    properties. A link, shape or placement is not a value to judge by.
    """
    parameters = {}
    for prop in getattr(obj, "PropertiesList", ()):
        if prop == "Label" or set(obj.getEditorMode(prop)) & {"Hidden", "ReadOnly"}:
            continue
        try:
            value = _named_value(getattr(obj, prop))
        except Exception:
            continue
        if value is not None:
            parameters[prop] = value
    return parameters


def _named_value(value):
    """
    The property's value where it is a number someone could name, else None.

    A Quantity is the pair of a Value and a Unit, and reads as "10.0 mm".
    """
    if isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "Value") and hasattr(value, "Unit"):
        return str(value)
    return None


def _constraints(obj):
    """
    A sketch's driving, named constraints — and deliberately not its geometry.

    Raw geometry is a defect formally present and practically invisible: a
    LineSegment's coordinates are the most tokens for the least judgement. A
    named constraint is the same dimension, said once, by someone who chose it.
    """
    return {constraint.Name: _dimension(constraint)
            for constraint in getattr(obj, "Constraints", ())
            if constraint.Name and constraint.Driving}


def _dimension(constraint):
    """
    One constraint as a person reads it.

    A constraint holds an angle in radians, and every place a person reads one
    it is degrees.
    """
    if constraint.Type in _ANGULAR:
        return "%.6g deg" % math.degrees(constraint.Value)
    if constraint.Type in _DIMENSIONAL:
        return "%.6g mm" % constraint.Value
    return constraint.Type


def _inventory(doc):
    """
    The geometry as it came out: material total, one entry per surface
    family. Grouping by kind/diameter/axis keeps identical holes one entry;
    only top-level objects count, so this is the part as it stands.

    A family counts distinct axis seats — a bore's two faces share one, flats share none — and volume tells apart a cut Pocket from one that missed.
    """
    solids, volume, seats = 0, 0.0, {}
    for obj in doc.Objects:
        if getattr(obj, "InList", None) or _measurable(obj) is None:
            continue
        shape = obj.Shape
        solids += len(shape.Solids)
        volume += shape.Volume
        for face in shape.Faces:
            family, seat = _family(face.Surface)
            seats.setdefault(family, []).append(seat)

    surfaces = []
    for family in sorted(seats, key=str):
        kind, diameter, axis = family
        seated = seats[family]
        entry = {"kind": kind}
        if diameter is not None:
            entry["diameter_mm"] = diameter
            entry["axis"] = list(axis)
        entry["count"] = len(set(seated)) if all(seated) else len(seated)
        surfaces.append(entry)
    return {"solids": solids, "volume_mm3": round(volume, 6), "surfaces": surfaces}


_INVENTORY_LINES = 40


def base_inventory(doc):
    """
    The part a Body starts from, in words: bounding box, then one line per face address, sections cut past `_INVENTORY_LINES`. None where no Body has a BaseFeature. Two ceilings: a B-spline address is approximate (family, bounding box and centroid rounded to 0.1 mm), and a plane address is the whole plane, so removing one of several faces on one plane leaves the address standing.
    """
    base = _base_feature(doc)
    if base is None:
        return None
    shape = base.Shape
    bound = shape.optimalBoundingBox(False)
    addresses = _addresses_of(shape)
    planes = {a: v for a, v in addresses.items() if a.startswith("plane ")}
    cylinders = {a: v for a, v in addresses.items() if a.startswith(("hole ", "boss "))}
    others = {a: v for a, v in addresses.items() if a not in planes and a not in cylinders}

    def line(address, area):
        return "%s (area %s mm2)" % (address, _g(area))

    lines = ["The part before the edit, in mm:",
             "bounding box: %s x %s x %s mm (x %s..%s, y %s..%s, z %s..%s)" % (
                 _g(bound.XLength), _g(bound.YLength), _g(bound.ZLength),
                 _g(bound.XMin), _g(bound.XMax), _g(bound.YMin), _g(bound.YMax),
                 _g(bound.ZMin), _g(bound.ZMax)),
             "volume: %s mm3, solids: %d" % (_g(shape.Volume), len(shape.Solids))]
    lines += _cut([line(a, v) for a, v in sorted(planes.items(), key=lambda item: -item[1])],
                  "planes")
    lines += _cut([line(a, v) for a, v in sorted(cylinders.items())], "cylinders")
    lines += _cut([line(a, v) for a, v in sorted(others.items())], "other surfaces")
    return "\n".join(lines)


def _base_feature(doc):
    base = next((obj.BaseFeature for obj in doc.Objects
                 if obj.TypeId == "PartDesign::Body" and obj.BaseFeature is not None), None)
    return base if base is not None and _measurable(base) is not None else None


def _addresses_of(shape):
    """Every face address of *shape* with the area summed over the faces that share it."""
    found = {}
    for face in shape.Faces:
        surface = face.Surface
        kind = surface.__class__.__name__
        if kind == "Plane":
            axis = _along(surface.Axis)
            address = _plane_at(axis, round(sum(a * b for a, b in zip(axis, surface.Position)), 4))
            address = "plane " + address
        elif kind == "Cylinder":
            axis = _along(surface.Axis)
            centre = surface.Center
            reach = sum(a * b for a, b in zip(axis, centre))
            seat = tuple(round(c - reach * a + 0.0, 4) for c, a in zip(centre, axis))
            address = "%s diameter %s, axis %s, at %s" % (
                "hole" if _concave(face, surface) else "boss",
                _g(round(surface.Radius * 2, 4)), _vector(axis), _vector(seat))
        elif kind == "Cone":
            address = "cone axis %s, apex %s, angle %s" % (
                _vector(_along(surface.Axis)), _vector(_rounded(surface.Apex)),
                _g(round(abs(math.degrees(surface.SemiAngle)), 2)))
        elif kind == "Toroid":
            address = "torus axis %s, centre %s, radii %s and %s" % (
                _vector(_along(surface.Axis)), _vector(_rounded(surface.Center)),
                _g(round(surface.MajorRadius, 4)), _g(round(surface.MinorRadius, 4)))
        else:
            poles = getattr(surface, "getPoles", None)
            if poles:
                points = [point for row in surface.getPoles() for point in row]
                low = [min(point[i] for point in points) for i in range(3)]
                high = [max(point[i] for point in points) for i in range(3)]
            else:
                box = face.BoundBox
                low, high = [box.XMin, box.YMin, box.ZMin], [box.XMax, box.YMax, box.ZMax]
            address = "%s bbox x %s..%s, y %s..%s, z %s..%s" % (
                kind, *[_g(round(v, 1) + 0.0) for pair in zip(low, high) for v in pair])
        found[address] = found.get(address, 0.0) + face.Area
    return found


def _rounded(point, places=4):
    return tuple(round(value, places) for value in (point.x, point.y, point.z))


def _g(value):
    return "%.6g" % value


def _vector(values):
    return "(%s)" % ", ".join(_g(value + 0.0) for value in values)


def _plane_at(axis, offset):
    named = {(1.0, 0.0, 0.0): "x", (0.0, 1.0, 0.0): "y", (0.0, 0.0, 1.0): "z"}.get(axis)
    if named:
        return "%s = %s" % (named, _g(offset))
    return "normal %s at offset %s" % (_vector(axis), _g(offset))


def _concave(face, surface):
    """Whether the material lies outside this cylinder face, so it is a hole and not a boss."""
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    radial = face.valueAt(u, v) - surface.Center
    along = radial.dot(surface.Axis)
    radial = radial - surface.Axis * along
    return face.normalAt(u, v).dot(radial) < 0


def _cut(entries, title):
    shown = entries[:_INVENTORY_LINES]
    if len(entries) > len(shown):
        shown.append("... and %d more" % (len(entries) - len(shown)))
    return ["%s:" % title] + ["  " + entry for entry in shown] if entries else []


def _family(surface):
    """Which family of surfaces this face belongs to, and where along it it sits."""
    kind = surface.__class__.__name__
    radius = getattr(surface, "Radius", None)
    axis = getattr(surface, "Axis", None)
    centre = getattr(surface, "Center", None)
    if radius is None or axis is None:
        return (kind, None, None), None
    return ((kind, round(radius * 2, 6), _along(axis)),
            tuple(round(v, 6) for v in centre) if centre is not None else None)


def _along(axis):
    """
    An axis is a line: a bore's normal pointing in or out is the same axis.

    The zero is added so that a flipped zero reads as 0.0 rather than -0.0.
    """
    direction = [round(value, 6) for value in (axis.x, axis.y, axis.z)]
    leading = next((value for value in direction if value), 0.0)
    flip = -1 if leading < 0 else 1
    return tuple(value * flip + 0.0 for value in direction)


_SCRATCH = "DaedalusTypeProbe"


def _inventoried(obj):
    """
    Every property the object has, as the running FreeCAD reports it, shared
    by `describe_object` and `describe_type`. An enumeration is named where
    there is one -- `'Through all'` for `ThroughAll` is a Preflight failure
    no property list would catch. An empty-string value is dropped: a third
    of one listing was `"group": ""` and `"documentation": ""`.
    """
    inventory = []
    for prop in obj.PropertiesList:
        entry = {"name": prop, "type": obj.getTypeIdOfProperty(prop)}
        for key, said in (("group", obj.getGroupOfProperty(prop)),
                          ("documentation", obj.getDocumentationOfProperty(prop))):
            if said:
                entry[key] = said
        try:
            value = _readable(getattr(obj, prop))
        except Exception as exc:
            value = "<unreadable: %s>" % type(exc).__name__
        if not _opaque(value):
            entry["value"] = value
        spellings = _enumerations(obj, prop)
        if spellings:
            entry["enumeration"] = spellings
        inventory.append(entry)
    return inventory


def _opaque(value):
    """
    Whether this value tells the reader nothing it could act on.

    `<Shape object at 0x7f8247480>` has no textual value and its address
    changes every run — knowing the property is there is the whole of what a
    Shape-valued property has to say, and the entry already says that.
    """
    if value is None or value == "":
        return True
    return isinstance(value, str) and value.startswith("<") and " object at 0x" in value


def _enumerations(obj, prop):
    """The spellings an enumeration property accepts, or None where it is not one."""
    try:
        return obj.getEnumerationsOfProperty(prop) or None
    except Exception:
        return None


def _described_type(type_id):
    """
    What a type has, asked of a throwaway Document and never of the user's.
    """
    import FreeCAD

    _load_geometry_modules()
    was_active = FreeCAD.ActiveDocument
    scratch = FreeCAD.newDocument(_SCRATCH, hidden=True, temp=True)
    try:
        try:
            obj = scratch.addObject(type_id, "Probe")
        except Exception as exc:
            raise ToolError(
                "This build cannot create %r: %s. Call supported_types to see what it has."
                % (type_id, exc)
            )
        return {"type": obj.TypeId, "properties": _inventoried(obj)}
    finally:
        FreeCAD.closeDocument(scratch.Name)
        if was_active is not None:
            FreeCAD.setActiveDocument(was_active.Name)


def build_registry(document=None, pipeline=None, dispatch=_direct) -> ToolRegistry:
    """
    Build the registry against a Document.

    `document` may be a Document or a callable returning one; leaving it None
    resolves `FreeCAD.ActiveDocument` at call time, which is what the addon
    wants — the user may close and open files under us. `pipeline` is the
    apply pipeline, `None` for the read-only Tools Level 2 introspection
    tests want, and `dispatch` reads a Gui touch (the live selection, the
    active Body) -- identity headless, a live GUI's marshaller otherwise.
    """
    def active():
        import FreeCAD

        doc = document() if callable(document) else document
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            raise ToolError("There is no open Document. Ask the user to create or open one.")
        return doc

    registry = ToolRegistry()

    _register_document(registry, active, dispatch)
    _register_introspection(registry, active)
    _register_material(registry)

    if pipeline is not None:
        _register_apply(registry, pipeline, lambda: _selection(dispatch))

    return registry


def _register_document(registry, active, dispatch=_direct):
    @registry.tool(
        "snapshot",
        "The cheap summary of Document state: what objects exist, what is active, "
        "and what the user has selected. Not the whole tree — call describe_object "
        "for detail on one object.",
    )
    def snapshot():
        doc = active()
        objects = [
            {"name": o.Name, "label": o.Label, "type": o.TypeId, "errors": _errors(o)}
            for o in doc.Objects[:_SNAPSHOT_LIMIT]
        ]
        result = {
            "document": doc.Name,
            "objects": objects,
            "object_count": len(doc.Objects),
            "active_body": _active_body(doc, dispatch),
            "selection": _selection(dispatch),
        }
        if len(doc.Objects) > _SNAPSHOT_LIMIT:
            result["truncated"] = "showing %d of %d objects" % (_SNAPSHOT_LIMIT, len(doc.Objects))
        return result

    @registry.tool(
        "base_inventory",
        "The part an editing run starts from, in words, or none. Driven by the loop "
        "to brief Design, never by a phase.",
    )
    def base_inventory_tool():
        return {"inventory": base_inventory(active())}

    @registry.tool(
        "overall_size",
        "How big the finished part is. Driven by the loop once before Review, "
        "never by a phase.",
    )
    def overall_size():
        return {"size_mm": _overall_size(active())}

    @registry.tool(
        "document_copy",
        "The live Document saved to a throwaway file, for a phase kernel to open. "
        "Driven by the loop, never by a phase; the caller deletes the file.",
    )
    def document_copy():
        doc = active()
        return {"path": dispatch(lambda: copy_of(doc))}

    @registry.tool(
        "dependencies",
        "What this object is built from and what would break if it changed. "
        "Check this before modifying or deleting anything.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The object's Name."}},
            "required": ["name"],
        },
    )
    def dependencies(name):
        doc = active()
        obj = doc.getObject(name)
        if obj is None:
            raise ToolError(
                "No object named %r. Existing: %s"
                % (name, ", ".join(o.Name for o in doc.Objects) or "(the Document is empty)")
            )
        return {
            "name": obj.Name,
            "depends_on": [o.Name for o in obj.OutList],
            "used_by": [o.Name for o in obj.InList],
        }


def _register_introspection(registry, active):
    @registry.tool(
        "describe_object",
        "Every property this object actually has in the running FreeCAD, with its "
        "type, group, documentation and current value. Use this instead of guessing "
        "a property name.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The object's Name, not its Label."}},
            "required": ["name"],
        },
    )
    def describe_object(name):
        doc = active()
        obj = doc.getObject(name)
        if obj is None:
            by_label = doc.getObjectsByLabel(name)
            if not by_label:
                raise ToolError(
                    "No object named %r. Existing: %s"
                    % (name, ", ".join(o.Name for o in doc.Objects) or "(the Document is empty)")
                )
            if len(by_label) > 1:
                raise ToolError(
                    "Label %r matches more than one object: %s. Use Name instead of Label."
                    % (name, ", ".join(o.Name for o in by_label))
                )
            obj = by_label[0]

        described = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "state": list(obj.State or []),
            "properties": _inventoried(obj),
        }

        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            box = shape.BoundBox
            described["shape"] = {
                "faces": len(shape.Faces),
                "edges": len(shape.Edges),
                "vertexes": len(shape.Vertexes),
                "volume": round(shape.Volume, 6),
                "bound_box": [round(v, 6) for v in
                              (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)],
            }
        return described

    @registry.tool(
        "describe_type",
        "Every property a type has, before any object of it exists — with the "
        "spellings this build accepts where a property is a choice between named "
        "values. Use this to check a type you are about to create; use "
        "describe_object for one that is already in the Document.",
        {
            "type": "object",
            "properties": {"type_id": {"type": "string",
                                       "description": "The full type, e.g. 'PartDesign::Pocket'."}},
            "required": ["type_id"],
        },
    )
    def describe_type(type_id):
        return _described_type(type_id)

    @registry.tool(
        "supported_types",
        "The object types this Document can actually create, straight from the "
        "running FreeCAD. Filter with a prefix such as 'Part::' or 'PartDesign::'.",
        {
            "type": "object",
            "properties": {"prefix": {"type": "string", "description": "Optional case-insensitive prefix filter."}},
        },
    )
    def supported_types(prefix=None):
        doc = active()
        _load_geometry_modules()
        types = sorted(doc.supportedTypes())
        if prefix:
            lowered = prefix.lower()
            types = [t for t in types if t.lower().startswith(lowered)]
        return {"types": types, "count": len(types)}


def _register_material(registry):
    @registry.tool(
        "materials",
        "The material cards this FreeCAD has, with the uuid ShapeMaterial needs. A "
        "uuid is not something to guess and a card's density is not something to set "
        "— pick the card that already has the density asked for.",
        {
            "type": "object",
            "properties": {"name": {"type": "string",
                                    "description": "Optional case-insensitive name filter, e.g. 'alumin'."}},
        },
    )
    def materials(name=None):
        """
        The material cards this build has, filtered by name where one is given.

        The 200 cards FreeCAD 1.1 ships are the same list every call, so an
        unfiltered answer is a page of it. A title block says "plain carbon
        steel" where cards say "Steel-Generic", so an unmatched name is
        matched on a word of itself, and failing that gets the whole shelf —
        an empty list read as "no material to assign" once cost a recorded
        run every revision it had.
        """
        try:
            import Materials
        except ImportError:
            raise ToolError("This FreeCAD build has no Materials module, so no material "
                            "can be assigned.")
        cards = Materials.MaterialManager().Materials

        def listed(match):
            return sorted(({"name": card.Name, "uuid": uuid,
                            "density": _physical(card, "Density")}
                           for uuid, card in cards.items() if match(card.Name.lower())),
                          key=lambda entry: entry["name"].lower())

        wanted = (name or "").lower()
        found = listed(lambda card: wanted in card)
        widened = bool(wanted) and not found
        if widened:
            words = [word for word in wanted.split() if len(word) > 2]
            found = (listed(lambda card: any(word in card for word in words))
                     or listed(lambda card: True))
        result = {"materials": found[:_MATERIALS_LIMIT], "count": len(found)}
        if widened:
            result["nothing_matched"] = (
                "no card's name contains %r, so a wider list is given instead — pick the "
                "nearest card rather than treating the material as unavailable." % name)
        if len(found) > _MATERIALS_LIMIT:
            result["truncated"] = ("showing %d of %d — filter by name"
                                   % (_MATERIALS_LIMIT, len(found)))
        return result


def _physical(card, quantity):
    """One physical value off a material card, or None where it has none."""
    try:
        value = card.getPhysicalValue(quantity)
    except Exception:
        return None
    return str(value) if value is not None else None


def _register_apply(registry, pipeline, selection):
    @registry.tool(
        "apply_step",
        "Run one Step against the Document, as one undoable unit. You get back "
        "either 'applied' or 'failed' with the traceback to correct against.",
        {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python for this Step. `doc`, `App`, `Part`, `Sketcher` "
                                   "and `PartDesign` are in scope. "
                                   "No Gui.*, no live selection, and no literal element "
                                   "names such as Edge12 — compute references instead.",
                },
            },
            "required": ["code"],
        },
    )
    def apply_step(code):
        return pipeline.apply(code, selection=selection()).as_tool_result()


def _readable(value):
    """Turn a FreeCAD property value into something JSON can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_readable(v) for v in value[:20]]
    if hasattr(value, "Name") and hasattr(value, "TypeId"):
        return value.Name
    return str(value)
