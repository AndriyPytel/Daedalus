"""
The FreeCAD-backed Tools. The only code in the addon that touches the Document.

API knowledge comes from introspecting the running FreeCAD — property lists,
supported types, real dependencies — not from a documentation corpus that
mixes versions and would reintroduce the removed API this project exists to
eliminate (ADR-0006). What these Tools report is true of the user's exact
build, because it was asked of it.

Introspection reports what exists, not always what is correct. `TRAPS` is the
cases where the two differ; the prompt carries that list and nothing more.
"""
import math

from core.tools import NO_PARAMETERS, ToolError, ToolRegistry

TRAPS = [
    "Sketch attachment goes through AttachmentSupport + MapMode, not Placement. "
    "Setting Placement on an attached sketch is silently overridden on recompute.",
    "sketch.Support and sketch.Plane were removed in FreeCAD 1.0. "
    "AttachmentSupport replaces Support.",
    "PartDesign features must be added to a Body via Body.addObject, not to the "
    "Document alone, or they will not appear in the feature tree.",
    "There is no active Body outside the GUI. ActiveObject, setActiveObject and "
    "activeView do not exist on App.Document; nothing needs activating, Body.addObject "
    "is what puts a feature in a Body.",
    "A sketch is a 'Sketcher::SketchObject'; there is no 'PartDesign::Sketch'. Its "
    "geometry comes from the Part module — Part.Circle, Part.LineSegment, Part.ArcOfCircle "
    "— which is why sketch.addGeometry takes a Part object. The Sketcher module holds "
    "constraints, not geometry.",
    "Body.addObject moves Body.Tip to the feature you just added. Capture the base "
    "feature before adding, or a dressup such as PartDesign::Fillet will end up "
    "referencing itself and fail to recompute.",
    "The constraint types this build has are Angle, Block, Coincident, Diameter, "
    "Distance, DistanceX, DistanceY, Equal, Horizontal, InternalAlignment, Parallel, "
    "Perpendicular, PointOnObject, Radius, SnellsLaw, Symmetric, Tangent, "
    "TangentViaPoint, Vertical and Weight. There is no 'Concentric': concentricity is "
    "Coincident between the two centres — Sketcher.Constraint('Coincident', a, 3, b, 3), "
    "where the position id is 0 for the edge itself, 1 start, 2 end, 3 centre. A type "
    "name this build does not have, or one of these at the wrong number of arguments, "
    "takes the FreeCAD core down inside the C++ constructor rather than raising, so "
    "there is no traceback to correct against.",
    "A material is assigned as obj.ShapeMaterial = Materials.MaterialManager()."
    "getMaterial(uuid). The property is a Materials::PropertyMaterial and takes a "
    "Materials.Material and nothing else. App.Material is the old appearance-only "
    "material and has no Name or Density. A Materials.Material cannot be built, "
    "subscripted or written to from here, so a density is chosen by choosing the card "
    "that already carries it — call the materials Tool for the uuid rather than "
    "guessing one.",
    "A thread is PartDesign::AdditiveHelix sweeping a profile along a helical path, "
    "or PartDesign::Hole with Threaded set; this build has both, and PartDesign::Helix "
    "and PartDesign::AdditivePipe besides. Lofting one V-section per turn builds a "
    "stack of flats that is not a helical surface, and nothing downstream reports it.",
    "A Pocket travels along the *negative* of its sketch normal, and a Pad along the "
    "positive, unless Reversed is True. A Pocket whose sketch normal points into the "
    "material therefore cuts away from the solid, meets nothing, and hands back the "
    "shape it was given — and FreeCAD reports it Up-to-date and Valid, because a Cut "
    "with nothing to cut is not an error. Check the sign against the Body's BoundBox: "
    "the sketch sits on the face the cut starts from with its normal pointing out of "
    "the material, or it carries Reversed=True. For a hole that goes through, "
    "Type='ThroughAll' says so and cannot run short or run the wrong way, where "
    "Length=bb.YLength is a distance in a direction you have to have got right.",
    "PartDesign::Fillet with UseAllEdges set rounds every edge of the base feature, "
    "which is a different part from one with the edges the drawing marks rounded. "
    "Where only some edges are meant, select them by computing which — off face "
    "normals, edge length, the plane an edge lies in — and pass that list in Base.",
    "Shape.BoundBox is taken off the shape's triangulation, so on a curved face it "
    "reports the tessellation and not the surface — and lands inside the true extreme, "
    "by the sagitta of whatever chord the mesh happened to use. The BoundBox of a pad "
    "with R25 rounded ends read XMin=0.182278 where the face is tangent at 0, and the "
    "five sketches placed at that XMin each built their feature 0.18 mm off, in a "
    "Document that reopens with XMin=0.0 and cannot be made to show it again. Read it "
    "for the sign of a direction and for which side of the solid you are on; never for "
    "a coordinate a feature is placed at. Shape.optimalBoundingBox() is exact where a "
    "box is genuinely what is wanted, and the face that carries the number — "
    "Surface.Center, Surface.Radius, a Vertex Point — is better still.",
    "App.Vector's methods are not all alike about the vector they are called on: "
    "multiply, normalize, scale and projectToPlane change it in place and hand it "
    "back, while add, sub and cross return a new one and leave it alone. So "
    "v.multiply(k) is not v scaled into a result, it is v scaled, and every later "
    "reader of v gets the scaled one. A recorded run's acceptance checks wrote "
    "unit(v) as v.multiply(1.0/n) and projected with zdir.multiply(dot(raw, zdir)): "
    "the projection zeroed zdir where the dot was 0 and left xdir 51.5 times too "
    "long, and 28 of 34 entries came back unmet on a part that met them. Where a "
    "vector is needed again, do the arithmetic with the operators — v * k, a - b, "
    "a + b — or build a new App.Vector; never call one of the in-place methods on a "
    "vector something else still reads.",
    "Body.addObject hands back a *list* of what it added, not the object. "
    "`sk = body.addObject(doc.addObject('Sketcher::SketchObject','S'))` therefore binds a "
    "list, and the next line — sk.AttachmentSupport, sk.addGeometry — dies on "
    "'list' object has no attribute. Add in one statement and keep the object in "
    "another: `sk = doc.addObject(...)` then `body.addObject(sk)`.",
    "A sketched curve that is not a line, an arc or a circle is a Part.BSplineCurve "
    "through points computed from its own equation — an involute flank, a cam profile, "
    "a cycloid. A chain of Part.LineSegment approximates it, and the approximation is "
    "what gets built and measured.",
    "A PropertyEnumeration setter — Pocket.Type, Pad.Type, and others — raises "
    "ValueError on any string outside its own accepted list, and that list is per "
    "property, not a spelling shared across the module. Read the real set first with "
    "obj.getEnumerationsOfProperty('Type') and set one of those; a spelling that works "
    "on one enum property is not evidence it works on another.",
    "PartDesign::Fillet fails on a radius the material cannot carry with 'BRep_API: "
    "command not done', a message that names the failed OCC call and nothing about the "
    "radius that caused it. A full round needs less than half the thickness of what it "
    "rounds; check the radius against the Body's wall thickness before setting it, not "
    "after the recompute fails with no way back to why.",
    "A Pad or Pocket sketch with more than one closed wire builds every wire — if the "
    "wires do not touch, the feature leaves the Body in more than one solid, and "
    "FreeCAD only reports it as a plain RuntimeError once the recompute runs, with no "
    "earlier signal. A second piece of geometry that does not touch the first belongs "
    "in its own Body, not a second wire in the same sketch.",
]

