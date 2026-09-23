"""
Level 2 -- the boss-foot and rib-flank fillets from `mm2024-phase1` attempt 2
(`logs/mm24-smoke-postepic/opus/mm2024-phase1.attempt2.jsonl`), replayed
against a real kernel from Steps 1-8 of the recorded Plan's own geometry.
The rib-flank pair refuses at every radius down to R0.1 -- the rib is 4 mm
thick and its flank fillets meet the swept round with no material left. The
boss-foot arc is geometrically buildable, but only selected together with
Step 8's edges in one `PartDesign::Fillet`; as its own sequential Step it
refuses the same way the recorded run found, and the kernel already names
the conflicting feature and edge instead of the bare OCC exception.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _v(value):
    return {"value": value}


DESIGN = {"features": [
    {"id": "pad-outer", "length": _v("80 mm"), "width": _v("30 mm"), "thickness": _v("5 mm")},
    {"id": "fillet-outer", "radius": _v("R8")},
    {"id": "boss-centre", "outer_diameter": _v("\u00d820"), "height": _v("20 mm"),
     "position": {"the left end face": _v("60 mm")}},
    {"id": "lug-left", "outer_diameter": _v("\u00d820"), "depth": _v("30 mm"),
     "position": {"the left end face": _v("20 mm"), "the top face": _v("10 mm")}},
    {"id": "rib-centre", "thickness": _v("4 mm"),
     "position": {"the left end face": _v("5.632 mm")}, "height": _v("15 mm")},
    {"id": "fillet-rib-upper", "radius": _v("R2")},
    {"id": "hole-centre", "diameter": _v("\u00d815"),
     "position": {"the left end face": _v("60 mm")}},
    {"id": "hole-left", "diameter": _v("\u00d810"),
     "position": {"the left end face": _v("20 mm"), "the top face": _v("10 mm")}},
    {"id": "fillet-inner", "radius": _v("R2")},
    {"id": "fillet-centre", "radius": _v("R2")},
    {"id": "fillet-upper", "radius": _v("R2")},
]}

STEP_1_PLATE = r"""
import re, math
def num(s):
    return float(re.search(r'[-+]?\d*\.?\d+', s).group())

feat = {f['id']: f for f in design['features']}
pad_d = feat['pad-outer']
L = num(pad_d['length']['value'])
W = num(pad_d['width']['value'])
T = num(pad_d['thickness']['value'])
R = num(feat['fillet-outer']['radius']['value'])
print(L, W, T, R)

body = doc.addObject('PartDesign::Body', 'Body')
body.Label = 'PlateBody'
xy = [o for o in body.Group if o.isDerivedFrom('App::Origin')][0] if False else None
origin = body.Origin
xy_plane = [o for o in origin.OriginFeatures if o.Role == 'XY_Plane'][0]

sk = doc.addObject('Sketcher::SketchObject', 'Sketch')
sk.Label = 'PlateProfile'
body.addObject(sk)
sk.AttachmentSupport = [(xy_plane, '')]
sk.MapMode = 'FlatFace'

hy = W / 2.0
cy = hy - R
cx0 = R
cx1 = L - R
V = App.Vector
# straight runs
sk.addGeometry(Part.LineSegment(V(cx0, -hy, 0), V(cx1, -hy, 0)), False)
sk.addGeometry(Part.LineSegment(V(cx1, hy, 0), V(cx0, hy, 0)), False)
sk.addGeometry(Part.LineSegment(V(0, cy, 0), V(0, -cy, 0)), False)
sk.addGeometry(Part.LineSegment(V(L, -cy, 0), V(L, cy, 0)), False)
# corner arcs (CCW ordering of the loop)
sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(cx1, -cy, 0), V(0,0,1), R), math.radians(270), math.radians(360)), False)
sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(cx1, cy, 0), V(0,0,1), R), math.radians(0), math.radians(90)), False)
sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(cx0, cy, 0), V(0,0,1), R), math.radians(90), math.radians(180)), False)
sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(cx0, -cy, 0), V(0,0,1), R), math.radians(180), math.radians(270)), False)
doc.recompute()

pad = doc.addObject('PartDesign::Pad', 'Pad')
pad.Label = 'Plate'
body.addObject(pad)
pad.Profile = sk
pad.Length = T
pad.Reversed = True
doc.recompute()

