"""
Classifies which fillet chains on a ring body's bore openings build cleanly
and which fail, given a fixed radius and an already-built base body. The
outside chains (Steps 27-30) and the bore chains (Steps 31-34) exclude each
other regardless of build order, a same-concavity edge mix across straight,
B-spline and circular edges builds only when Base is the Tip captured before
the Body retips itself, and an 18-edge boundary loop mixing convex and
concave edges is refused regardless of Base.

`check_step_27_and_31_exclude_each_other` also runs as a pytest test in
test_ring_fillet_reproduction.py; the rest of this module (the mixed-
concavity and event-16094 demonstrations) is exploratory and still run
directly: `python ring_fillet_classification.py` from a Python that
resolves `core.kernel.find_freecadcmd()`, e.g. the repo's own `.venv`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.kernel import Kernel, find_freecadcmd

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

HELPERS = """
def _isclose(a, b, tol=0.05):
    return abs(a - b) < tol
def plane_info(f):
    s = f.Surface
    if type(s).__name__ != 'Plane':
        return None
    return s.Axis, s.Position
def cyl_info(f):
    s = f.Surface
    if type(s).__name__ != 'Cylinder':
        return None
    return s.Axis, s.Radius, s.Center
def bb(f):
    return f.BoundBox
"""


def _load(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


def _build_steps_1_to_26(kernel, design, steps, fillet_1730_code):
    """Body ready for Step 27 onward: Steps 1-16 plus the base/hook fillets 17-26."""
    boot = kernel.check("doc.addObject('PartDesign::Body', 'Body')", design=design)
    assert boot.ok, boot.traceback
    for n in range(1, 17):
        result = kernel.check(steps[str(n)])
        assert result.ok, "Step %d failed: %s" % (n, result.traceback)
    base_and_hook = fillet_1730_code.split("def make_ring_predicates")[0]
    result = kernel.check(HELPERS + base_and_hook)
    assert result.ok, result.traceback
    return result


RING_PREDICATES_CODE = """
def make_ring_predicates(ring_x, x_lo, x_hi):
    def front0(f):
        info = plane_info(f)
        if info is None: return False
        axis, pos = info
        box = bb(f)
        return _isclose(abs(axis.x),1,0.05) and _isclose(pos.x, ring_x, 1) and box.YMin>=-0.5 and box.YMax<=40.5 and box.XMin>=x_lo and box.XMax<=x_hi
    def rear0(f):
        info = plane_info(f)
        if info is None: return False
        axis, pos = info
        box = bb(f)
        return _isclose(abs(axis.x),1,0.05) and _isclose(pos.x, ring_x, 1) and box.YMin>=59.5 and box.YMax<=100.5 and box.XMin>=x_lo and box.XMax<=x_hi
    def frontcap(f):
        info = plane_info(f)
        if info is None: return False
        axis, pos = info
        box = bb(f)
        return _isclose(abs(axis.y),1,0.05) and _isclose(pos.y, 40, 1) and box.XMin>=x_lo and box.XMax<=x_hi
    def rearcap(f):
        info = plane_info(f)
        if info is None: return False
        axis, pos = info
        box = bb(f)
        return _isclose(abs(axis.y),1,0.05) and _isclose(pos.y, 60, 1) and box.XMin>=x_lo and box.XMax<=x_hi
    def notch_lower(f):
        info = cyl_info(f)
        if info is None: return False
        axis, radius, center = info
        return _isclose(radius,20,0.5) and _isclose(abs(axis.z),1,0.05) and _isclose(abs(center.x-ring_x),20,2) and _isclose(center.z,0,1)
    def notch_upper(f):
        info = cyl_info(f)
        if info is None: return False
        axis, radius, center = info
        return _isclose(radius,20,0.5) and _isclose(abs(axis.z),1,0.05) and _isclose(abs(center.x-ring_x),20,2) and _isclose(center.z,50,1)
    def outer25(f):
        info = cyl_info(f)
        if info is None: return False
        axis, radius, center = info
        return _isclose(radius,25,0.3) and _isclose(center.x,ring_x,1)
    def bore20(f):
        info = cyl_info(f)
        if info is None: return False
        axis, radius, center = info
        return _isclose(radius,20,0.3) and _isclose(center.x,ring_x,1) and _isclose(abs(axis.y),1,0.05)
    def botface(f):
        info = plane_info(f)
        if info is None: return False
        axis, pos = info
        return _isclose(abs(axis.z),1,0.05) and _isclose(pos.z,0,0.5)
    def topface(f):
        info = plane_info(f)
        if info is None: return False
        axis, pos = info
        return _isclose(abs(axis.z),1,0.05) and _isclose(pos.z,50,0.5)
    return dict(front0=front0, rear0=rear0, frontcap=frontcap, rearcap=rearcap,
                notch_lower=notch_lower, notch_upper=notch_upper, outer25=outer25,
                bore20=bore20, bot=botface, top=topface)