_SNAPSHOT_LIMIT = 200
_MATERIALS_LIMIT = 40

_GEOMETRY_MODULES = ("Part", "PartDesign", "Sketcher")


def _load_geometry_modules():
    """
    Import the geometry modules, so the Document will admit their types.

    `supportedTypes()` only reports types whose module has been imported.
    Without this, a session where the user never opened PartDesign is told
    PartDesign::Pad does not exist — introspection reporting a confident, wrong
    absence.
    """
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

    Read on the main thread, like every other Gui touch: the `snapshot` tool
    asks for this at the start of a run and again on every Step, and the loop
    that asks is on the worker thread. The import is local because
    `core.apply` imports this module.
    """
    try:
        import FreeCADGui

        from core.apply import on_main_thread
        selection = on_main_thread(FreeCADGui.Selection.getSelectionEx)
    except (ImportError, AttributeError):
        return []

    return [
        {"object": sel.ObjectName, "elements": list(sel.SubElementNames)}
        for sel in selection
    ]


_OPPOSITE_ISOMETRIC = "viewIsometricOpposite"
"""
Not a `FreeCADGui` button — `libFreeCADGui` names `viewFront`, `viewTop`,
`viewRight`, `viewRear`, `viewBottom`, `viewLeft`, `viewIsometric`,
`viewDimetric` and `viewTrimetric`, and none of them is the corner
diagonally across from isometric's. `capture_review_views` builds it instead,
from whatever orientation `viewIsometric` is already holding.
"""

_SECTION_VIEW = "section"
"""
Not a `FreeCADGui` orientation either — `capture_review_views` builds the
cutaway itself, clipping through whatever `_section_plane` finds to be the
part's own interior, the way FreeCAD's own View > Cross-sections does
(`View3DInventorPy.toggleClippingPlane`).
"""

REVIEW_VIEWS = ("viewIsometric", _OPPOSITE_ISOMETRIC, "viewTop", "viewFront", _SECTION_VIEW)


_A_3D_VIEW = "Gui::View3DInventor"


def active_document():
    """
    Whichever Document is in focus right now, or None with nothing open.

    The sanctioned route this module already is for the Document is what a
    Session's worker binds through, too: its first Intent asks this once, to
    settle which Document that Session is about (FCC-181), rather than the
    panel reaching `FreeCAD.ActiveDocument` on its own and being caught by
    `test_registry_is_the_only_route.py`.
    """
    import FreeCAD
    return FreeCAD.ActiveDocument


def _active_view(document=None):
    """
    The Document's 3D view — the one with a camera, not the tab in front.

    `document=None` reads `FreeCADGui.ActiveDocument`, whichever MDI tab is
    active in the whole GUI. A Session bound to its own Document (FCC-181)
    passes it explicitly and reaches that Document's view through
    `FreeCADGui.getDocument(name)` instead, so a Step landing in a Document
    the user is not currently looking at still moves the right camera.

    `ActiveDocument.ActiveView` is whichever MDI tab is active, and a
    Spreadsheet or a TechDraw page of the same Document is one of them. Neither
    has a camera, so with one of those in front `fitAll` raised AttributeError
    out of a Step whose geometry was already in the Document, and out of the
    Review capture at the end of a run.

    Asking the view for a camera and falling back to `mdiViewsOfType` is how
    FreeCAD's own Show module finds the viewport, in
    `Mod/Show/SceneDetails/Camera.py`. That one indexes the list blindly; a
    Document showing no 3D view at all is the case that returns None here.
    """
    try:
        import FreeCADGui
        gui_document = (FreeCADGui.getDocument(document.Name) if document is not None
                        else FreeCADGui.ActiveDocument)
        view = gui_document.ActiveView
        if hasattr(view, "getCamera"):
            return view
        views = gui_document.mdiViewsOfType(_A_3D_VIEW)
        return views[0] if views else None
    except (ImportError, AttributeError):
        return None


def _scaffolding(obj):
    """
    Whether *obj* is a Shape past `_SCAFFOLDING` — an Origin plane or axis,
    almost always — the same test `_measurable` makes for the reporting path,
    kept separate here because `fit_view` only cares about the oversized
    case: an object with no `Shape` at all, or one with no `Faces`, is not
    scaffolding, just not a solid, and hiding it would change what the
    camera fits to for no reason.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return False
    bound = shape.BoundBox
    return bound.isValid() and bound.DiagonalLength > _SCAFFOLDING