assert len(sk.Shape.Wires) == 1, 'profile made %d wires, wanted 1' % len(sk.Shape.Wires)
assert len(body.Shape.Solids) == 1, 'body has %d solids, wanted 1' % len(body.Shape.Solids)
expected = (L*W - 4*(R*R - math.pi*R*R/4.0)) * T
assert abs(body.Shape.Volume - expected) < 1e-3, 'plate volume %.4f, wanted %.4f' % (body.Shape.Volume, expected)

cyls = [f for f in body.Shape.Faces if hasattr(f.Surface, 'Radius') and abs(f.Surface.Radius - R) < 1e-7]
assert len(cyls) == 4, 'found %d R%g corner faces, wanted 4' % (len(cyls), R)
zs = sorted(set(round(f.Surface.Position.z, 6) for f in body.Shape.Faces
               if f.Surface.TypeId == 'Part::GeomPlane' and abs(abs(f.Surface.Axis.z) - 1.0) < 1e-9))
assert zs == [-T, 0.0], 'horizontal faces at z=%s, wanted [%g, 0.0]' % (zs, -T)
print('volume', body.Shape.Volume, 'faces z', zs)
"""

STEP_2_BOSS = r"""
import math
body = doc.getObject("Body")
xy = doc.getObject("XY_Plane")
boss = next(f for f in design["features"] if f["id"]=="boss-centre")
od = float(boss["outer_diameter"]["value"].replace("\u00d8",""))
h = float(boss["height"]["value"].split()[0])
x = float(boss["position"]["the left end face"]["value"].split()[0])
sk = doc.addObject("Sketcher::SketchObject","BossSketch")
body.addObject(sk)
sk.Label = "BossProfile"
sk.AttachmentSupport = [(xy, "")]
sk.MapMode = "FlatFace"
doc.recompute()
sk.addGeometry(Part.Circle(App.Vector(x, 0, 0), App.Vector(0,0,1), od/2.0), False)
pad = doc.addObject("PartDesign::Pad","BossPad")
pad.Profile = sk
pad.Length = h
pad.Reversed = False
body.addObject(pad)
pad.Label = "Boss"
doc.recompute()
assert not body.Shape.isNull(), "body shape null"
assert len(body.Shape.Solids)==1, "body in %d solids" % len(body.Shape.Solids)
cyls = [f for f in body.Shape.Faces if f.Surface.__class__.__name__=="Cylinder" and abs(f.Surface.Radius-od/2.0)<1e-9]
assert cyls, "no boss cylinder of radius %.3f" % (od/2.0)
top = max(v.Point.z for v in cyls[0].Vertexes)
assert abs(top - h) < 1e-6, "boss top z %.4f, wanted %.4f" % (top, h)
c = cyls[0].Surface.Center
assert abs(c.x-x)<1e-6 and abs(c.y)<1e-6, "boss centre (%.4f,%.4f), wanted (%.4f,0)" % (c.x,c.y,x)
print("ok", top, c)
"""

STEP_3_LUG = r"""
import re, math
feat = {f["id"]: f for f in design["features"]}
lug = feat["lug-left"]
def num(s):
    return float(re.findall(r"[-+]?\d*\.?\d+", s)[0])
d_lug = num(lug["outer_diameter"]["value"])
r_lug = d_lug / 2.0
depth = num(lug["depth"]["value"])
x_axis = num(lug["position"]["the left end face"]["value"])
z_below = num(lug["position"]["the top face"]["value"])
z_axis = -z_below
print(d_lug, depth, x_axis, z_axis)

body = doc.getObject("Body")
xz = [o for o in doc.Objects if o.Name == "XZ_Plane"][0]

sk = doc.addObject("Sketcher::SketchObject", "LugSketch")
body.addObject(sk)
sk.Label = "LugProfile"
sk.AttachmentSupport = [(xz, "")]
sk.MapMode = "FlatFace"

