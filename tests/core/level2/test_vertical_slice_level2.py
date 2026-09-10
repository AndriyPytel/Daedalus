"""
Level 2 — the first vertical slice, end to end.

Primitive -> pad -> parameter change -> fillet via a Computed Reference -> a
through hole, whose diameter is the thing no view of the result answers, through
the real loop, the real Tool registry, the real apply pipeline and a real
Preflight Worker, against a real Document. Only the model is scripted, because
what is being checked here is the machinery, not the model's taste.

The load-bearing assertion is the last one: the fillet is still on the intended
edge after a parameter upstream in the history changes. That is the whole reason
ADR-0005 forbids a literal `Edge12`.

STEP_FILLET is the point of the whole slice: the edges are worked out from the
geometry at execution time, and no element name is written down in advance.
STEP_HOLE drills a hole because a hole is the feature the Description exists
for: it is a dimension and not a pixel, and no view of the result answers "is
that one Ø4".
"""
import json

import pytest

from core.freecad_apply import ApplyPipeline
from core.events import (
    APPLIED, BLOCKED, CALL_STARTED, COST, INTENT, PLAN, REVIEW_VERDICT,
    STEP_STARTED, THOUGHT, TOOL_CALL, TOOL_RESULT, WRITING, EventStream,
)
from core.freecad_apply import build_registry
from core.llm import ScriptedLLMClient
from core.loop.gate import APPROVE, Approval
from core.loop.run import run
from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

SQUARE = 10.0
PAD = 5.0
PAD_CHANGED = 12.0
RADIUS = 1.5
BORE = 4.0
TOP_EDGES = 4


STEP_SKETCH = """
import Part, Sketcher

body = doc.addObject("PartDesign::Body", "Body")
sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
body.addObject(sketch)
# Attachment goes through AttachmentSupport + MapMode, not Placement.
sketch.AttachmentSupport = [(doc.getObject("XY_Plane"), '')]
sketch.MapMode = "FlatFace"

side = %(side)s
corners = [App.Vector(0, 0, 0), App.Vector(side, 0, 0),
           App.Vector(side, side, 0), App.Vector(0, side, 0)]
for a, b in [(0, 1), (1, 2), (2, 3), (3, 0)]:
    sketch.addGeometry(Part.LineSegment(corners[a], corners[b]), False)
for i in range(4):
    sketch.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i + 1) %% 4, 1))
sketch.addConstraint(Sketcher.Constraint('Horizontal', 0))
sketch.addConstraint(Sketcher.Constraint('Horizontal', 2))
sketch.addConstraint(Sketcher.Constraint('Vertical', 1))
sketch.addConstraint(Sketcher.Constraint('Vertical', 3))
sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, side))
sketch.addConstraint(Sketcher.Constraint('DistanceY', 1, 1, 1, 2, side))
""" % {"side": SQUARE}

STEP_PAD = """
body = doc.getObject("Body")
pad = doc.addObject("PartDesign::Pad", "Pad")
body.addObject(pad)
pad.Profile = doc.getObject("Sketch")
pad.Length = %s
""" % PAD

STEP_PARAMETER = """
doc.getObject("Pad").Length = %s
""" % PAD_CHANGED

STEP_FILLET = """
body = doc.getObject("Body")
# Capture the base feature first: Body.addObject moves Tip to the new feature,
# so reading body.Tip afterwards would make the Fillet reference itself.
base = body.Tip
solid = base.Shape
top = solid.BoundBox.ZMax
edges = ["Edge%%d" %% (n + 1) for n, edge in enumerate(solid.Edges)
         if abs(edge.CenterOfMass.z - top) < 1e-6]
# The Step says four, so four is what the selection has to come to.
if len(edges) != %(count)d:
    raise RuntimeError("the top face has %(count)d edges, found %%d" %% len(edges))

fillet = doc.addObject("PartDesign::Fillet", "Fillet")
body.addObject(fillet)
fillet.Base = (base, edges)
fillet.Radius = %(radius)s
""" % {"count": TOP_EDGES, "radius": RADIUS}