def any_of(*fns):
    return lambda f: any(fn(f) for fn in fns)

def build_chain(body, base, opening, target, label):
    shape = base.Shape
    names = edges_between(shape, opening, target)
    fillet = doc.addObject('PartDesign::Fillet', label)
    body.addObject(fillet)
    fillet.Base = (base, names)
    fillet.Radius = 2.0
    doc.recompute()
    return names, built_ok(fillet), fillet

def try_build(RESULTS, body, label, opening, target):
    doc = body.Document
    base = body.Tip
    try:
        n, ok, f = build_chain(body, base, opening, target, label)
        if ok:
            RESULTS[label] = ('OK', len(n), ok, n)
        else:
            RESULTS[label] = ('FAIL', 'built with %d edges but left Invalid: %s' % (len(n), n))
            body.removeObject(f); doc.removeObject(f.Name); body.Tip = base; doc.recompute()
    except Exception as exc:
        RESULTS[label] = ('FAIL', str(exc).splitlines()[-1] if str(exc) else repr(exc))
        stray = doc.getObject(label)
        if stray is not None:
            if stray in body.Group:
                body.removeObject(stray)
            doc.removeObject(label)
        body.Tip = base
        doc.recompute()

left = make_ring_predicates(-50, -85, -55)
right = make_ring_predicates(50, 55, 85)
"""


def classify_steps_27_to_34(kernel, design, steps, fillet_1730_code, bore_first):
    """
    Rebuilds the base body, then attempts the 4 outside chains and the 4
    bore chains in the order *bore_first* selects. Returns `{label: result}`
    for all 8, `('OK', edge_count, built_ok, names)` or `('FAIL', message)`;
    a fresh Python namespace is built per `kernel.check()` call, only the
    FreeCAD document itself persists across calls.
    """
    _build_steps_1_to_26(kernel, design, steps, fillet_1730_code)

    outer = [
        ("27_out_L_lower", "any_of(left['front0'], left['notch_lower'], left['rearcap'])",
         "any_of(left['outer25'], left['bot'])"),
        ("28_out_L_upper", "any_of(left['frontcap'], left['notch_upper'], left['rear0'])",
         "any_of(left['outer25'], left['top'])"),
        ("29_out_R_lower", "any_of(right['front0'], right['notch_lower'], right['rearcap'])",
         "any_of(right['outer25'], right['bot'])"),
        ("30_out_R_upper", "any_of(right['frontcap'], right['notch_upper'], right['rear0'])",
         "any_of(right['outer25'], right['top'])"),
    ]
    bore = [
        ("31_bore_L_lower", "any_of(left['front0'], left['notch_lower'], left['rearcap'])", "left['bore20']"),
        ("32_bore_L_upper", "any_of(left['frontcap'], left['notch_upper'], left['rear0'])", "left['bore20']"),
        ("33_bore_R_lower", "any_of(right['front0'], right['notch_lower'], right['rearcap'])", "right['bore20']"),
        ("34_bore_R_upper", "any_of(right['frontcap'], right['notch_upper'], right['rear0'])", "right['bore20']"),
    ]
    order = bore + outer if bore_first else outer + bore

    lines = ["body = doc.getObject('Body')", "RESULTS = {}"]
    for label, opening_expr, target_expr in order:
        lines.append("try_build(RESULTS, body, %r, %s, %s)" % (label, opening_expr, target_expr))
    lines.append("print(RESULTS)")
    result = kernel.check(HELPERS + RING_PREDICATES_CODE + "\n" + "\n".join(lines))
    assert result.ok, result.traceback
    return eval(result.stdout.strip())


def reproduce_event_16094(kernel, design, steps):
    """
    A selection of 6 convex edges on the bore opening that mixes edge types
    (2 straight, 2 B-spline, 1 circle) fails when the fillet's Base is set
    to the Body, because `PartDesign::Body.addObject` retips the Body to the
    new, still-unbuilt feature before the assignment reads it. The same
    selection builds when Base is the Tip captured before
    `body.addObject(fillet)` runs.
    """
    boot = kernel.check("doc.addObject('PartDesign::Body', 'Body')", design=design)
    assert boot.ok, boot.traceback
    for n in range(1, 17):
        result = kernel.check(steps[str(n)])
        assert result.ok, "Step %d failed: %s" % (n, result.traceback)

    event_code = _load("ring_fillet_event_selector.json")["code"]
    selector = event_code.split("edge_objs = [(body, en) for en in convex_names]")[0]
    selector = selector.replace(
        "if len(convex_names) != 6:\n    raise RuntimeError('expected 6 convex edges on left bore opening, found %d: %s' % (len(convex_names), convex_names))\n\n",
        "",
    )

    with_body = kernel.check(selector + """