c = App.Vector(x_axis, z_axis, 0)
circ = Part.Circle(c, App.Vector(0, 0, 1), r_lug)
sk.addGeometry(Part.ArcOfCircle(circ, math.pi, 2*math.pi), False)
sk.addGeometry(Part.LineSegment(App.Vector(x_axis + r_lug, z_axis, 0), App.Vector(x_axis + r_lug, 0, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(x_axis + r_lug, 0, 0), App.Vector(x_axis - r_lug, 0, 0)), False)
sk.addGeometry(Part.LineSegment(App.Vector(x_axis - r_lug, 0, 0), App.Vector(x_axis - r_lug, z_axis, 0)), False)
doc.recompute()

pad = doc.addObject("PartDesign::Pad", "LugPad")
pad.Label = "Lug"
pad.Profile = sk
pad.SideType = "Symmetric"
pad.Length = depth
body.addObject(pad)
doc.recompute()

assert "Invalid" not in pad.State, "LugPad invalid: %s" % pad.State
assert len(body.Shape.Solids) == 1, "body left in %d solids after lug pad" % len(body.Shape.Solids)
cyls = [f for f in body.Shape.Faces if f.Surface.__class__.__name__ == "Cylinder"
        and abs(f.Surface.Radius - r_lug) < 1e-9 and abs(f.Surface.Axis.y) > 0.9]
assert len(cyls) == 1, "expected 1 lug cylindrical face, found %d" % len(cyls)
s = cyls[0].Surface
low = s.Center.z - s.Radius
assert abs(low - (z_axis - r_lug)) < 1e-9, "lug lowest point z=%.4f, wanted %.4f" % (low, z_axis - r_lug)
print("ok", s.Center, low, len(body.Shape.Solids))
"""

STEP_4_RIB = r"""
import re
def num(s):
    return float(re.search(r"[-+]?[0-9]*\.?[0-9]+", s).group())
rib = next(f for f in design["features"] if f["id"] == "rib-centre")
t = num(rib["thickness"]["value"])
print(t, rib["position"]["the left end face"]["value"], rib["height"]["value"])
body = next(o for o in doc.Objects if o.Label == "PlateBody")
xz = next(o for o in doc.Objects if o.Label == "XZ-plane")
sk = doc.addObject("Sketcher::SketchObject", "RibSketch")
body.addObject(sk)
sk.Label = "RibProfile"
sk.AttachmentSupport = [(xz, "")]
sk.MapMode = "FlatFace"
P = [App.Vector(5.632, -2, 0), App.Vector(52.0, -2, 0), App.Vector(52.0, 13.559, 0), App.Vector(5.632, -1.897, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(P[i], P[(i+1) % 4]), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i+1) % 4, 1))
doc.recompute()
pad = doc.addObject("PartDesign::Pad", "RibPad")
pad.Profile = sk
pad.Label = "Rib"
pad.SideType = "Symmetric"
pad.Length = t
body.addObject(pad)
doc.recompute()
assert not pad.Shape.isNull(), "rib pad shape is null"
assert len(body.Shape.Solids) == 1, "rib pad left %d solids" % len(body.Shape.Solids)
ys = [v.Point.y for v in body.Shape.Vertexes]
print("ok", pad.Length.Value, min(ys), max(ys))
"""

STEP_5_RIB_ROUND = r"""
import math
body = doc.getObject('Body')
ribsk = next(o for o in body.Group if o.Label == 'RibProfile')
ribpad = next(o for o in body.Group if o.Label == 'Rib')
xz = ribsk.AttachmentSupport[0][0]

# the rib's dropped top line: the only line whose endpoints both stand above the base level
lines = [g for g in ribsk.Geometry if isinstance(g, Part.LineSegment)]
ybase = min(min(g.StartPoint.y, g.EndPoint.y) for g in lines)
top = [g for g in lines if g.StartPoint.y > ybase + 1e-6 and g.EndPoint.y > ybase + 1e-6]
if len(top) != 1:
    raise RuntimeError('expected 1 rib top line, found %d' % len(top))
top = top[0]
p = [top.StartPoint, top.EndPoint]
p.sort(key=lambda v: v.x)
lo, hi = p[0], p[1]   # sketch-local (x=global X, y=global Z)
start = App.Vector(lo.x, 0.0, lo.y)
end = App.Vector(hi.x, 0.0, hi.y)
print('path', start, end)

rib = next(f for f in design['features'] if f['id'] == 'fillet-rib-upper')
rad = float(rib['radius']['value'].replace('R', '').strip())
ribthk = float(next(f for f in design['features'] if f['id'] == 'rib-centre')['thickness']['value'].split()[0])
assert abs(2.0 * rad - ribthk) < 1e-9, 'round diameter %.3f does not match rib thickness %.3f' % (2 * rad, ribthk)