STEP_HOLE = """
import Part

body = doc.getObject("Body")
profile = doc.addObject("Sketcher::SketchObject", "HoleSketch")
body.addObject(profile)
profile.AttachmentSupport = [(doc.getObject("XY_Plane"), '')]
profile.MapMode = "FlatFace"
side = %(side)s
profile.addGeometry(Part.Circle(App.Vector(side / 2, side / 2, 0),
                                App.Vector(0, 0, 1), %(bore)s / 2), False)

hole = doc.addObject("PartDesign::Hole", "Hole")
body.addObject(hole)
hole.Profile = profile
hole.Diameter = %(bore)s
hole.DepthType = "ThroughAll"
# The profile is on XY and the material is above it, so the cut goes up.
hole.Reversed = True
""" % {"side": SQUARE, "bore": BORE}


def scripted_model():
    """
    Design, which has no drawing to read and describes nothing, then the Plan,
    then the whole of it written in one Execute turn, then the verdict. Each
    Step still lands on its own, through its own apply.
    """
    steps = [
        (STEP_SKETCH, "Sketch the square"),
        (STEP_PAD, "Pad the sketch"),
        (STEP_PARAMETER, "Change the pad length"),
        (STEP_FILLET, "Fillet the top edges"),
        (STEP_HOLE, "Drill the hole"),
    ]
    return ScriptedLLMClient([
        json.dumps({"declare": {"design": {}, "question": ""}}),
        json.dumps({"declare": {"steps": [
            "Sketch a %gmm square on the XY plane inside a new Body" % SQUARE,
            "Pad the sketch by %gmm" % PAD,
            "Change the pad length to %gmm" % PAD_CHANGED,
            "Fillet the %d top edges with a %gmm radius, finding them from the geometry"
            % (TOP_EDGES, RADIUS),
            "Drill a %gmm hole through it on the centreline" % BORE,
        ]}}),
        *[json.dumps({"code": code, "step": number, "label": label})
          for number, (code, label) in enumerate(steps, start=1)],
        json.dumps({"declare": {"steps": [
            {"step": number, "label": label} for number, (code, label) in enumerate(steps, start=1)
        ]}}),
        json.dumps({"declare": {"suspected": []}}),
        json.dumps({"declare": {"verdict": "pass",
                    "reason": "A %gmm square padded to %gmm, top edges filleted, "
                              "drilled Ø%g." % (SQUARE, PAD_CHANGED, BORE)}}),
    ])


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=120) as running:
        yield running


def _drive_the_slice(worker, render_dir):
    """
    Run the whole slice once against a document of its own and hand back
    everything to assert on. The document is closed when the caller is done.
    """
    import FreeCAD

    doc = FreeCAD.newDocument("VerticalSlice")
    doc.UndoMode = 1
    try:
        pipeline = ApplyPipeline(document=doc, worker=worker,
                                 dispatch=lambda func, timeout=None: func(),
                                 render_dir=render_dir,
                                 confirm=lambda kinds: False)
        events = EventStream()
        gate = {"asked": [], "objects_at_the_gate": None}

        def plan_gate(steps):
            gate["asked"].append(list(steps))
            gate["objects_at_the_gate"] = [o.Name for o in doc.Objects]
            return Approval(APPROVE)

        result = run("Make a %gmm square padded to %gmm with filleted top edges"
                     % (SQUARE, PAD_CHANGED),
                     llm=scripted_model(), tools=build_registry(doc, pipeline=pipeline),
                     events=events, gate=plan_gate)

        yield {"result": result, "events": events, "doc": doc,
               "pipeline": pipeline, "gate": gate}
    finally:
        FreeCAD.closeDocument(doc.Name)


@pytest.fixture(scope="module")
def slice_run(worker, tmp_path_factory):
    """
    The one run every read-only assertion in this module shares.

    Driving it per test cost the suite 132s of its 220s: the run itself is
    0.9s, but the thirteenth in a row hung the Preflight Worker until the
    120s timeout took it. A test that changes the document asks for
    own_slice_run instead.
    """
    yield from _drive_the_slice(worker, str(tmp_path_factory.mktemp("slice")))


@pytest.fixture
def own_slice_run(worker, tmp_path):
    """A run of its own, for a test that changes the document it asserts on."""
    yield from _drive_the_slice(worker, str(tmp_path))


def test_the_full_loop_runs_end_to_end(slice_run):
    result = slice_run["result"]

    assert result.ok, result.reason
    assert result.steps_applied == 5
    assert result.verdict == "pass"


def test_nothing_was_applied_before_the_plan_gate(slice_run):
    assert slice_run["gate"]["objects_at_the_gate"] == []
    assert len(slice_run["gate"]["asked"]) == 1, "the user was asked more than once"