def _hide_scaffolding(document):
    hidden = []
    for obj in document.Objects:
        if not _scaffolding(obj):
            continue
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None or not view_object.Visibility:
            continue
        view_object.Visibility = False
        hidden.append(view_object)
    return hidden


def fit_view(document=None):
    """
    Put everything the Document shows back inside the viewport.

    Every landed Step moves the user's camera, which is the opposite of what
    `capture_review_views` does deliberately — and the reason is the same one.
    The camera holds whatever zoom and centre it had before the run, while a
    part is modelled at the scale its drawing gives it: a 3 mm bracket and a
    900 mm frame both arrive somewhere off to one side, one of them a few
    pixels across. The user is watching a Plan land Step by Step, and cannot
    judge what they are being asked to approve if it is not on screen.

    `document` is the Session's own bound Document (FCC-181); left None this
    reads whichever MDI tab is in front, which is wrong the moment two
    Documents are open and the user is looking at the other one.

    A Body's Origin carries three planes and three axes, each 1e100 mm
    across, and `view.fitAll()` is a raw Coin3D call: it fits the camera to
    the bounding box of everything currently visible, with no filter of its
    own. One visible Origin feature is enough to turn "fit the part in the
    viewport" into "fit a plane 1e100 mm across", at which point a
    millimetre- or centimetre-scale part is a speck nothing can see. An
    Origin plane ends up visible in the first place when a sketch is
    attached or edited programmatically — the path a generated Step takes —
    rather than through the GUI's own attachment editor, which is what
    normally re-hides it. Anything past `_SCAFFOLDING` — the same threshold
    `_measurable` already applies to the reporting path — is hidden before
    `fitAll()` runs and shown again
    straight after, each object's own prior `Visibility` restored rather than
    blanket-set, so scaffolding the user had already hidden themselves stays
    hidden; a `document` with no `Objects` to check (the `None` default, read
    here only) leaves this skipped and `fitAll()` as bare as it always was.
    This runs after `_transaction` has already committed or raised (never
    inside the open transaction, so a rolled-back Step never touches
    visibility at all), and the hide/restore happens inside one call, so
    nothing outlives a raised exception or a rollback to leave a stale
    `Visibility` behind — undo not tracking `ViewObject` properties is moot
    here because none of this survives past the `finally`.

    Orientation is left alone. Which way the part is turned is the user's
    choice, and nothing here is a better one.

    Headless there is no view and nothing to fit.
    """
    view = _active_view(document)
    if view is None:
        return
    hidden = _hide_scaffolding(document) if document is not None and hasattr(document, "Objects") else []
    try:
        view.fitAll()
    finally:
        for view_object in hidden:
            view_object.Visibility = True


