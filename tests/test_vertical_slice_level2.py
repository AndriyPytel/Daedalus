"""
Level 2 — the first vertical slice, end to end.

Primitive -> pad -> parameter change -> fillet via a Computed Reference, through
the real loop, the real Tool registry, the real apply pipeline and a real
Preflight Worker, against a real Document. Only the model is scripted, because
what is being checked here is the machinery, not the model's taste.

The load-bearing assertion is the last one: the fillet is still on the intended
edge after a parameter upstream in the history changes. That is the whole reason
ADR-0005 forbids a literal `Edge12`.
"""
import json

import pytest

from core.apply import ApplyPipeline
from core.events import (
    APPLIED, BLOCKED, PLAN, RENDER, REVIEW_VERDICT, STEP_STARTED, TOOL_CALL,
    TOOL_RESULT, EventStream,
)
from core.freecad_tools import build_registry
from core.llm import ScriptedLLMClient
from core.loop import APPROVE, Approval, run
from core.preflight import PreflightWorker, find_freecadcmd

pytestmark = pytest.mark.freecad

SQUARE = 10.0
PAD = 5.0
PAD_CHANGED = 12.0
RADIUS = 1.5

# ── the four Steps, as a model would write them ───────────────────────────────

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

# The point of the whole slice: the edges are worked out from the geometry at
# execution time. No element name is written down in advance.
STEP_FILLET = """
body = doc.getObject("Body")
# Capture the base feature first: Body.addObject moves Tip to the new feature,
# so reading body.Tip afterwards would make the Fillet reference itself.
base = body.Tip
solid = base.Shape
top = solid.BoundBox.ZMax
edges = [n + 1 for n, edge in enumerate(solid.Edges)
         if abs(edge.CenterOfMass.z - top) < 1e-6]

fillet = doc.addObject("PartDesign::Fillet", "Fillet")
body.addObject(fillet)
fillet.Base = (base, ["Edge%%d" %% n for n in edges])
fillet.Radius = %s
""" % RADIUS


def scripted_model():
    return ScriptedLLMClient([
        json.dumps({"steps": [
            "Sketch a %gmm square on the XY plane inside a new Body" % SQUARE,
            "Pad the sketch by %gmm" % PAD,
            "Change the pad length to %gmm" % PAD_CHANGED,
            "Fillet the top edges with a %gmm radius, finding them from the geometry" % RADIUS,
        ]}),
        json.dumps({"code": STEP_SKETCH, "label": "Sketch the square"}),
        json.dumps({"code": STEP_PAD, "label": "Pad the sketch"}),
        json.dumps({"code": STEP_PARAMETER, "label": "Change the pad length"}),
        json.dumps({"code": STEP_FILLET, "label": "Fillet the top edges"}),
        json.dumps({"verdict": "pass",
                    "reason": "A %gmm square padded to %gmm with the top edges filleted."
                              % (SQUARE, PAD_CHANGED)}),
    ])


# ── the harness: everything real except the model ─────────────────────────────


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with PreflightWorker(timeout=120) as running:
        yield running


@pytest.fixture
def doc():
    import FreeCAD

    document = FreeCAD.newDocument("VerticalSlice")
    document.UndoMode = 1
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