f = doc.addObject('PartDesign::Fillet', 'FilletAllBody')
body.addObject(f)
f.Base = (body, convex_names)
f.Radius = 2.0
doc.recompute()
print('with body:', built_ok(f))
""")
    with_tip = kernel.check(selector + """
tip = body.Tip
f = doc.addObject('PartDesign::Fillet', 'FilletAllTip')
body.addObject(f)
f.Base = (tip, convex_names)
f.Radius = 2.0
doc.recompute()
print('with tip captured before addObject:', built_ok(f), 'edges:', convex_names)
""")
    return with_body, with_tip


def demonstrate_step_35_mixed_concavity(kernel, design, steps, fillet_1730_code):
    """An 18-edge front boundary loop mixing convex and concave edges is refused as one fillet Base."""
    _build_steps_1_to_26(kernel, design, steps, fillet_1730_code)
    boundary = kernel.check(HELPERS + """
body = doc.getObject('Body')
base = body.Tip
shape = base.Shape
def is_front_outer(f):
    info = plane_info(f)
    if info is None: return False
    axis, pos = info
    box = bb(f)
    return abs(abs(axis.y)-1) < 0.01 and _isclose(pos.y, 0, 0.5) and (box.XMax-box.XMin) > 60
names = edges_between(shape, is_front_outer, lambda f: not is_front_outer(f))
fillet = doc.addObject('PartDesign::Fillet', 'FilletFront')
body.addObject(fillet)
fillet.Base = (base, names)
fillet.Radius = 2.0
doc.recompute()
""")
    return boundary


def build_step_35_split_by_concavity(kernel, design, steps, fillet_1730_code):
    """
    Splits Step 35's front boundary loop with `edges_by_concavity`, builds
    one `PartDesign::Fillet` per group in order (concave first), and reports
    each group's size and `built_ok`, plus the final Body's validity and
    solid count. The tangent transitional edges are expected to fall out of
    every group rather than into the convex one.
    """
    _build_steps_1_to_26(kernel, design, steps, fillet_1730_code)
    result = kernel.check(HELPERS + """
body = doc.getObject('Body')
base = body.Tip
shape = base.Shape
def is_front_outer(f):
    info = plane_info(f)
    if info is None: return False
    axis, pos = info
    box = bb(f)
    return abs(abs(axis.y)-1) < 0.01 and _isclose(pos.y, 0, 0.5) and (box.XMax-box.XMin) > 60