def _cavity_center(box, material):
    """
    The centre of mass of the largest void inside `box`, or None with
    nothing hollow to find.

    `material` is the fused Shape of the tip features only — "only what
    nothing else uses", the same `obj.InList` reading `_inventory` already
    takes, so a Box consumed by a Shell or a Cut does not paper over the
    cavity its own result carves. Cutting that material from a solid built
    on `box` itself leaves exactly the voids a shell, bore or blind pocket
    opens up; the biggest by volume is the one worth a plane through it.

    Costs one boolean `cut`: measured at ~0.11s for a 200x200x30 plate cut
    by forty 8mm bores (`tests/test_freecad_tools_level2.py`), and the same
    single call, void or not, on anything smaller.
    """
    import FreeCAD
    import Part

    solid = Part.makeBox(box.XLength, box.YLength, box.ZLength,
                          FreeCAD.Vector(box.XMin, box.YMin, box.ZMin))
    try:
        voids = [void for void in solid.cut(material).Solids if void.Volume > 1e-6]
    except Part.OCCError:
        return None
    if not voids:
        return None
    return max(voids, key=lambda void: void.Volume).CenterOfMass


def _section_plane(document):
    """
    A placement that cuts through the part's real interior, not one of its
    faces — the one thing a shell, a bore or a blind pocket needs to become
    visible that the existing four views cannot give it (FCC-261).

    The plane sits at the centre of the largest actual void the modelled
    geometry has (`_cavity_center`), not a guess at where a cavity usually
    sits — a bore or pocket well off from the part's own bounding-box
    centre still gets cut through it rather than past it. A solid part with
    nothing hollow, or a void `_cavity_center` cannot resolve, falls back to
    the bounding box's own centre, so a section view is still produced.

    `document` is the Session's own bound Document (FCC-181); the `None`
    default reads `FreeCAD.ActiveDocument`, matching every other reader in
    this module. None with nothing modelled to cut through yet.
    """
    import FreeCAD

    doc = document if document is not None else FreeCAD.ActiveDocument
    if doc is None:
        return None
    box = None
    material = None
    for obj in doc.Objects:
        bound = _measurable(obj)
        if bound is None:
            continue
        box = bound if box is None else box.united(bound)
        if not obj.InList:
            material = obj.Shape if material is None else material.fuse(obj.Shape)
    if box is None:
        return None
    center = _cavity_center(box, material) or box.Center
    return FreeCAD.Placement(center, FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90))


