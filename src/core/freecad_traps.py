"""
A Trap: a claim about FreeCAD's behaviour and, where one can be written, the script that reproduces it.
`SEED_TRAPS` names the cases where introspection reports something that exists but is not correct, each carrying its script. `RETIRED_TRAPS` keeps the ones a script disproved, with why.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Trap:
    claim: str
    script: str = None


SEED_TRAPS = [
    Trap(
        claim='Sketch attachment goes through AttachmentSupport + MapMode, not Placement. Setting Placement on an attached sketch is silently overridden on recompute.',
        script="""
plane = doc.addObject("App::Plane", "XY_Plane")
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(plane, "")]
sk.MapMode = "FlatFace"
doc.recompute()
attached_placement = App.Placement(sk.Placement)
sk.Placement = App.Placement(App.Vector(50, 50, 50), App.Rotation())
doc.recompute()
assert sk.Placement.Base == attached_placement.Base, "Placement stuck where it was set, expected the attachment to override it"
""",
    ),
    Trap(
        claim='sketch.Support and sketch.Plane were removed in FreeCAD 1.0. AttachmentSupport replaces Support.',
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
try:
    sk.Support
    assert False, "Support still exists on Sketcher::SketchObject"
except AttributeError:
    pass
try:
    sk.Plane
    assert False, "Plane still exists on Sketcher::SketchObject"
except AttributeError:
    pass
sk.AttachmentSupport
""",
    ),
    Trap(
        claim="A PartDesign::Pad or Pocket left off the Body via Body.addObject computes its own Shape from Profile regardless, and looks completely correct -- Shape.isNull() False, a sane Volume -- which is exactly what a check against the feature's own Shape misses. Body.Shape stays null until the feature is Body.addObject'd, and reading Body.Shape.Volume off it raises 'shape is invalid', not a wrong number. Check the Body's Shape, never the feature's own.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
sk = doc.addObject("Sketcher::SketchObject", "S")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
doc.recompute()
assert not pad.Shape.isNull()
assert pad.Shape.Volume > 0
try:
    body.Shape.Volume
    assert False, "Body.Shape.Volume did not raise for a feature never added to the Body"
except Exception as e:
    assert "invalid" in str(e).lower()
doc.removeObject(pad.Name)
""",
    ),
    Trap(
        claim="A sketch is a 'Sketcher::SketchObject'; there is no 'PartDesign::Sketch'. Its geometry comes from the Part module — Part.Circle, Part.LineSegment, Part.ArcOfCircle — which is why sketch.addGeometry takes a Part object. The Sketcher module holds constraints, not geometry.",
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
assert sk.TypeId == "Sketcher::SketchObject"
try:
    doc.addObject("PartDesign::Sketch", "Bad")
    assert False, "PartDesign::Sketch exists as a type"
except Exception:
    pass
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5))
sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
sk.addGeometry(Part.ArcOfCircle(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), 0, 1))
doc.recompute()
assert len(sk.Geometry) == 3
try:
    Sketcher.Circle
    assert False, "Sketcher module carries geometry constructors"
except AttributeError:
    pass
""",
    ),
    Trap(
        claim='Body.addObject moves Body.Tip to the feature you just added. Capture the base feature before adding, or a dressup such as PartDesign::Fillet will end up referencing itself and fail to recompute.',
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()
assert body.Tip.Name == pad.Name, "Body.Tip did not move to the feature just added"
""",
    ),
    Trap(
        claim="The constraint types this build has are Angle, Block, Coincident, Diameter, Distance, DistanceX, DistanceY, Equal, Horizontal, InternalAlignment, Parallel, Perpendicular, PointOnObject, Radius, SnellsLaw, Symmetric, Tangent, TangentViaPoint, Vertical and Weight. There is no 'Concentric': concentricity is Coincident between the two centres — Sketcher.Constraint('Coincident', a, 3, b, 3), where the position id is 0 for the edge itself, 1 start, 2 end, 3 centre. A type name this build does not have, or one of these at the wrong number of arguments, takes the FreeCAD core down inside the C++ constructor rather than raising, so there is no traceback to correct against.",
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5))
sk.addGeometry(Part.Circle(App.Vector(20, 0, 0), App.Vector(0, 0, 1), 5))
Sketcher.Constraint("Concentric", 0, 3, 1, 3)
""",
    ),
    Trap(
        claim='A material is assigned as obj.ShapeMaterial = Materials.MaterialManager().getMaterial(uuid). The property is a Materials::PropertyMaterial and takes a Materials.Material and nothing else. App.Material is the old appearance-only material and has no Name or Density. A Materials.Material cannot be built, subscripted or written to from here, so a density is chosen by choosing the card that already carries it — call the materials Tool for the uuid rather than guessing one.',
        script="""
box = doc.addObject("Part::Box", "Box")
mgr = Materials.MaterialManager()
uuid = next(iter(mgr.Materials.keys()))
mat = mgr.getMaterial(uuid)
box.ShapeMaterial = mat
assert box.ShapeMaterial.Name
old = App.Material()
try:
    old.Name
    assert False, "the old App.Material carries a Name"
except AttributeError:
    pass
try:
    old.Density
    assert False, "the old App.Material carries a Density"
except AttributeError:
    pass
""",
    ),
    Trap(
        claim='A thread is PartDesign::AdditiveHelix sweeping a profile along a helical path, or PartDesign::Hole with Threaded set; this build has both, and PartDesign::Helix and PartDesign::AdditivePipe besides. Lofting one V-section per turn builds a stack of flats that is not a helical surface, and nothing downstream reports it.',
        script="""
types = doc.supportedTypes()
for t in ("PartDesign::AdditiveHelix", "PartDesign::Hole", "PartDesign::Helix", "PartDesign::AdditivePipe"):
    assert t in types, t
hole = doc.addObject("PartDesign::Hole", "Hole")
assert hasattr(hole, "Threaded")
doc.removeObject(hole.Name)
""",
    ),
    Trap(
        claim="A Pocket travels along the *negative* of its sketch normal, and a Pad along the positive, unless Reversed is True. A Pocket whose sketch normal points into the material therefore cuts away from the solid, meets nothing, and hands back the shape it was given — and FreeCAD reports it Up-to-date and Valid, because a Cut with nothing to cut is not an error. Check the sign against the Body's BoundBox: the sketch sits on the face the cut starts from with its normal pointing out of the material, or it carries Reversed=True. For a hole that goes through, Type='ThroughAll' says so and cannot run short or run the wrong way, where Length=bb.YLength is a distance in a direction you have to have got right.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
skpad = doc.addObject("Sketcher::SketchObject", "SPad")
skpad.AttachmentSupport = [(xyplane, "")]
skpad.MapMode = "FlatFace"
skpad.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(skpad)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = skpad
pad.Length = 10
body.addObject(pad)
doc.recompute()
before = pad.Shape.Volume

top_index = None
for i, f in enumerate(pad.Shape.Faces):
    if abs(f.CenterOfMass.z - 10) < 1e-6:
        top_index = i + 1
        break

sk2 = doc.addObject("Sketcher::SketchObject", "SPocket")
sk2.AttachmentSupport = [(pad, "Face%d" % top_index)]
sk2.MapMode = "FlatFace"
sk2.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
body.addObject(sk2)
sk2.AttachmentOffset = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(1, 0, 0), 180))
doc.recompute()