def test_primitive_pad_parameter_change_and_fillet_all_landed(slice_run):
    doc = slice_run["doc"]

    assert [o.Name for o in doc.Objects if o.Name in ("Sketch", "Pad", "Fillet")] \
        == ["Sketch", "Pad", "Fillet"]
    assert doc.getObject("Pad").Length.Value == PAD_CHANGED
    assert doc.getObject("Fillet").Radius.Value == RADIUS
    assert doc.getObject("Fillet").State == ["Up-to-date"]


def test_the_fillet_was_placed_by_a_computed_reference(slice_run):
    """
    The Step never wrote an element name down; it worked them out from the
    solid. The names FreeCAD ended up storing are a consequence, not an input.
    """
    fillet = slice_run["doc"].getObject("Fillet")
    referenced = list(fillet.Base[1])

    assert len(referenced) == 4, "the four top edges were not all found: %s" % referenced
    assert all(name.startswith("Edge") for name in referenced)
    assert fillet.Base[0].Name == "Pad", "the Fillet references itself instead of the Pad"


def test_the_generated_code_would_have_been_rejected_if_it_hardcoded_an_edge():
    """The lint that makes the Computed Reference the only option (ADR-0005)."""
    from core.lint import lint

    assert lint(STEP_FILLET).ok, lint(STEP_FILLET).report()

    hardcoded = STEP_FILLET.replace("(base, edges)", "(base, ['Edge12'])")
    assert hardcoded != STEP_FILLET, "the substitution did not take"
    assert not lint(hardcoded).ok
    assert "literal element name" in lint(hardcoded).report()


def test_every_object_the_run_created_is_owned(slice_run):
    owned = slice_run["pipeline"].owned

    assert {"Sketch", "Pad", "Fillet", "Body"} <= owned


def _top_flat_area(shape):
    top = shape.BoundBox.ZMax
    return sum(face.Area for face in shape.Faces
               if abs(face.CenterOfMass.z - top) < 1e-6)


def test_the_fillet_stays_on_the_intended_edge_after_an_upstream_change(own_slice_run):
    """
    Change the pad length — upstream of the fillet in the history — and the
    fillet must still be on the top edges. A hardcoded `Edge12` would not
    crash here; it would silently move, which is why ADR-0005 exists.

    The section stays square, so the filleted top stays the same area on all
    four of its edges.
    """
    doc = own_slice_run["doc"]
    fillet = doc.getObject("Fillet")
    before = _top_flat_area(fillet.Shape)

    doc.getObject("Pad").Length = 25
    doc.recompute()

    assert fillet.State == ["Up-to-date"], "the fillet broke on an upstream change"
    assert fillet.Shape.BoundBox.ZMax == pytest.approx(25.0)
    assert _top_flat_area(fillet.Shape) == pytest.approx(before)
    assert _top_flat_area(fillet.Shape) == pytest.approx((SQUARE - 2 * RADIUS) ** 2)


def test_the_fillet_survives_a_change_further_upstream_still(own_slice_run):
    """
    The sketch dimension, two features upstream of the fillet. The top is 20 by
    10 afterwards, still filleted on all four of its edges.
    """
    doc = own_slice_run["doc"]
    sketch, fillet = doc.getObject("Sketch"), doc.getObject("Fillet")
    distance_x = [n for n, c in enumerate(sketch.Constraints) if c.Type == "DistanceX"][0]

    sketch.setDatum(distance_x, 20.0)
    doc.recompute()

    assert fillet.State == ["Up-to-date"]
    assert _top_flat_area(fillet.Shape) == pytest.approx((20 - 2 * RADIUS) * (SQUARE - 2 * RADIUS))


def test_one_undo_takes_back_exactly_one_step(own_slice_run):
    doc = own_slice_run["doc"]
    assert doc.getObject("Hole") is not None

    doc.undo()

    assert doc.getObject("Hole") is None, "the hole Step did not come back off"
    assert doc.getObject("Fillet") is not None, "undo took more than one Step"
    assert doc.getObject("Pad").Length.Value == PAD_CHANGED


