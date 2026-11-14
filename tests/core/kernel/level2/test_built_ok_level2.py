"""
Level 2 -- `built_ok` answers "did this feature recompute clean" for any
`DocumentObject`, where `isError()` does not.

`mm2024-phase1.jsonl` Step 4 built a rib-round sweep as a
`PartDesign::AdditivePipe` and asserted `not pipe.isError()`;
`PartDesign::AdditivePipe` has no `isError` in this build (neither does
`PartDesign::Pad`, `Fillet`, `AdditiveBox` or `AdditiveLoft` -- checked
directly), so the assert raised `AttributeError` instead of running. `Steps
cannot monkeypatch a method onto FreeCAD's C++-bound feature types, so the
kernel exposes `built_ok(obj)`, reading the same `obj.State` field its own
failure report already reads.
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


DESIGN = {"features": [{"id": "rib-centre", "thickness": {"value": "4 mm"}}]}


def with_design(code, design):
    """*code* preceded by the `design` name its Steps read."""
    features = design["features"]
    if isinstance(features, list):
        features = {feature["id"]: feature for feature in features}
    return "design = %r\n" % ({**design, "features": features},) + code


A_RIB_PAD = r"""
body = doc.addObject('PartDesign::Body', 'Body')
xz = doc.getObject('XZ_Plane')
ribsk = doc.addObject('Sketcher::SketchObject', 'RibSketch')
body.addObject(ribsk)
ribsk.Label = 'RibProfile'
ribsk.AttachmentSupport = [(xz, '')]
ribsk.MapMode = 'FlatFace'
P = [App.Vector(5.632, -2, 0), App.Vector(52.0, -2, 0), App.Vector(52.0, 13.559, 0), App.Vector(5.632, -1.897, 0)]
for i in range(4):
    ribsk.addGeometry(Part.LineSegment(P[i], P[(i + 1) % 4]), False)
doc.recompute()
pad = doc.addObject('PartDesign::Pad', 'RibPad')
pad.Profile = ribsk
pad.Label = 'Rib'
pad.SideType = 'Symmetric'
pad.Length = 4
body.addObject(pad)
doc.recompute()
assert len(body.Shape.Solids) == 1, 'rib pad left %d solids' % len(body.Shape.Solids)
"""

STEP_4_WITH_ISERROR = r"""
import math
body = doc.getObject('Body')
rib = design["features"]["rib-centre"]
thk = float(rib['thickness']['value'].split()[0])
r = thk / 2.0
ribsk = next(o for o in doc.Objects if o.Label == 'RibProfile')
segs = [g for g in ribsk.Geometry if isinstance(g, Part.LineSegment)]
top = max(segs, key=lambda g: 0.5 * (g.StartPoint.y + g.EndPoint.y))
a_l, b_l = top.StartPoint, top.EndPoint
if a_l.x > b_l.x:
    a_l, b_l = b_l, a_l
pl = ribsk.Placement
p1 = pl.multVec(App.Vector(a_l.x, a_l.y, 0.0))
p2 = pl.multVec(App.Vector(b_l.x, b_l.y, 0.0))

path = doc.addObject('Sketcher::SketchObject', 'RibPathSk')
body.addObject(path)
path.Label = 'RibPath'
path.AttachmentSupport = ribsk.AttachmentSupport
path.MapMode = ribsk.MapMode
path.AttachmentOffset = ribsk.AttachmentOffset
path.addGeometry(Part.LineSegment(App.Vector(a_l.x, a_l.y, 0), App.Vector(b_l.x, b_l.y, 0)), False)
doc.recompute()
pedges = path.Shape.Edges
if len(pedges) != 1:
    raise RuntimeError('rib sweep path must be 1 edge, found %d' % len(pedges))
spine_name = 'Edge1'
gs, ge = pedges[0].Vertexes[0].Point, pedges[0].Vertexes[-1].Point
assert (gs - p1).Length < 1e-6 or (ge - p1).Length < 1e-6, 'path sketch start %s not at %s' % (gs, p1)

prof = doc.addObject('Sketcher::SketchObject', 'RibRoundProfileSk')
body.addObject(prof)
prof.Label = 'RibRoundProfile'
d = p2 - p1
d = App.Vector(d.x / d.Length, d.y / d.Length, d.z / d.Length)
prof.Placement = App.Placement(p1, App.Rotation(App.Vector(0, 0, 1), d))
prof.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), r), False)
doc.recompute()