names = edges_between(shape, is_front_outer, lambda f: not is_front_outer(f))
groups = edges_by_concavity(shape, names)
GROUP_RESULTS = []
current_base = base
for i, group in enumerate(groups):
    fillet = doc.addObject('PartDesign::Fillet', 'FilletFrontGroup%d' % i)
    body.addObject(fillet)
    fillet.Base = (current_base, group)
    fillet.Radius = 2.0
    doc.recompute()
    GROUP_RESULTS.append((len(group), built_ok(fillet)))
    current_base = fillet
FINAL_SHAPE = body.Shape
print((len(names), GROUP_RESULTS, FINAL_SHAPE.isValid(), len(FINAL_SHAPE.Solids)))
""")
    assert result.ok, result.traceback
    return eval(result.stdout.strip())



def check_step_27_and_31_exclude_each_other():
    """
    Builds the ring's own left-lower opening and tries Step 27 (outer edge)
    and Step 31 (bore edge) in both orders, asserting that whichever one runs
    second on that opening cannot find an edge to fillet.
    """
    find_freecadcmd()
    design = _load("ring_fillet_design.json")
    steps = _load("ring_fillet_steps.json")
    fillet_1730_code = _load("ring_fillet_steps_17_30.json")["code"]

    with Kernel() as kernel:
        outer_first = classify_steps_27_to_34(kernel, design, steps, fillet_1730_code, bore_first=False)
    with Kernel() as kernel:
        bore_first = classify_steps_27_to_34(kernel, design, steps, fillet_1730_code, bore_first=True)

    outer_status, _count, outer_ok, _names = outer_first["27_out_L_lower"]
    bore_status, _bore_message = outer_first["31_bore_L_lower"]
    assert outer_status == "OK" and outer_ok, "Step 27 was expected to build first: %r" % (outer_first,)
    assert bore_status == "FAIL", "Step 31 was expected to fail once Step 27 had built: %r" % (outer_first,)

    bore_status2, _count2, bore_ok2, _names2 = bore_first["31_bore_L_lower"]
    outer_status2, _outer_message2 = bore_first["27_out_L_lower"]
    assert bore_status2 == "OK" and bore_ok2, "Step 31 was expected to build first: %r" % (bore_first,)
    assert outer_status2 == "FAIL", "Step 27 was expected to fail once Step 31 had built: %r" % (bore_first,)

    return outer_first, bore_first


def main():
    find_freecadcmd()
    design = _load("ring_fillet_design.json")
    steps = _load("ring_fillet_steps.json")
    fillet_1730_code = _load("ring_fillet_steps_17_30.json")["code"]

    outer_first, bore_first = check_step_27_and_31_exclude_each_other()
    print("Steps 27 and 31 exclude each other on the left-lower opening:")
    print("  outer first:", outer_first["27_out_L_lower"], outer_first["31_bore_L_lower"])
    print("  bore first:", bore_first["31_bore_L_lower"], bore_first["27_out_L_lower"])

    with Kernel() as kernel:
        plan_order = classify_steps_27_to_34(kernel, design, steps, fillet_1730_code, bore_first=False)
        print("Plan's own order (27-30 then 31-34):")
        for label, res in plan_order.items():
            print(" ", label, res)

    with Kernel() as kernel:
        reordered = classify_steps_27_to_34(kernel, design, steps, fillet_1730_code, bore_first=True)
        print("Reordered (31-34 then 27-30):")
        for label, res in reordered.items():
            print(" ", label, res)

    with Kernel() as kernel:
        with_body, with_tip = reproduce_event_16094(kernel, design, steps)
        print("Event 16094 reproduction:")
        print(" ", "Base=(body, ...):", "OK" if with_body.ok else "FAIL (%s)" % with_body.traceback.splitlines()[-1])
        print(" ", "Base=(tip captured pre-addObject, ...):", with_tip.stdout.strip() if with_tip.ok else "FAIL (%s)" % with_tip.traceback.splitlines()[-1])

    with Kernel() as kernel:
        boundary = demonstrate_step_35_mixed_concavity(kernel, design, steps, fillet_1730_code)
        print("Step 35 (18-edge front boundary loop, unsplit):",
              "MIXED CONCAVITY -- refused before recompute" if not boundary.ok else "built")
        if not boundary.ok:
            print(" ", boundary.traceback.splitlines()[-4])


if __name__ == "__main__":
    main()