@pytest.fixture
def slice_run(doc, worker, tmp_path):
    """Run the whole slice once and hand back everything to assert on."""
    pipeline = ApplyPipeline(document=doc, worker=worker,
                             dispatch=lambda func, timeout=None: func(),
                             render_dir=str(tmp_path),
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

    return {"result": result, "events": events, "doc": doc,
            "pipeline": pipeline, "gate": gate}


# ── the loop runs against a real Document ─────────────────────────────────────


def test_the_full_loop_runs_end_to_end(slice_run):
    result = slice_run["result"]

    assert result.ok, result.reason
    assert result.steps_applied == 4
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

    hardcoded = STEP_FILLET.replace('["Edge%d" % n for n in edges]', "['Edge12']")
    assert hardcoded != STEP_FILLET, "the substitution did not take"
    assert not lint(hardcoded).ok
    assert "literal element name" in lint(hardcoded).report()


def test_every_object_the_run_created_is_owned(slice_run):
    owned = slice_run["pipeline"].owned

    assert {"Sketch", "Pad", "Fillet", "Body"} <= owned


# ── the assertion the whole design is for ─────────────────────────────────────


def _top_flat_area(shape):
    top = shape.BoundBox.ZMax
    return sum(face.Area for face in shape.Faces
               if abs(face.CenterOfMass.z - top) < 1e-6)


def test_the_fillet_stays_on_the_intended_edge_after_an_upstream_change(slice_run):
    """
    Change the pad length — upstream of the fillet in the history — and the
    fillet must still be on the top edges. A hardcoded `Edge12` would not
    crash here; it would silently move, which is why ADR-0005 exists.
    """
    doc = slice_run["doc"]
    fillet = doc.getObject("Fillet")
    before = _top_flat_area(fillet.Shape)

    doc.getObject("Pad").Length = 25
    doc.recompute()

    assert fillet.State == ["Up-to-date"], "the fillet broke on an upstream change"
    assert fillet.Shape.BoundBox.ZMax == pytest.approx(25.0)
    # Same square section, so the same filleted top: still all four top edges.
    assert _top_flat_area(fillet.Shape) == pytest.approx(before)
    assert _top_flat_area(fillet.Shape) == pytest.approx((SQUARE - 2 * RADIUS) ** 2)


def test_the_fillet_survives_a_change_further_upstream_still(slice_run):
    """The sketch dimension, two features upstream of the fillet."""
    doc = slice_run["doc"]
    sketch, fillet = doc.getObject("Sketch"), doc.getObject("Fillet")
    distance_x = [n for n, c in enumerate(sketch.Constraints) if c.Type == "DistanceX"][0]

    sketch.setDatum(distance_x, 20.0)
    doc.recompute()

    assert fillet.State == ["Up-to-date"]
    # A 20 x 10 top, still filleted on all four of its edges.
    assert _top_flat_area(fillet.Shape) == pytest.approx((20 - 2 * RADIUS) * (SQUARE - 2 * RADIUS))


# ── undo ──────────────────────────────────────────────────────────────────────


def test_one_undo_takes_back_exactly_one_step(slice_run):
    doc = slice_run["doc"]
    assert doc.getObject("Fillet") is not None

    doc.undo()

    assert doc.getObject("Fillet") is None, "the fillet Step did not come back off"
    assert doc.getObject("Pad") is not None, "undo took more than one Step"
    assert doc.getObject("Pad").Length.Value == PAD_CHANGED


def test_each_step_of_the_run_undoes_in_turn(slice_run):
    doc = slice_run["doc"]

    assert doc.UndoNames == ["Fillet the top edges", "Change the pad length",
                             "Pad the sketch", "Sketch the square"]

    doc.undo()                                        # un-fillet
    assert doc.getObject("Fillet") is None
    doc.undo()                                        # un-change the parameter
    assert doc.getObject("Pad").Length.Value == PAD
    doc.undo()                                        # un-pad
    assert doc.getObject("Pad") is None
    doc.undo()                                        # un-sketch
    assert doc.getObject("Sketch") is None


# ── every event is there for the panel to show ────────────────────────────────


def test_the_panel_had_everything_it_needed_to_show_the_run(slice_run):
    events = slice_run["events"]
    kinds = [e.kind for e in events]

    assert kinds[0] == PLAN
    assert len(events.of(STEP_STARTED)) == 4
    assert len(events.of(TOOL_CALL)) == 4
    assert len(events.of(TOOL_RESULT)) == 4
    assert len(events.of(APPLIED)) == 4
    assert events.last(REVIEW_VERDICT).data["verdict"] == "pass"
    assert events.first(BLOCKED) is None


def test_every_step_that_produced_geometry_produced_a_render(slice_run):
    """
    Three renders, not four: Step 1 leaves only a sketch, and a wire has no
    facets to rasterise. Review judges the finished model, and the Step that
    finished it did render.
    """
    rendered = [e.data["step"] for e in slice_run["events"].of(RENDER)]

    assert rendered == [2, 3, 4]
    assert slice_run["result"].render is not None


def test_each_step_is_narrated_in_order(slice_run):
    started = slice_run["events"].of(STEP_STARTED)

    assert [e.data["index"] for e in started] == [1, 2, 3, 4]
    assert all(e.data["total"] == 4 for e in started)
    assert "Fillet the top edges" in started[3].data["description"]


def test_a_render_review_could_judge_by_exists_for_every_step(slice_run):
    import os

    for event in slice_run["events"].of(RENDER):
        assert os.path.getsize(event.data["path"]) > 1000


def test_the_run_reads_back_from_its_log(slice_run, tmp_path):
    path = str(tmp_path / "session.jsonl")
    slice_run["events"].log_to(path)

    replayed = [json.loads(line) for line in open(path).read().strip().splitlines()]

    assert [e["kind"] for e in replayed] == [e.kind for e in slice_run["events"]]
    assert replayed[0]["data"]["steps"][0].startswith("Sketch a 10mm square")
