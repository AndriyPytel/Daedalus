"""
The FreeCAD-backed Tools. The only code in the addon that touches the Document.

API knowledge comes from introspecting the running FreeCAD — property lists,
supported types, real dependencies — not from a documentation corpus that
mixes versions and would reintroduce the removed API this project exists to
eliminate (ADR-0006). What these Tools report is true of the user's exact
build, because it was asked of it.
"""
from core.tools import NO_PARAMETERS, ToolError, ToolRegistry

# Introspection reports what exists, not always what is correct. These are the
# cases where the two differ; the prompt carries this list and nothing more.
TRAPS = [
    "Sketch attachment goes through AttachmentSupport + MapMode, not Placement. "
    "Setting Placement on an attached sketch is silently overridden on recompute.",
    "sketch.Support and sketch.Plane were removed in FreeCAD 1.0. "
    "AttachmentSupport replaces Support.",
    "PartDesign features must be added to a Body via Body.addObject, not to the "
    "Document alone, or they will not appear in the feature tree.",
]

_SNAPSHOT_LIMIT = 200

# supportedTypes() only reports types whose module has been imported. Without
# this, a session where the user never opened PartDesign is told PartDesign::Pad
# does not exist — introspection reporting a confident, wrong absence.
_GEOMETRY_MODULES = ("Part", "PartDesign", "Sketcher")


def _load_geometry_modules():
    for module in _GEOMETRY_MODULES:
        try:
            __import__(module)
        except ImportError:
            pass


def _selection():
    """
    What the user has selected right now, sub-element names included.

    Element names from a live selection are explicit user input and are the
    one sanctioned source of an `Edge12` (ADR-0005). Headless has no GUI and
    therefore no selection.
    """
    try:
        import FreeCADGui
        selection = FreeCADGui.Selection.getSelectionEx()
    except (ImportError, AttributeError):
        return []

    return [
        {"object": sel.ObjectName, "elements": list(sel.SubElementNames)}
        for sel in selection
    ]


def _active_body(doc):
    try:
        import FreeCADGui
        body = FreeCADGui.ActiveDocument.ActiveView.getActiveObject("pdbody")
    except (ImportError, AttributeError):
        body = None
    if body is not None:
        return body.Name
    bodies = [o.Name for o in doc.Objects if o.isDerivedFrom("PartDesign::Body")]
    return bodies[0] if len(bodies) == 1 else None


def _errors(obj):
    return [s for s in (obj.State or []) if s in ("Invalid", "Error", "Touched")]


def build_registry(document=None, pipeline=None) -> ToolRegistry:
    """
    Build the registry against a Document.

    `document` may be a Document or a callable returning one; leaving it None
    resolves `FreeCAD.ActiveDocument` at call time, which is what the addon
    wants — the user may close and open files under us.

    `pipeline` is the apply pipeline. Passing None registers the read-only
    Tools alone, which is what Level 2 introspection tests want.
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
            "active_body": _active_body(doc),
            "selection": _selection(),
        }
        if len(doc.Objects) > _SNAPSHOT_LIMIT:
            result["truncated"] = "showing %d of %d objects" % (_SNAPSHOT_LIMIT, len(doc.Objects))
        return result

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
            obj = by_label[0]

        properties = []
        for prop in obj.PropertiesList:
            entry = {
                "name": prop,
                "type": obj.getTypeIdOfProperty(prop),
                "group": obj.getGroupOfProperty(prop),
                "documentation": obj.getDocumentationOfProperty(prop),
            }
            try:
                entry["value"] = _readable(getattr(obj, prop))
            except Exception as exc:
                entry["value"] = "<unreadable: %s>" % type(exc).__name__
            properties.append(entry)

        described = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "state": list(obj.State or []),
            "properties": properties,
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

    if pipeline is not None:
        _register_apply(registry, pipeline, _selection)

    return registry


def _register_apply(registry, pipeline, selection):
    @registry.tool(
        "apply_step",
        "Run one Step against the Document. The code is checked, tried against a "
        "throwaway copy first, and then applied as a single undoable unit. You get "
        "back either 'applied' or 'failed' with the traceback to correct against.",
        {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python for this Step. `doc` and `App` are in scope. "
                                   "No Gui.*, no live selection, and no literal element "
                                   "names such as Edge12 — compute references instead.",
                },
                "label": {
                    "type": "string",
                    "description": "Short name for the undo entry, e.g. 'Pad the base sketch'.",
                },
            },
            "required": ["code"],
        },
    )
    def apply_step(code, label="Copilot Step"):
        # Preflight lives behind this Tool. From here up, a Step was applied or
        # it was not — there is no third thing to know about.
        return pipeline.apply(code, selection=selection(), label=label).as_tool_result()


def _readable(value):
    """Turn a FreeCAD property value into something JSON can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_readable(v) for v in value[:20]]
    if hasattr(value, "Name") and hasattr(value, "TypeId"):
        return value.Name
    return str(value)