# path sketch, attached to the same XZ plane the rib profile uses
path = doc.addObject('Sketcher::SketchObject', 'RibTopPath')
body.addObject(path)
path.Label = 'RibTopPath'
path.AttachmentSupport = [(xz, '')]
path.MapMode = 'FlatFace'
path.addGeometry(Part.LineSegment(App.Vector(lo.x, lo.y, 0), App.Vector(hi.x, hi.y, 0)), False)
doc.recompute()

# profile sketch: circle of radius 2 in the plane normal to the path, centred on its start
dirv = end - start
dirv = App.Vector(dirv.x / dirv.Length, dirv.y / dirv.Length, dirv.z / dirv.Length)
prof = doc.addObject('Sketcher::SketchObject', 'RibRoundProfile')
body.addObject(prof)
prof.Label = 'RibRoundProfile'
prof.MapMode = 'Deactivated'
prof.Placement = App.Placement(start, App.Rotation(App.Vector(0, 0, 1), dirv))
prof.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), rad), False)
doc.recompute()
print('prof normal', prof.Placement.Rotation.multVec(App.Vector(0, 0, 1)), 'dir', dirv)

# spine edge name computed from the path sketch's own geometry
names = ['Edge%d' % (i + 1) for i, e in enumerate(path.Shape.Edges)]
if len(names) != 1:
    raise RuntimeError('path sketch built %d edges, expected 1' % len(names))

pipe = doc.addObject('PartDesign::AdditivePipe', 'RibTopRound')
pipe.Label = 'RibTopRound'
pipe.Profile = prof
pipe.Spine = (path, names)
pipe.Mode = 'Frenet'
body.addObject(pipe)
doc.recompute()

if pipe.State and 'Invalid' in pipe.State:
    raise RuntimeError('AdditivePipe invalid: %s' % pipe.State)
assert len(body.Shape.Solids) == 1, 'body left in %d solids' % len(body.Shape.Solids)
cyls = [f for f in body.Shape.Faces if hasattr(f.Surface, 'Radius') and abs(f.Surface.Radius - rad) < 1e-6]
assert len(cyls) >= 1, 'no R%.1f swept face found on the body' % rad
print('ok', pipe.Name, body.Shape.Volume, len(body.Shape.Solids), len(cyls))
"""

STEP_6_BORE = r"""
import re
body = doc.getObject('Body')
hc = next(f for f in design['features'] if f['id'] == 'hole-centre')
dia = float(re.search(r'[\d.]+', hc['diameter']['value']).group())
bc = next(f for f in design['features'] if f['id'] == 'boss-centre')
x_bore = float(re.search(r'[\d.]+', bc['position']['the left end face']['value']).group())

top = doc.getObject('XY_Plane')
sk = doc.addObject('Sketcher::SketchObject', 'BoreSketch')
body.addObject(sk)
sk.Label = 'BoreProfile'
sk.AttachmentSupport = [(top, '')]
sk.MapMode = 'FlatFace'
sk.addGeometry(Part.Circle(App.Vector(x_bore, 0, 0), App.Vector(0, 0, 1), dia / 2.0), False)
doc.recompute()

pk = doc.addObject('PartDesign::Pocket', 'Bore')
pk.Label = 'Bore'
pk.Profile = sk
pk.Type = 'ThroughAll'
pk.Midplane = True
body.addObject(pk)
doc.recompute()

cyls = [f for f in body.Shape.Faces
        if f.Surface.TypeId == 'Part::GeomCylinder'
        and abs(f.Surface.Radius - dia / 2.0) < 1e-6
        and abs(f.Surface.Center.x - x_bore) < 1e-6 and abs(f.Surface.Center.y) < 1e-6
        and abs(abs(f.Surface.Axis.z) - 1.0) < 1e-9]
assert cyls, "no vertical cylindrical face of radius %.3f at x=%.1f, y=0 in the body" % (dia / 2.0, x_bore)
zs = [v.Point.z for f in cyls for v in f.Vertexes]
assert abs(max(zs) - 20.0) < 1e-6 and abs(min(zs) + 5.0) < 1e-6, \
    "bore runs z %.3f..%.3f, wanted -5..+20" % (min(zs), max(zs))
assert len(body.Shape.Solids) == 1, "body is %d solids" % len(body.Shape.Solids)
print('bored', pk.Name, 'vol', body.Shape.Volume)
"""

STEP_7_LUG_BORE = r"""
import re, math
feat = {f['id']: f for f in design['features']}
hl = feat['hole-left']
def num(s):
    return float(re.search(r'[-+]?[0-9]*\.?[0-9]+', str(s)).group(0))