pipe = doc.addObject('PartDesign::AdditivePipe', 'RibRoundSweep')
pipe.Label = 'RibRoundSweep'
pipe.Profile = prof
pipe.Spine = (path, [spine_name])
pipe.Mode = 'Frenet'
body.addObject(pipe)
doc.recompute()
assert not pipe.isError(), 'sweep failed to recompute'
sh = body.Shape
assert len(sh.Solids) == 1, 'body left in %d solids' % len(sh.Solids)
cyls = [f for f in sh.Faces if hasattr(f.Surface, 'Radius') and abs(f.Surface.Radius - r) < 1e-6]
assert len(cyls) >= 1, 'no R%.1f swept face found on the body' % r
print('solids', len(sh.Solids))
"""

STEP_4_RIB_ROUND_SWEEP = r"""
import math
body = doc.getObject('Body')
rib = design["features"]["rib-centre"]
thk = float(rib['thickness']['value'].split()[0])
r = thk / 2.0
ribsk = next(o for o in doc.Objects if o.Label == 'RibProfile')
segs = [g for g in ribsk.Geometry if isinstance(g, Part.LineSegment)]
top = max(segs, key=lambda g: 0.5 * (g.StartPoint.y + g.EndPoint.y))
a_l, b_l = top.StartPoint, top.EndPoint
if a_l.x > b_l.x:
    a_l, b_l = b_l, a_l
pl = ribsk.Placement
p1 = pl.multVec(App.Vector(a_l.x, a_l.y, 0.0))
p2 = pl.multVec(App.Vector(b_l.x, b_l.y, 0.0))

path = doc.addObject('Sketcher::SketchObject', 'RibPathSk')
body.addObject(path)
path.Label = 'RibPath'
path.AttachmentSupport = ribsk.AttachmentSupport
path.MapMode = ribsk.MapMode
path.AttachmentOffset = ribsk.AttachmentOffset
path.addGeometry(Part.LineSegment(App.Vector(a_l.x, a_l.y, 0), App.Vector(b_l.x, b_l.y, 0)), False)
doc.recompute()
pedges = path.Shape.Edges
if len(pedges) != 1:
    raise RuntimeError('rib sweep path must be 1 edge, found %d' % len(pedges))
spine_name = 'Edge1'
gs, ge = pedges[0].Vertexes[0].Point, pedges[0].Vertexes[-1].Point
assert (gs - p1).Length < 1e-6 or (ge - p1).Length < 1e-6, 'path sketch start %s not at %s' % (gs, p1)

prof = doc.addObject('Sketcher::SketchObject', 'RibRoundProfileSk')
body.addObject(prof)
prof.Label = 'RibRoundProfile'
d = p2 - p1
d = App.Vector(d.x / d.Length, d.y / d.Length, d.z / d.Length)
prof.Placement = App.Placement(p1, App.Rotation(App.Vector(0, 0, 1), d))
prof.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), r), False)
doc.recompute()

pipe = doc.addObject('PartDesign::AdditivePipe', 'RibRoundSweep')
pipe.Label = 'RibRoundSweep'
pipe.Profile = prof
pipe.Spine = (path, [spine_name])
pipe.Mode = 'Frenet'
body.addObject(pipe)
doc.recompute()
assert built_ok(pipe), 'sweep failed to recompute'
sh = body.Shape
assert len(sh.Solids) == 1, 'body left in %d solids' % len(sh.Solids)
cyls = [f for f in sh.Faces if hasattr(f.Surface, 'Radius') and abs(f.Surface.Radius - r) < 1e-6]
assert len(cyls) >= 1, 'no R%.1f swept face found on the body' % r
print('solids', len(sh.Solids))
"""


def test_a_step_that_calls_isError_on_an_additive_pipe_raises_attributeerror(kernel):
    setup = kernel.rehearse(with_design(A_RIB_PAD, DESIGN))
    assert setup.ok, setup.traceback

    result = kernel.rehearse(with_design(STEP_4_WITH_ISERROR, DESIGN))

    assert not result.ok
    assert "AttributeError" in result.traceback
    assert "isError" in result.traceback


def test_the_same_step_using_built_ok_recomputes_clean(kernel):
    setup = kernel.rehearse(with_design(A_RIB_PAD, DESIGN))
    assert setup.ok, setup.traceback

    result = kernel.rehearse(with_design(STEP_4_RIB_ROUND_SWEEP, DESIGN))

    assert result.ok, result.traceback