def capture_review_views(paths, document=None, size=(800, 600)):
    """
    The five views Review judges, captured once, camera put back afterwards.

    Two isometrics from opposite corners of the part's box, plus top, front
    and a section. Front-right-top and the corner diagonally across from it
    between them show every one of a cube's six faces by construction, which
    the old isometric/front/top/right never did — three of the six were
    outside all four of those, the same blind spot `FCC-63` closed for a
    single mirrored isometry, one level deeper. The spellings were checked
    against libFreeCADGui rather than recalled.

    The section is a fifth capture, not a replacement (FCC-261): a shell, a
    bore or a blind pocket is invisible to all four of the others because
    none of them cuts into the part. `_section_plane` finds where to cut;
    `toggleClippingPlane` is the same call FreeCAD's own View > Cross-sections
    makes, so the cutaway a user could open by hand is the one Review is
    handed. A part with nothing modelled yet has no plane to cut with, and
    that capture is skipped rather than saved as a plain front view wearing
    the section's name.

    `document` is the Session's own bound Document (FCC-181), read through
    `FreeCADGui.getDocument(name)` rather than whichever tab is in front.

    Returns the paths actually written — empty with no GUI, which is what
    leaves the headless fallback to the Preflight Worker's rasteriser.

    The camera is restored whatever happens, and so is the clipping plane:
    the user did not ask for their view to be moved or left mid-cutaway, and
    a run that ends with the part face-on or sectioned because Review looked
    at it last has damaged something that belongs to them.
    """
    import FreeCAD

    view = _active_view(document)
    if view is None:
        return []

    camera = view.getCamera()
    sectioned = False
    written = []
    try:
        for name, path in zip(REVIEW_VIEWS, paths):
            if name == _OPPOSITE_ISOMETRIC:
                view.setCameraOrientation(view.getCameraOrientation().multiply(
                    FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 180)))
            elif name == _SECTION_VIEW:
                plane = _section_plane(document)
                if plane is None:
                    continue
                view.toggleClippingPlane(1, False, True, plane)
                sectioned = True
            else:
                getattr(view, name)()
            view.fitAll()
            view.saveImage(path, size[0], size[1], "Current")
            if name == _SECTION_VIEW:
                view.toggleClippingPlane(0)
                sectioned = False
            written.append(path)
    finally:
        view.setCamera(camera)
        if sectioned:
            view.toggleClippingPlane(0)
    return written