dia = num(hl['diameter']['value'])
x = num(hl['position']['the left end face']['value'])
zdrop = num(hl['position']['the top face']['value'])
lug_depth = num(feat['lug-left']['depth']['value'])
print('dia', dia, 'x', x, 'zdrop', zdrop, 'lug depth', lug_depth)

body = doc.getObject('Body')
xz = [o for o in doc.Objects if o.TypeId == 'App::Plane' and o.Label == 'XZ-plane'][0]

sk = doc.addObject('Sketcher::SketchObject', 'LugBoreSketch')
sk.Label = 'LugBoreProfile'
body.addObject(sk)
sk.AttachmentSupport = [(xz, '')]
sk.MapMode = 'FlatFace'
sk.addGeometry(Part.Circle(App.Vector(x, -zdrop, 0), App.Vector(0, 0, 1), dia / 2.0), False)
doc.recompute()

base = body.Tip
pk = doc.addObject('PartDesign::Pocket', 'LugBore')
pk.Label = 'LugBore'
pk.Profile = sk
pk.Type = 'ThroughAll'
pk.SideType = 'Symmetric'
body.addObject(pk)
doc.recompute()

assert body.Tip is pk, 'Body tip is %r, expected the LugBore pocket' % (body.Tip,)
assert len(body.Shape.Solids) == 1, 'LugBore left %d solids, expected 1' % len(body.Shape.Solids)
assert not body.Shape.isNull(), 'Body shape null after LugBore'
# the bore must be a through cylinder spanning the full lug depth
cyls = [f for f in body.Shape.Faces
        if hasattr(f.Surface, 'Radius') and abs(f.Surface.Radius - dia / 2.0) < 1e-6
        and abs(abs(f.Surface.Axis.y) - 1.0) < 1e-6
        and abs(f.Surface.Center.x - x) < 1e-6 and abs(f.Surface.Center.z + zdrop) < 1e-6]
assert len(cyls) >= 1, 'found %d cylindrical faces of R%.3f on the lug axis, expected at least 1' % (len(cyls), dia / 2.0)
bb = cyls[0].BoundBox
span = sum(c.BoundBox.YLength for c in cyls)
assert abs(span - lug_depth) < 1e-3, 'lug bore spans %.3f mm front to rear, wanted %.3f' % (span, lug_depth)
print('ok', body.Shape.Volume, len(cyls), span)
"""

STEP_8_FILLET_RIB_TO_BOSS = r"""
import re
body = doc.getObject('Body')
fi = next(f for f in design["features"] if f["id"] == "fillet-inner")
r = float(re.findall(r"[\d.]+", fi["radius"]["value"])[0])
base = body.Tip
shp = base.Shape

def planar_y(f, y):
    return f.Surface.TypeId == 'Part::GeomPlane' and abs(abs(f.Surface.Axis.y) - 1.0) < 1e-6 and abs(f.Surface.Position.y - y) < 1e-6

def boss_cyl(f):
    return (f.Surface.TypeId == 'Part::GeomCylinder' and abs(f.Surface.Radius - 10.0) < 1e-6
            and abs(abs(f.Surface.Axis.z) - 1.0) < 1e-6 and abs(f.Surface.Center.x - 60.0) < 1e-6)

rib_side = lambda f: planar_y(f, 2.0) or planar_y(f, -2.0)
names = edges_between(shp, rib_side, boss_cyl)
if len(names) != 2:
    raise RuntimeError("fillet-inner wants 2 rib-to-boss edges, found %d: %s" % (len(names), names))
fl = doc.addObject('PartDesign::Fillet', 'FilletRibBoss')
fl.Label = 'FilletRibBoss'
fl.Base = (base, names)
fl.Radius = r
body.addObject(fl)
doc.recompute()
print(fl.Radius, names, body.Shape.Volume, len(body.Shape.Solids))
assert len(body.Shape.Solids) == 1, "body split into %d solids" % len(body.Shape.Solids)
"""

STEP_9_FILLET_BOSS_FOOT = r"""
import re
body = next(o for o in doc.Objects if o.TypeId=='PartDesign::Body' and o.Label=='PlateBody')
fc = next(f for f in design['features'] if f['id']=='fillet-centre')
radius = float(re.search(r'[0-9.]+', fc['radius']['value']).group())
base = body.Tip
shape = base.Shape