def test_each_step_of_the_run_undoes_in_turn(own_slice_run):
    """Five undos take the run apart, one Step at a time, in reverse order."""
    doc = own_slice_run["doc"]

    assert doc.UndoNames == ["Drill the hole", "Fillet the top edges",
                             "Change the pad length", "Pad the sketch",
                             "Sketch the square"]

    doc.undo()
    assert doc.getObject("Hole") is None
    doc.undo()
    assert doc.getObject("Fillet") is None
    doc.undo()
    assert doc.getObject("Pad").Length.Value == PAD
    doc.undo()
    assert doc.getObject("Pad") is None
    doc.undo()
    assert doc.getObject("Sketch") is None


def test_the_panel_had_everything_it_needed_to_show_the_run(slice_run):
    """
    Every event the panel draws the run from. The twelve Tool calls are the
    five kernel checks a code turn makes of its own Step (FCC-A-53), the five
    applies, the self-intersection check (FCC-256, unconditional) and the one
    review_views the loop drives at the end.

    CALL_STARTED, COST, THOUGHT and WRITING are per-call bookkeeping and say nothing
    about the order of the run, so they are filtered out before checking the
    structural event sequence.
    """
    events = slice_run["events"]
    kinds = [e.kind for e in events
             if e.kind not in (CALL_STARTED, COST, THOUGHT, WRITING)]

    assert kinds[:2] == [INTENT, PLAN]
    assert len(events.of(STEP_STARTED)) == 5
    assert len(events.of(TOOL_CALL)) == 12
    assert len(events.of(TOOL_RESULT)) == 12
    assert len(events.of(APPLIED)) == 5
    assert events.last(REVIEW_VERDICT).data["verdict"] == "pass"
    assert events.first(BLOCKED) is None


def test_no_step_rendered_and_review_still_got_a_picture(slice_run):
    """
    No Step produces a picture at all any more (FCC-81): there is one render
    in a run and it is the one Review judges by — the Worker's rasteriser
    here, because this run is headless.
    """
    assert [e for e in slice_run["events"] if e.kind == "render"] == []
    assert all("render" not in e.data["result"]
               for e in slice_run["events"].of(TOOL_RESULT)
               if e.data["name"] == "apply_step")

    views = [e for e in slice_run["events"].of(TOOL_RESULT)
             if e.data["name"] == "review_views"]
    assert len(views) == 1, "the loop did not ask for Review's picture"
    assert len(views[0].data["result"]["views"]) == 1, "headless is one isometry"


def test_the_description_review_judged_by_is_in_the_session_log(slice_run):
    """
    A whole run leaves behind what Review was actually given, and it is a
    description of the part rather than three lines per object: the Pad's
    length in mm, and the hole read off the Shape with a diameter and an axis
    that no Acceptance list had to ask for. FCC-81.
    """
    applied = [e for e in slice_run["events"].of(TOOL_RESULT)
               if e.data["name"] == "apply_step"]
    described = applied[-1].data["result"]["description"]
    features = {f["name"]: f for f in described["features"]}

    assert features["Pad"]["parameters"]["Length"] == "%.1f mm" % PAD_CHANGED
    assert features["Fillet"]["parameters"]["Radius"] == "%.1f mm" % RADIUS
    rounded = [entry for entry in described["geometry"]["surfaces"]
               if entry["kind"] == "Cylinder"]
    assert rounded == [{"kind": "Cylinder", "diameter_mm": RADIUS * 2,
                        "axis": [0.0, 1.0, 0.0], "count": 2},
                       {"kind": "Cylinder", "diameter_mm": RADIUS * 2,
                        "axis": [1.0, 0.0, 0.0], "count": 2},
                       {"kind": "Cylinder", "diameter_mm": BORE,
                        "axis": [0.0, 0.0, 1.0], "count": 1}]


def test_each_step_is_narrated_in_order(slice_run):
    started = slice_run["events"].of(STEP_STARTED)

    assert [e.data["index"] for e in started] == [1, 2, 3, 4, 5]
    assert all(e.data["total"] == 5 for e in started)
    assert "Fillet the %d top edges" % TOP_EDGES in started[3].data["description"]


def test_the_run_reads_back_from_its_log(slice_run, tmp_path):
    path = str(tmp_path / "session.jsonl")
    slice_run["events"].log_to(path)

    replayed = [json.loads(line) for line in open(path).read().strip().splitlines()]

    assert [e["kind"] for e in replayed] == [e.kind for e in slice_run["events"]]
    planned = next(entry for entry in replayed if entry["kind"] == PLAN)
    assert planned["data"]["steps"][0].startswith("Sketch a 10mm square")