def _active_body(doc):
    """
    The Body the user is working in, or the only one there is.

    `ActiveDocument.ActiveView` is the Gui's state and is read on the main
    thread for the reason `_selection` is.
    """
    try:
        import FreeCADGui

        from core.apply import on_main_thread
        body = on_main_thread(
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


def _overall_size(doc):
    """
    How big the modelled part is, `[x, y, z]` in mm, or None if nothing is.

    The one dimension a drawing states outright, and the one nothing in the
    loop reported: Review judged renders and a list of type names, so whether
    the result came out the right size was not a question it could ask.

    A Body's Origin carries three planes and three axes, each 1e100 across, so
    a literal union of every bounding box makes any part 2e+100 mm wide.
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
    What the Document now holds, in the two layers Review can judge by.

    The feature tree with its parameters is what the part was asked to be, and
    it is trustworthy because Lint forces the tree to be genuine PartDesign
    (ADR-0012). The inventory read off `Shape` is what it came out as — the
    only one of the two that reports a hole nobody thought to ask about, since
    the Acceptance list is written before the part exists.

    Both layers read data that was already there; neither is a subsystem.
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

    Hidden and read-only are FreeCAD's own answer to "is this editable here":
    a Pad extruded one side marks the other side's length read-only, a Hole
    cut through marks its depth read-only. It is not a relevance filter — an
    unthreaded Hole still reports its thread properties, because this build
    leaves them editable — so what comes out is every value the property
    editor would let someone type into, and not a curated list. A link, a
    shape or a placement is not a value to judge by, so those go.
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

    Raw geometry is the form in which a defect is formally present and
    practically invisible: the coordinates of every LineSegment are the most
    tokens in the Description for the least judgement. A named constraint is
    the same dimension, said once, by someone who chose to name it.
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
    The geometry as it came out: how much material there is, and one entry per
    family of surfaces.

    Grouping by kind, diameter and axis is what keeps this a description: on a
    real part twenty identical holes are one entry with a count, and ungrouped
    they are twenty. Only what nothing else uses is measured, so this is the
    part as it stands and not every intermediate feature it was built from.

    Two faces of one bore share a seat on the axis, where six flats share
    nothing, so a family is counted by its distinct seats where it has them.
    The volume is how a Step that removed nothing is told from one that worked:
    a Pocket extruded away from the solid cuts nothing, and every other reading
    here — the feature tree, the face families, the overall size — comes out
    identical whether it cut or not.
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


_SCRATCH = "CopilotTypeProbe"


def _inventoried(obj):
    """
    Every property the object has, as the running FreeCAD reports it.

    Shared by `describe_object` and `describe_type` because the question is the
    same one — what does this thing actually have — and only where the object
    came from differs. An enumeration is named where there is one: a property
    whose value is a choice between spellings is the case where knowing the
    property exists is not enough, and `'Through all'` for `ThroughAll` is a
    Preflight failure that no property list would have caught.

    A key whose value is an empty string is a line of the answer that says
    nothing, and this answer is read by something that pays by the token: a
    third of one of these listings was `"group": ""` and `"documentation": ""`.
    What is absent is absent.
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

    `<Shape object at 0x7f8247480>` is the repr of something that has no
    textual value, and the address changes every run. Knowing the property is
    there is the whole of what a Shape-valued property has to say, and the
    entry already says that.
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

    Before the first object of a type exists its properties are unreachable:
    `describe_object` needs the Name of an object that is already there and
    `supported_types` reports type names and nothing else. So the type is
    instantiated where nobody can see it, read, and the Document destroyed
    (FCC-95). Still ADR-0006 — this is the running build answering about
    itself, not a documentation corpus mixed across versions.

    The active Document is put back by hand. `newDocument` makes its Document
    the active one and `closeDocument` leaves none active, so a Tool that only
    asked a question would have left the addon with nowhere to write. The
    scratch Document is closed by the name FreeCAD gave it, not by the one it
    was asked for, since FreeCAD uniquifies a name that is already taken.
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

    _register_document(registry, active)
    _register_introspection(registry, active)
    _register_material(registry)

    if pipeline is not None:
        _register_apply(registry, pipeline, _selection)

    return registry


def _register_document(registry, active):
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
        "overall_size",
        "How big the finished part is. Driven by the loop once before Review, "
        "never by a phase.",
    )
    def overall_size():
        return {"size_mm": _overall_size(active())}

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

        The 200 cards that ship with FreeCAD 1.1 are the same list every call,
        so the unfiltered answer is a page of them rather than the lot. A title
        block says "plain carbon steel" where the cards say "Steel-Generic", so
        a name that matches nothing whole is matched on a word of itself, and
        failing that gets the whole shelf: an empty list is the one answer that
        reads as "this build has no material to assign", and a recorded run
        spent every revision it had on that reading rather than picking a card.
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
        "Run one Step against the Document. The code is checked, tried against a "
        "throwaway copy first, and then applied as a single undoable unit. You get "
        "back either 'applied' or 'failed' with the traceback to correct against.",
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
                "label": {
                    "type": "string",
                    "description": "Short name for the undo entry, e.g. 'Pad the base sketch'.",
                },
                "op": {
                    "type": "string",
                    "description": "This Step's operation, one of the 36 in plan.md's closed "
                                   "list — carried straight from the Plan, not asked for here.",
                },
            },
            "required": ["code"],
        },
    )
    def apply_step(code, label="Copilot Step", op=None):
        """
        Preflight lives behind this Tool: from here up, a Step was applied or
        it was not, and there is no third thing to know about.
        """
        return pipeline.apply(code, selection=selection(), label=label,
                              op=op).as_tool_result()

    @registry.tool(
        "check_assertions",
        "Run read-only checks against a throwaway copy of the finished Document. "
        "Driven by the loop once per run, never by a phase.",
        {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    )
    def check_assertions(code):
        return pipeline.check_assertions(code)

    @registry.tool(
        "review_views",
        "Capture the four views Review judges. Driven by the loop at the end of a "
        "run, never by a phase.",
        {"type": "object", "properties": {}},
    )
    def review_views():
        return {"views": pipeline.review_views()}



def _readable(value):
    """Turn a FreeCAD property value into something JSON can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_readable(v) for v in value[:20]]
    if hasattr(value, "Name") and hasattr(value, "TypeId"):
        return value.Name
    return str(value)