pocket = doc.addObject("PartDesign::Pocket", "Pocket")
pocket.Profile = sk2
pocket.Length = 5
pocket.Reversed = False
body.addObject(pocket)
doc.recompute()
assert pocket.Shape.Volume == before, "flipped normal, Reversed=False cut material after all"
pocket.Reversed = True
doc.recompute()
assert pocket.Shape.Volume < before, "Reversed=True did not fix a Pocket cutting the wrong way"
""",
    ),
    Trap(
        claim="PartDesign::Fillet with UseAllEdges set rounds every edge of the base feature, which is a different part from one with the edges the drawing marks rounded. Where only some edges are meant, select them by computing which — off face normals, edge length, the plane an edge lies in — and pass that list in Base. None of those three tells a rim's inner edges from its outer ones — a slot's hole rim and the plate's own edge share length, plane and z. Only the two faces either side of an edge say which way a fillet on it pulls, and Preflight refuses a Base that mixes them, naming the edges it will not carry together.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(40, 0, 0)))
sk.addGeometry(Part.LineSegment(App.Vector(40, 0, 0), App.Vector(40, 40, 0)))
sk.addGeometry(Part.LineSegment(App.Vector(40, 40, 0), App.Vector(0, 40, 0)))
sk.addGeometry(Part.LineSegment(App.Vector(0, 40, 0), App.Vector(0, 0, 0)))
sk.addGeometry(Part.Circle(App.Vector(20, 20, 0), App.Vector(0, 0, 1), 6.366197723675814))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()

def top_edge(length, is_outer):
    for i, e in enumerate(pad.Shape.Edges):
        if abs(e.CenterOfMass.z - 10) < 1e-6 and abs(e.Length - length) < 1e-3:
            outer = e.CenterOfMass.distanceToPoint(App.Vector(20, 20, 10)) > 15
            if outer == is_outer:
                return "Edge%d" % (i + 1)
    return None

outer_edge = top_edge(40, True)
hole_edge = top_edge(40, False)
assert outer_edge and hole_edge, "the plate's outer rim and the hole's rim do not share a same-length, same-plane edge to test with"

fillet = doc.addObject("PartDesign::Fillet", "Fillet")
fillet.Base = (pad, [outer_edge, hole_edge])
body.addObject(fillet)
doc.recompute()
""",
    ),
    Trap(
        claim="Shape.BoundBox is taken off the shape's triangulation, so on a curved face it reports the tessellation and not the surface — and lands inside the true extreme, by the sagitta of whatever chord the mesh happened to use. The BoundBox of a pad with R25 rounded ends read XMin=0.182278 where the face is tangent at 0, and the five sketches placed at that XMin each built their feature 0.18 mm off, in a Document that reopens with XMin=0.0 and cannot be made to show it again. Read it for the sign of a direction and for which side of the solid you are on; never for a coordinate a feature is placed at. Shape.optimalBoundingBox(False) is exact where a box is genuinely what is wanted — its default triangulates and inherits the same tessellation padding as BoundBox once the shape carries a mesh — and the face that carries the number — Surface.Center, Surface.Radius, a Vertex Point — is better still.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 25))
body.addObject(sk)
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()

shape = pad.Shape.copy()
shape.tessellate(1.0)
assert shape.BoundBox.YMax < 25.0 - 1e-3, "BoundBox no longer reads off the triangulation"
assert abs(shape.optimalBoundingBox(False).YMax - 25.0) < 1e-6
faces = [f for f in pad.Shape.Faces if type(f.Surface).__name__ == "Cylinder"]
assert abs(faces[0].Surface.Radius - 25.0) < 1e-9
""",
    ),
    Trap(
        claim="App.Vector's methods are not all alike about the vector they are called on: multiply, normalize, scale and projectToPlane change it in place and hand it back, while add, sub and cross return a new one and leave it alone. So v.multiply(k) is not v scaled into a result, it is v scaled, and every later reader of v gets the scaled one. A recorded run's acceptance checks wrote unit(v) as v.multiply(1.0/n) and projected with zdir.multiply(dot(raw, zdir)): the projection zeroed zdir where the dot was 0 and left xdir 51.5 times too long, and 28 of 34 entries came back unmet on a part that met them. Where a vector is needed again, do the arithmetic with the operators — v * k, a - b, a + b — or build a new App.Vector; never call one of the in-place methods on a vector something else still reads.",
        script="""
v = App.Vector(3, 0, 0)
w = v.normalize()
assert v.Length == 1, "normalize did not change v in place"
assert w.Length == 1

u = App.Vector(2, 0, 0)
r = u.multiply(3)
assert u == App.Vector(6, 0, 0), "multiply did not change u in place"
assert r == u

a = App.Vector(1, 0, 0)
b = App.Vector(0, 1, 0)
c = a.add(b)
assert a == App.Vector(1, 0, 0), "add changed a in place, expected it to leave a alone"
assert c == App.Vector(1, 1, 0)

x = App.Vector(1, 0, 0)
y = App.Vector(0, 1, 0)
s = x - y
assert x == App.Vector(1, 0, 0), "subtraction operator changed its left operand"
assert s == App.Vector(1, -1, 0)
""",
    ),
    Trap(
        claim="Body.addObject hands back a *list* of what it added, not the object. `sk = body.addObject(doc.addObject('Sketcher::SketchObject','S'))` therefore binds a list, and the next line — sk.AttachmentSupport, sk.addGeometry — dies on 'list' object has no attribute. Add in one statement and keep the object in another: `sk = doc.addObject(...)` then `body.addObject(sk)`.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
added = body.addObject(doc.addObject("Sketcher::SketchObject", "S"))
assert isinstance(added, list), "Body.addObject stopped returning a list"
try:
    added.AttachmentSupport
    assert False, "a list unexpectedly exposes AttachmentSupport"
except AttributeError:
    pass
""",
    ),
    Trap(
        claim='A sketched curve that is not a line, an arc or a circle is a Part.BSplineCurve through points computed from its own equation — an involute flank, a cam profile, a cycloid. A chain of Part.LineSegment approximates it, and the approximation is what gets built and measured.',
        script="""
sk = doc.addObject("Sketcher::SketchObject", "S")
curve = Part.BSplineCurve()
curve.interpolate([App.Vector(0, 0, 0), App.Vector(10, 5, 0), App.Vector(20, 0, 0)])
n = sk.addGeometry(curve, False)
doc.recompute()
assert isinstance(sk.Geometry[n], Part.BSplineCurve)
""",
    ),
    Trap(
        claim="A PropertyEnumeration setter — Pocket.Type, Pad.Type, and others — raises ValueError on any string outside its own accepted list, and that list is per property, not a spelling shared across the module. Read the real set first with obj.getEnumerationsOfProperty('Type') and set one of those; a spelling that works on one enum property is not evidence it works on another.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()

sk2 = doc.addObject("Sketcher::SketchObject", "S2")
sk2.AttachmentSupport = [(pad, "Face3")]
sk2.MapMode = "FlatFace"
sk2.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
body.addObject(sk2)
doc.recompute()
pocket = doc.addObject("PartDesign::Pocket", "Pocket")
pocket.Profile = sk2
body.addObject(pocket)
doc.recompute()

valid_types = pocket.getEnumerationsOfProperty("Type")
assert "ThroughAll" in valid_types
try:
    pocket.Type = "NotARealType"
    assert False, "an invalid Type string was accepted"
except ValueError:
    pass
pocket.Type = "ThroughAll"
doc.recompute()
""",
    ),
    Trap(
        claim="PartDesign::Fillet fails on a radius the material cannot carry with 'BRep_API: command not done', a message that names the failed OCC call and nothing about the radius that caused it. A full round needs less than half the thickness of what it rounds; check the radius against the Body's wall thickness before setting it, not after the recompute fails with no way back to why.",
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 4
body.addObject(pad)
doc.recompute()

top_edge = None
for i, e in enumerate(pad.Shape.Edges):
    if abs(e.CenterOfMass.z - 4) < 1e-6:
        top_edge = "Edge%d" % (i + 1)
        break

fillet = doc.addObject("PartDesign::Fillet", "Fillet")
fillet.Base = (pad, [top_edge])
fillet.Radius = 4.5
body.addObject(fillet)
doc.recompute()
""",
    ),
    Trap(
        claim='A Pad or Pocket sketch with more than one closed wire builds every wire — if the wires do not touch, the feature leaves the Body in more than one solid, and FreeCAD only reports it as a plain RuntimeError once the recompute runs, with no earlier signal. A second piece of geometry that does not touch the first belongs in its own Body, not a second wire in the same sketch.',
        script="""
body = doc.addObject("PartDesign::Body", "Body")
xyplane = [f for f in body.Origin.OriginFeatures if f.Name == "XY_Plane"][0]
sk = doc.addObject("Sketcher::SketchObject", "S")
sk.AttachmentSupport = [(xyplane, "")]
sk.MapMode = "FlatFace"
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5))
sk.addGeometry(Part.Circle(App.Vector(50, 0, 0), App.Vector(0, 0, 1), 5))
body.addObject(sk)
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
body.addObject(pad)
doc.recompute()
""",
    ),
]

RETIRED_TRAPS = (
    (
        'There is no active Body outside the GUI. ActiveObject, setActiveObject and activeView do not exist on App.Document; nothing needs activating, Body.addObject is what puts a feature in a Body.',
        'doc.ActiveObject exists on App.Document and returns None headless; only setActiveObject and activeView are actually absent. core.lint already rejects all four ActiveObject/setActiveObject/activeView/getActiveObject spellings outright, so nothing the trap defended is left unguarded.',
    ),
)

TRAPS = tuple(trap.claim for trap in SEED_TRAPS)