def is_top(f):
    s = f.Surface
    return s.TypeId=='Part::GeomPlane' and abs(s.Position.z) < 1e-7 and abs(abs(s.Axis.z)-1.0) < 1e-7

def is_boss(f):
    s = f.Surface
    return (s.TypeId=='Part::GeomCylinder' and abs(s.Radius-10.0) < 1e-7
            and abs(abs(s.Axis.z)-1.0) < 1e-7 and abs(s.Center.x-60.0) < 1e-6)

edges = edges_between(shape, is_top, is_boss)
if len(edges) != 2:
    raise RuntimeError('boss foot selection found %d edges, expected the single foot arc as its 2 seam-split halves: %r' % (len(edges), edges))
groups = edges_by_concavity(shape, edges)
if len(groups) != 1:
    raise RuntimeError('boss foot edges are of mixed concavity: %r' % (groups,))
fil = doc.addObject('PartDesign::Fillet', 'FilletBossFoot')
body.addObject(fil)
fil.Label = 'FilletBossFoot'
fil.Base = (base, edges)
fil.Radius = radius
doc.recompute()
print(radius, edges, body.Shape.Volume, len(body.Shape.Solids))
assert len(body.Shape.Solids)==1, 'boss foot fillet left %d solids' % len(body.Shape.Solids)
"""

STEP_10_FILLET_RIB_FLANKS = r"""
import re
body = doc.getObject('Body')
base = body.Tip
shape = base.Shape
fu = next(f for f in design['features'] if f['id']=='fillet-upper')
rad = float(re.findall(r'[\d.]+', fu['radius']['value'])[0])
def top_face(f):
    return f.Surface.__class__.__name__=='Plane' and abs(abs(f.Surface.Axis.z)-1)<1e-6 and abs(f.Surface.Position.z)<1e-6
def rib_side(f):
    return f.Surface.__class__.__name__=='Plane' and abs(abs(f.Surface.Axis.y)-1)<1e-6 and abs(abs(f.Surface.Position.y)-2.0)<1e-6
edges = edges_between(shape, top_face, rib_side)
print(rad, edges)
if len(edges) != 2:
    raise RuntimeError('fillet-upper wants 2 rib-side-to-top-face edges, found %d: %s' % (len(edges), edges))
fil = doc.addObject('PartDesign::Fillet','FilletRibTop')
fil.Label = 'FilletRibTop'
fil.Base = (base, edges)
fil.Radius = rad
body.addObject(fil)
doc.recompute()
print(body.Shape.Volume, len(body.Shape.Solids), body.Tip.Name)
assert len(body.Shape.Solids)==1, 'body left in %d solids' % len(body.Shape.Solids)
"""


def _build_through_step_8(kernel):
    for step in (STEP_1_PLATE, STEP_2_BOSS, STEP_3_LUG, STEP_4_RIB, STEP_5_RIB_ROUND, STEP_6_BORE, STEP_7_LUG_BORE, STEP_8_FILLET_RIB_TO_BOSS,):
        result = kernel.check(step, design=DESIGN)
        assert result.ok, result.traceback


def test_boss_foot_fillet_refuses_naming_the_conflicting_feature(kernel):
    _build_through_step_8(kernel)

    result = kernel.check(STEP_9_FILLET_BOSS_FOOT, design=DESIGN)

    assert not result.ok
    assert "FilletRibBoss" in result.traceback, (
        "the refusal does not name the feature whose fillet consumed the boss-foot edges: %s"
        % result.traceback)
    assert "StdFail_NotDone" in result.traceback


def test_rib_flank_fillet_refuses_at_every_radius_naming_the_conflicting_feature(kernel):
    _build_through_step_8(kernel)

    for radius in (2.0, 1.5, 1.0, 0.5, 0.1):
        code = STEP_10_FILLET_RIB_FLANKS.replace(
            "rad = float(re.findall(r'[\\d.]+', fu['radius']['value'])[0])",
            "rad = %r" % radius)
        result = kernel.check(code, design=DESIGN)

        assert not result.ok, "R%s built where the recorded run found every radius refused" % radius
        assert "FilletRibBoss" in result.traceback, (
            "R%s's refusal does not name the conflicting feature: %s" % (radius, result.traceback))
        assert "StdFail_NotDone" in result.traceback
